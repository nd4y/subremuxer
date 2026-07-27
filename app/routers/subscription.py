"""The public endpoint clients point their subscription URL at.

One path serves both kinds of link. A token belongs either to a profile or to an
aggregate, never to both, so the client never has to know which it was handed.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..aggregates import AggregateRepository
from ..logs import LogRepository
from ..pipeline import Defaults, aggregate_refs, proxy_aggregate, proxy_subscription
from ..profiles import OUTPUT_FORMATS, ProfileRepository
from ..security import client_ip

router = APIRouter()

def _not_found() -> Response:
    return Response(status_code=404, content="Not Found", media_type="text/plain")


def _format_override(request: Request) -> str | None:
    """A per-request format override is handy when debugging a client by hand."""
    override = request.query_params.get("format")
    return override if override in OUTPUT_FORMATS and override != "auto" else None


async def _serve(request: Request, token: str) -> Response:
    app = request.app
    profiles = ProfileRepository(app.state.db)
    logs = LogRepository(app.state.db)
    settings = app.state.settings

    common = {
        "defaults": Defaults.from_settings(app.state.db.all_settings()),
        "fetcher": app.state.fetcher,
        "cache": app.state.cache,
        "client_headers": dict(request.headers),
        "client_ip": client_ip(request, settings),
        "request_path": str(request.url.path),
    }

    profile = profiles.get_by_token(token)
    if profile is not None:
        if not profile.enabled:
            # Same answer for "no such token" and "disabled", so the endpoint
            # cannot be used to enumerate which tokens exist.
            return _not_found()
        override = _format_override(request)
        if override:
            profile.output_format = override
        result = await proxy_subscription(profile=profile, **common)
        logs.record(result.entry, result.decisions)
        return _respond(result.body, result.status_code, result.content_type, result.headers)

    aggregate = AggregateRepository(app.state.db).get_by_token(token)
    if aggregate is None or not aggregate.enabled:
        return _not_found()

    override = _format_override(request)
    if override:
        aggregate.output_format = override
    merged = await proxy_aggregate(
        aggregate=aggregate,
        refs=aggregate_refs(aggregate, {item.id: item for item in profiles.list()}),
        **common,
    )
    # Every source is logged in its own right — that is where the HWID actually
    # sent and the per-node verdicts live — and the aggregate gets one summary
    # row on top, so the list reads top-down as "the link, then its parts".
    for source in merged.sources:
        logs.record(source.entry, source.decisions)
    logs.record(merged.proxy.entry)
    result = merged.proxy
    return _respond(result.body, result.status_code, result.content_type, result.headers)


def _respond(body: str, status: int, content_type: str, headers: dict[str, str]) -> Response:
    merged = dict(headers)
    merged.setdefault("cache-control", "no-store")
    return Response(content=body, status_code=status, media_type=content_type, headers=merged)


@router.get("/s/{token}")
async def serve_subscription(request: Request, token: str) -> Response:
    return await _serve(request, token)


@router.head("/s/{token}")
async def head_subscription(request: Request, token: str) -> Response:
    response = await _serve(request, token)
    return Response(
        status_code=response.status_code,
        media_type=response.media_type,
        headers=dict(response.headers),
    )


@router.get("/s/{token}/{suffix:path}")
async def serve_subscription_with_suffix(request: Request, token: str, suffix: str) -> Response:
    """Some clients append a filename to the URL; the suffix is ignored."""
    del suffix
    return await _serve(request, token)
