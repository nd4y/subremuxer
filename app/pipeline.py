"""The request pipeline: fetch upstream → parse → filter → re-emit → log."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .aggregates import Aggregate
from .filtering import CompiledFilter, Decision, apply_filter
from .formats import (
    FORMAT_LABELS,
    ParsedSubscription,
    SubFormat,
    UnknownFormatError,
    detect_and_parse,
)
from .formats.merge import MergePart, merge
from .formats.uri_list import UriListSubscription
from .logs import RequestLogEntry
from .profiles import Profile
from .upstream import (
    UpstreamError,
    UpstreamFetcher,
    UpstreamRequest,
    UpstreamResult,
    passthrough_response_headers,
    plan_hwid,
)


@dataclass(slots=True)
class Defaults:
    """Instance-wide fallbacks, editable in the admin UI."""

    hwid: str | None = None
    device_os: str | None = None
    device_ver: str | None = None
    device_model: str | None = None

    @classmethod
    def from_settings(cls, data: dict[str, Any]) -> Defaults:
        return cls(
            hwid=(data.get("default_hwid") or None),
            device_os=(data.get("default_device_os") or None),
            device_ver=(data.get("default_device_ver") or None),
            device_model=(data.get("default_device_model") or None),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "default_hwid": self.hwid or "",
            "default_device_os": self.device_os or "",
            "default_device_ver": self.device_ver or "",
            "default_device_model": self.device_model or "",
        }


@dataclass(slots=True)
class ProxyResult:
    body: str
    content_type: str
    headers: dict[str, str]
    status_code: int
    entry: RequestLogEntry
    decisions: list[Decision] = field(default_factory=list)
    passthrough: bool = False


class UpstreamCache:
    """Tiny TTL cache so a client that polls every minute does not hammer the panel.

    Keyed by the exact upstream request, HWID included: two devices with different
    HWIDs must never share a cached body, or the panel's device accounting breaks.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[Any, ...], tuple[float, UpstreamResult]] = {}

    def get(self, key: tuple[Any, ...], ttl: int) -> UpstreamResult | None:
        if ttl <= 0:
            return None
        hit = self._store.get(key)
        if hit is None:
            return None
        stored_at, result = hit
        if time.monotonic() - stored_at > ttl:
            self._store.pop(key, None)
            return None
        return result

    def put(self, key: tuple[Any, ...], result: UpstreamResult, ttl: int) -> None:
        if ttl <= 0:
            return
        if len(self._store) > 512:
            self._store.clear()
        self._store[key] = (time.monotonic(), result)

    def invalidate_profile(self, profile_id: int) -> None:
        for key in [k for k in self._store if k and k[0] == profile_id]:
            self._store.pop(key, None)


def build_request(
    profile: Profile, defaults: Defaults, client_headers: dict[str, str]
) -> UpstreamRequest:
    lowered = {k.lower(): v for k, v in client_headers.items()}
    plan = plan_hwid(profile.hwid_mode, profile.hwid or defaults.hwid, lowered.get("x-hwid"))
    return UpstreamRequest(
        url=profile.upstream_url,
        client_headers=client_headers,
        hwid_plan=plan,
        device_os=profile.device_os or defaults.device_os,
        device_ver=profile.device_ver or defaults.device_ver,
        device_model=profile.device_model or defaults.device_model,
        user_agent_override=profile.upstream_ua,
    )


def _apply_encoding_override(parsed: ParsedSubscription, output_format: str) -> None:
    """Base64 and plain text are the same document in different envelopes."""
    if not isinstance(parsed, UriListSubscription):
        return
    if output_format == "base64":
        parsed.set_base64(True)
    elif output_format == "plain":
        parsed.set_base64(False)


@dataclass(slots=True)
class SourceResult:
    """One profile fetched, parsed and filtered — the step both paths share.

    ``parsed`` is None when nothing filterable came back. The raw fields then
    hold what should be handed to the client instead: an upstream error, the
    panel's own 4xx page, or a body in a format this app does not know.
    """

    entry: RequestLogEntry
    headers: dict[str, str] = field(default_factory=dict)
    parsed: ParsedSubscription | None = None
    keep: list[int] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    raw_body: str = ""
    raw_content_type: str = "text/plain; charset=utf-8"
    raw_status: int = 502


async def fetch_and_filter(
    *,
    profile: Profile,
    defaults: Defaults,
    fetcher: UpstreamFetcher,
    cache: UpstreamCache,
    client_headers: dict[str, str],
    client_ip: str | None,
    request_path: str | None,
) -> SourceResult:
    """Fetch one profile's upstream and apply its filter. Never raises."""
    lowered = {k.lower(): v for k, v in client_headers.items()}
    request = build_request(profile, defaults, client_headers)

    entry = RequestLogEntry(
        profile_id=profile.id,
        profile_name=profile.name,
        client_ip=client_ip,
        user_agent=lowered.get("user-agent"),
        request_path=request_path,
        hwid_in=request.hwid_plan.hwid_in,
        hwid_sent=request.hwid_plan.hwid_sent,
        hwid_action=request.hwid_plan.action,
        upstream_url=profile.upstream_url,
    )

    cache_key = (
        profile.id,
        request.hwid_plan.hwid_sent,
        request.build_headers().get("user-agent"),
        lowered.get("accept"),
    )
    result = cache.get(cache_key, profile.cache_ttl)
    cached = result is not None
    if result is None:
        try:
            result = await fetcher.fetch(request)
        except UpstreamError as exc:
            entry.error = str(exc)
            entry.status_code = 502
            return SourceResult(entry=entry, raw_body=str(exc), raw_status=502)
        cache.put(cache_key, result, profile.cache_ttl)

    entry.upstream_status = result.status_code
    entry.upstream_ms = 0 if cached else result.elapsed_ms
    entry.bytes_in = result.bytes_in
    headers = passthrough_response_headers(result.headers)
    content_type = result.content_type or "text/plain; charset=utf-8"

    if result.status_code >= 400:
        entry.status_code = result.status_code
        entry.error = f"апстрим ответил {result.status_code}"
        entry.bytes_out = len(result.text.encode("utf-8"))
        return SourceResult(
            entry=entry,
            headers=headers,
            raw_body=result.text,
            raw_content_type=content_type,
            raw_status=result.status_code,
        )

    try:
        parsed = detect_and_parse(result.text)
    except UnknownFormatError as exc:
        # Most often this is the panel's HTML landing page for browsers. Passing it
        # through untouched is strictly better than guessing.
        entry.detected_format = "passthrough"
        entry.output_format = "passthrough"
        entry.error = f"формат не распознан, отдан без изменений: {exc}"
        entry.status_code = result.status_code
        entry.bytes_out = len(result.text.encode("utf-8"))
        return SourceResult(
            entry=entry,
            headers=headers,
            raw_body=result.text,
            raw_content_type=content_type,
            raw_status=result.status_code,
        )

    decisions = apply_filter(parsed.nodes, profile.compiled_filter())
    keep = [decision.node.index for decision in decisions if decision.kept]

    entry.detected_format = parsed.format.value
    entry.nodes_total = len(decisions)
    entry.nodes_kept = len(keep)
    entry.status_code = 200

    return SourceResult(
        entry=entry, headers=headers, parsed=parsed, keep=keep, decisions=decisions
    )


async def proxy_subscription(
    *,
    profile: Profile,
    defaults: Defaults,
    fetcher: UpstreamFetcher,
    cache: UpstreamCache,
    client_headers: dict[str, str],
    client_ip: str | None,
    request_path: str | None,
) -> ProxyResult:
    source = await fetch_and_filter(
        profile=profile,
        defaults=defaults,
        fetcher=fetcher,
        cache=cache,
        client_headers=client_headers,
        client_ip=client_ip,
        request_path=request_path,
    )
    entry = source.entry
    if source.parsed is None:
        return ProxyResult(
            body=source.raw_body,
            content_type=source.raw_content_type,
            headers=source.headers,
            status_code=source.raw_status,
            entry=entry,
            passthrough=True,
        )

    parsed = source.parsed
    _apply_encoding_override(parsed, profile.output_format)
    body = parsed.render(source.keep)

    entry.output_format = _effective_output_format(parsed, profile.output_format).value
    entry.bytes_out = len(body.encode("utf-8"))

    return ProxyResult(
        body=body,
        content_type=parsed.content_type(),
        headers=source.headers,
        status_code=200,
        entry=entry,
        decisions=source.decisions,
    )


# ------------------------------------------------------------------ aggregates


@dataclass(slots=True)
class AggregateSourceRef:
    """A profile taking part in an aggregate, with the label its nodes get."""

    profile: Profile
    prefix: str = ""


def aggregate_refs(
    aggregate: Aggregate, profiles: Mapping[int, Profile]
) -> list[AggregateSourceRef]:
    """Resolve an aggregate's sources, skipping ones that are gone or switched off.

    A source with no prefix of its own borrows the profile's name, which is what
    makes «включить подписи» useful straight away without typing anything.
    """
    refs: list[AggregateSourceRef] = []
    for source in aggregate.sources:
        profile = profiles.get(source.profile_id)
        if profile is None or not profile.enabled:
            continue
        prefix = (source.prefix or profile.name) if aggregate.prefix_names else ""
        refs.append(AggregateSourceRef(profile=profile, prefix=prefix))
    return refs


@dataclass(slots=True)
class AggregateResult:
    """What to answer with, plus every source's own log entry."""

    proxy: ProxyResult
    sources: list[SourceResult] = field(default_factory=list)


async def proxy_aggregate(
    *,
    aggregate: Aggregate,
    refs: Sequence[AggregateSourceRef],
    defaults: Defaults,
    fetcher: UpstreamFetcher,
    cache: UpstreamCache,
    client_headers: dict[str, str],
    client_ip: str | None,
    request_path: str | None,
) -> AggregateResult:
    """Serve several profiles as one subscription.

    Sources are fetched concurrently — a client refreshing an aggregate of five
    panels should wait for the slowest one, not for all five in turn.
    """
    lowered = {k.lower(): v for k, v in client_headers.items()}
    entry = RequestLogEntry(
        profile_name=aggregate.name,
        client_ip=client_ip,
        user_agent=lowered.get("user-agent"),
        request_path=request_path,
        upstream_url=f"сборка из {len(refs)} источников",
    )

    def failure(message: str) -> AggregateResult:
        entry.error = message
        entry.status_code = 502
        entry.bytes_out = len(message.encode("utf-8"))
        return AggregateResult(
            proxy=ProxyResult(
                body=message,
                content_type="text/plain; charset=utf-8",
                headers={},
                status_code=502,
                entry=entry,
                passthrough=True,
            )
        )

    if not refs:
        return failure("в сборке нет включённых источников")

    started = time.monotonic()
    results = list(
        await asyncio.gather(
            *(
                fetch_and_filter(
                    profile=ref.profile,
                    defaults=defaults,
                    fetcher=fetcher,
                    cache=cache,
                    client_headers=client_headers,
                    client_ip=client_ip,
                    request_path=request_path,
                )
                for ref in refs
            )
        )
    )

    entry.bytes_in = sum(item.entry.bytes_in for item in results)
    entry.upstream_ms = int((time.monotonic() - started) * 1000)

    parts: list[MergePart] = []
    problems: list[str] = []
    for ref, source in zip(refs, results, strict=True):
        if source.parsed is None:
            problems.append(f"«{ref.profile.name}»: {source.entry.error or 'нет данных'}")
            continue
        parts.append(
            MergePart(
                parsed=source.parsed,
                keep=source.keep,
                prefix=ref.prefix,
                label=ref.profile.name,
            )
        )

    if not parts:
        result = failure("ни один источник сборки не ответил: " + "; ".join(problems))
        result.sources = results
        return result

    merged = merge(parts, dedupe=aggregate.dedupe, output_format=aggregate.output_format)
    problems.extend(merged.skipped)

    entry.upstream_status = 200
    entry.detected_format = parts[0].parsed.format.value
    entry.output_format = merged.format.value
    entry.nodes_total = sum(item.entry.nodes_total for item in results)
    entry.nodes_kept = merged.kept
    entry.bytes_out = len(merged.body.encode("utf-8"))
    entry.status_code = 200
    entry.error = "; ".join(problems) or None

    return AggregateResult(
        proxy=ProxyResult(
            body=merged.body,
            content_type=merged.content_type,
            headers={},
            status_code=200,
            entry=entry,
        ),
        sources=results,
    )


def _effective_output_format(parsed: ParsedSubscription, output_format: str) -> SubFormat:
    if isinstance(parsed, UriListSubscription):
        if output_format == "base64":
            return SubFormat.BASE64
        if output_format == "plain":
            return SubFormat.URI_LIST
    return parsed.format


@dataclass(slots=True)
class PreviewResult:
    detected_format: str | None
    format_label: str | None
    upstream_status: int
    upstream_ms: int
    hwid_sent: str | None
    hwid_action: str
    regex: str
    nodes: list[dict[str, Any]]
    total: int
    kept: int
    error: str | None = None
    body_preview: str | None = None


async def preview_filter(
    *,
    profile: Profile,
    defaults: Defaults,
    fetcher: UpstreamFetcher,
    compiled: CompiledFilter,
    client_headers: dict[str, str] | None = None,
) -> PreviewResult:
    """Run the whole pipeline for the admin's *Test* button, without serving anything."""
    request = build_request(profile, defaults, client_headers or {})

    def preview(**overrides: Any) -> PreviewResult:
        values: dict[str, Any] = {
            "detected_format": None,
            "format_label": None,
            "upstream_status": 0,
            "upstream_ms": 0,
            "hwid_sent": request.hwid_plan.hwid_sent,
            "hwid_action": request.hwid_plan.action,
            "regex": compiled.builder_regex,
            "nodes": [],
            "total": 0,
            "kept": 0,
            **overrides,
        }
        return PreviewResult(**values)

    try:
        result = await fetcher.fetch(request)
    except UpstreamError as exc:
        return preview(error=str(exc))

    if result.status_code >= 400:
        return preview(
            upstream_status=result.status_code,
            upstream_ms=result.elapsed_ms,
            error=f"апстрим ответил {result.status_code}",
            body_preview=result.text[:2000],
        )

    try:
        parsed = detect_and_parse(result.text)
    except UnknownFormatError as exc:
        return preview(
            upstream_status=result.status_code,
            upstream_ms=result.elapsed_ms,
            error=f"формат не распознан: {exc}",
            body_preview=result.text[:2000],
        )

    decisions = apply_filter(parsed.nodes, compiled)
    return preview(
        detected_format=parsed.format.value,
        format_label=FORMAT_LABELS.get(parsed.format),
        upstream_status=result.status_code,
        upstream_ms=result.elapsed_ms,
        nodes=[decision.as_dict() for decision in decisions],
        total=len(decisions),
        kept=sum(1 for decision in decisions if decision.kept),
    )
