"""Combining several already-filtered subscriptions into one document.

The proxy never converts between families, and merging does not change that: the
parts have to speak the same format, which in practice they do, because every
source is asked with the *same* client's ``User-Agent`` and the panels answer in
the format that client reads. A part that comes back in a different family is
left out and reported, rather than mangled into the others.

Within a family the merge is still not a plain concatenation. Names have to stay
unique — sing-box and Clash address nodes *by* their tag, and two servers called
«🇳🇱 Amsterdam» would collide — and the selector groups of the first document have
to be rewired onto the merged node list, or the client shows a config it cannot
choose anything in.
"""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from .base import FORMAT_LABELS, Node, ParsedSubscription, SubFormat
from .clash import BUILTIN_TARGETS, ClashSubscription
from .singbox import GROUP_TYPES, SingboxSubscription, is_node_entry
from .uri_list import UriListSubscription, rename_uri
from .xray_json import (
    XrayConfigListSubscription,
    XrayConfigSubscription,
    is_node_outbound,
    prune_routing,
)

#: What goes between a source's prefix and the node's own name.
PREFIX_SEPARATOR = " · "


class MergeError(ValueError):
    """Nothing could be merged — no parts at all, or the format is unsupported."""


@dataclass(slots=True)
class MergePart:
    """One source's contribution: what it parsed and what survived its filter."""

    parsed: ParsedSubscription
    keep: Sequence[int]
    prefix: str = ""
    label: str = ""


@dataclass(slots=True)
class MergeResult:
    body: str
    content_type: str
    format: SubFormat
    total: int
    kept: int
    #: Human-readable notes about sources left out of the result.
    skipped: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Item:
    """One node on its way into the merged document."""

    part: MergePart
    index: int
    node: Node
    name: str


def merge(
    parts: Sequence[MergePart], *, dedupe: bool = True, output_format: str = "auto"
) -> MergeResult:
    """Combine ``parts`` into one subscription in the first part's format."""
    if not parts:
        raise MergeError("нет ни одного источника для объединения")

    base = parts[0]
    compatible: list[MergePart] = []
    skipped: list[str] = []
    for part in parts:
        if isinstance(part.parsed, type(base.parsed)):
            compatible.append(part)
            continue
        skipped.append(
            f"«{part.label}» отдал {_format_label(part.parsed.format)}, "
            f"а сборка собирается как {_format_label(base.parsed.format)}"
        )

    items = _collect(compatible, dedupe=dedupe)
    body, content_type, out_format = _render(base, items, output_format)
    return MergeResult(
        body=body,
        content_type=content_type,
        format=out_format,
        total=sum(len(part.parsed.nodes) for part in compatible),
        kept=len(items),
        skipped=skipped,
    )


def _format_label(fmt: SubFormat) -> str:
    return FORMAT_LABELS.get(fmt, fmt.value)


def _collect(parts: Sequence[MergePart], *, dedupe: bool) -> list[_Item]:
    """Flatten the kept nodes of every part, prefixed, deduplicated and uniquely named."""
    items: list[_Item] = []
    used_names: set[str] = set()
    seen: set[tuple[str, str, int]] = set()

    for part in parts:
        keep = set(part.keep)
        for node in part.parsed.nodes:
            if node.index not in keep:
                continue
            if dedupe:
                key = _dedupe_key(node)
                # A node with no parseable address cannot be compared with
                # anything, so it is always let through rather than guessed at.
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
            label = f"{part.prefix}{PREFIX_SEPARATOR}{node.name}" if part.prefix else node.name
            name = _unique(label, used_names)
            items.append(_Item(part=part, index=node.index, node=node, name=name))
    return items


def _dedupe_key(node: Node) -> tuple[str, str, int] | None:
    if not node.server or node.port is None:
        return None
    return (node.protocol, node.server.lower(), node.port)


def _unique(name: str, used: set[str]) -> str:
    candidate = name or "node"
    suffix = 2
    while candidate in used:
        candidate = f"{name} ({suffix})"
        suffix += 1
    used.add(candidate)
    return candidate


# --------------------------------------------------------------------- render


def _render(
    base: MergePart, items: list[_Item], output_format: str
) -> tuple[str, str, SubFormat]:
    parsed = base.parsed
    # The first source's document is the skeleton the rest are poured into, so
    # its own nodes are the ones whose old names its groups and rules still use.
    renames = {item.node.name: item.name for item in items if item.part is base}
    if isinstance(parsed, UriListSubscription):
        return _render_uri_list(parsed, items, output_format)
    if isinstance(parsed, XrayConfigListSubscription):
        return _render_xray_list(items)
    if isinstance(parsed, XrayConfigSubscription):
        return _render_xray_config(parsed, items, renames)
    if isinstance(parsed, SingboxSubscription):
        return _render_singbox(parsed, items, renames)
    if isinstance(parsed, ClashSubscription):
        return _render_clash(parsed, items, renames)
    raise MergeError(f"формат {_format_label(parsed.format)} нельзя объединять")


def _render_uri_list(
    base: UriListSubscription, items: list[_Item], output_format: str
) -> tuple[str, str, SubFormat]:
    lines: list[str] = []
    for item in items:
        parsed = item.part.parsed
        assert isinstance(parsed, UriListSubscription)
        uri = parsed.node_uri(item.index)
        # Untouched when nothing renamed it — passing a URI through byte for byte
        # is always safer than re-encoding it.
        lines.append(uri if item.name == item.node.name else rename_uri(uri, item.name))

    encoded = base.format is SubFormat.BASE64
    if output_format == "base64":
        encoded = True
    elif output_format == "plain":
        encoded = False

    body = "\n".join(lines)
    if body:
        body += "\n"
    if encoded:
        return (
            base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "text/plain; charset=utf-8",
            SubFormat.BASE64,
        )
    return body, "text/plain; charset=utf-8", SubFormat.URI_LIST


def _render_xray_list(items: list[_Item]) -> tuple[str, str, SubFormat]:
    configs: list[Any] = []
    for item in items:
        parsed = item.part.parsed
        assert isinstance(parsed, XrayConfigListSubscription)
        config = copy.deepcopy(parsed.node_config(item.index))
        if isinstance(config, dict):
            config["remarks"] = item.name
        configs.append(config)
    return (
        json.dumps(configs, ensure_ascii=False, indent=2),
        "application/json; charset=utf-8",
        SubFormat.XRAY_JSON,
    )


def _render_xray_config(
    base: XrayConfigSubscription, items: list[_Item], renames: dict[str, str]
) -> tuple[str, str, SubFormat]:
    outbounds: list[Any] = []
    for item in items:
        parsed = item.part.parsed
        assert isinstance(parsed, XrayConfigSubscription)
        outbound = copy.deepcopy(parsed.node_outbound(item.index))
        outbound["tag"] = item.name
        outbounds.append(outbound)

    if base.bare_outbounds:
        return (
            json.dumps(outbounds, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8",
            SubFormat.XRAY_JSON,
        )

    doc = copy.deepcopy(base.document)
    assert isinstance(doc, dict)
    original = doc.get("outbounds")
    original = original if isinstance(original, list) else []
    old_tags = {
        str(entry["tag"])
        for entry in original
        if is_node_outbound(entry) and entry.get("tag")
    }
    doc["outbounds"] = _splice(original, outbounds, is_node_outbound)
    # A rule that named a node still means what it meant — it just has to say the
    # node's new name. Only what has no new name at all is dropped.
    _remap_xray_routing(doc, renames)
    prune_routing(doc, old_tags - {item.name for item in items})
    return (
        json.dumps(doc, ensure_ascii=False, indent=2),
        "application/json; charset=utf-8",
        SubFormat.XRAY_JSON,
    )


def _remap_xray_routing(doc: dict[str, Any], renames: dict[str, str]) -> None:
    routing = doc.get("routing")
    if not renames or not isinstance(routing, dict):
        return
    rules = routing.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and str(rule.get("outboundTag", "")) in renames:
                rule["outboundTag"] = renames[str(rule["outboundTag"])]
    balancers = routing.get("balancers")
    if isinstance(balancers, list):
        for balancer in balancers:
            if isinstance(balancer, dict) and isinstance(balancer.get("selector"), list):
                balancer["selector"] = [
                    renames.get(str(item), str(item)) for item in balancer["selector"]
                ]


def _render_singbox(
    base: SingboxSubscription, items: list[_Item], renames: dict[str, str]
) -> tuple[str, str, SubFormat]:
    merged: dict[str, list[Any]] = {"outbounds": [], "endpoints": []}
    for item in items:
        parsed = item.part.parsed
        assert isinstance(parsed, SingboxSubscription)
        key, entry = parsed.node_entry(item.index)
        clone = copy.deepcopy(entry)
        clone["tag"] = item.name
        merged[key].append(clone)

    doc = copy.deepcopy(base.document)
    old_tags: set[str] = set()
    for key in ("outbounds", "endpoints"):
        original = doc.get(key)
        if not isinstance(original, list):
            if merged[key]:
                doc[key] = merged[key]
            continue
        old_tags |= {
            str(entry["tag"]) for entry in original if is_node_entry(entry) and entry.get("tag")
        }
        doc[key] = _splice(original, merged[key], is_node_entry)

    tags = [item.name for item in items]
    _rewire_singbox_groups(doc, old_tags, tags, renames)
    _prune_singbox_route(doc, old_tags - set(tags), tags, renames)
    return (
        json.dumps(doc, ensure_ascii=False, indent=2),
        "application/json; charset=utf-8",
        SubFormat.SINGBOX,
    )


def _rewire_singbox_groups(
    doc: dict[str, Any], old_tags: set[str], tags: list[str], renames: dict[str, str]
) -> None:
    """Point every group that listed nodes at the merged node list instead."""
    outbounds = doc.get("outbounds")
    if not isinstance(outbounds, list):
        return
    for entry in outbounds:
        if not isinstance(entry, dict) or entry.get("type") not in GROUP_TYPES:
            continue
        members = entry.get("outbounds")
        if not isinstance(members, list):
            continue
        if not any(str(member) in old_tags for member in members):
            continue
        entry["outbounds"] = [m for m in members if str(m) not in old_tags] + tags
        default = entry.get("default")
        if default is None:
            continue
        renamed = renames.get(str(default), str(default))
        if renamed in entry["outbounds"]:
            entry["default"] = renamed
        elif entry["outbounds"]:
            entry["default"] = entry["outbounds"][0]
        else:
            entry.pop("default")


def _prune_singbox_route(
    doc: dict[str, Any], removed: set[str], tags: list[str], renames: dict[str, str]
) -> None:
    route = doc.get("route")
    if not isinstance(route, dict):
        return
    rules = route.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and str(rule.get("outbound", "")) in renames:
                rule["outbound"] = renames[str(rule["outbound"])]
        route["rules"] = [
            rule
            for rule in rules
            if not (isinstance(rule, dict) and str(rule.get("outbound", "")) in removed)
        ]
    final = route.get("final")
    if final is None:
        return
    if str(final) in renames:
        route["final"] = renames[str(final)]
    elif str(final) in removed:
        if tags:
            route["final"] = tags[0]
        else:
            route.pop("final", None)


def _render_clash(
    base: ClashSubscription, items: list[_Item], renames: dict[str, str]
) -> tuple[str, str, SubFormat]:
    proxies: list[Any] = []
    for item in items:
        parsed = item.part.parsed
        assert isinstance(parsed, ClashSubscription)
        proxy = copy.deepcopy(parsed.node_proxy(item.index))
        proxy["name"] = item.name
        proxies.append(proxy)

    doc = copy.deepcopy(base.document)
    original = doc.get("proxies")
    old_names = (
        {str(entry["name"]) for entry in original if isinstance(entry, dict) and entry.get("name")}
        if isinstance(original, list)
        else set()
    )
    doc["proxies"] = proxies

    names = [item.name for item in items]
    _rewire_clash_groups(doc, old_names, names)
    _repoint_clash_rules(doc, old_names - set(names), renames)
    return (
        yaml.safe_dump(
            doc, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096
        ),
        "text/yaml; charset=utf-8",
        SubFormat.CLASH,
    )


def _rewire_clash_groups(doc: dict[str, Any], old_names: set[str], names: list[str]) -> None:
    groups = doc.get("proxy-groups")
    if not isinstance(groups, list):
        return
    surviving: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            surviving.append(group)
            continue
        members = group.get("proxies")
        if isinstance(members, list) and any(str(member) in old_names for member in members):
            group["proxies"] = [m for m in members if str(m) not in old_names] + names
        surviving.append(group)
    doc["proxy-groups"] = surviving


def _repoint_clash_rules(doc: dict[str, Any], removed: set[str], renames: dict[str, str]) -> None:
    """Follow a renamed proxy; fall back to DIRECT only when it is really gone."""
    if not removed and not renames:
        return
    rules = doc.get("rules")
    if not isinstance(rules, list):
        return
    rewritten: list[Any] = []
    for rule in rules:
        if not isinstance(rule, str):
            rewritten.append(rule)
            continue
        parts = [part.strip() for part in rule.split(",")]
        changed = False
        for i, part in enumerate(parts):
            if i == 0 or part in BUILTIN_TARGETS:
                continue
            if part in renames:
                parts[i] = renames[part]
                changed = True
            elif part in removed:
                parts[i] = "DIRECT"
                changed = True
        rewritten.append(",".join(parts) if changed else rule)
    doc["rules"] = rewritten


def _splice(original: list[Any], replacement: list[Any], is_node: Any) -> list[Any]:
    """Put ``replacement`` where the original's first node sat, drop the other nodes.

    Keeps everything that is not a node — Xray's ``freedom``/``blackhole``,
    sing-box's selectors and DNS outbounds — exactly where the source had it.
    """
    result: list[Any] = []
    inserted = False
    for entry in original:
        if is_node(entry):
            if not inserted:
                result.extend(replacement)
                inserted = True
            continue
        result.append(entry)
    if not inserted:
        result.extend(replacement)
    return result
