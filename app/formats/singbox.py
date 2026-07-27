"""Sing-box JSON subscriptions.

Nodes live in ``outbounds`` (and, since sing-box 1.11, in ``endpoints`` for
WireGuard). Dropping a node is not enough on its own: ``selector``/``urltest``
groups reference nodes by tag, and a group left pointing at nothing makes
sing-box refuse to start. So removals cascade — an emptied group is removed too,
and references to it are pruned in turn, until the document is stable again.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from .base import NON_NODE_KINDS, Node, ParsedSubscription, SubFormat, canonical_protocol

GROUP_TYPES = frozenset({"selector", "urltest"})


def is_node_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    kind = canonical_protocol(str(entry.get("type", "")))
    return kind not in NON_NODE_KINDS and kind != "unknown"


class SingboxSubscription(ParsedSubscription):
    format = SubFormat.SINGBOX

    def __init__(
        self,
        doc: dict[str, Any],
        nodes: list[Node],
        node_refs: list[tuple[str, int]],
    ) -> None:
        super().__init__(nodes)
        self._doc = doc
        self._node_refs = node_refs

    @classmethod
    def parse(cls, doc: dict[str, Any]) -> SingboxSubscription:
        nodes: list[Node] = []
        refs: list[tuple[str, int]] = []
        for key in ("outbounds", "endpoints"):
            entries = doc.get(key)
            if not isinstance(entries, list):
                continue
            for position, entry in enumerate(entries):
                if not is_node_entry(entry):
                    continue
                port = entry.get("server_port")
                nodes.append(
                    Node(
                        index=len(nodes),
                        name=str(entry.get("tag") or f"node #{len(nodes) + 1}"),
                        protocol=canonical_protocol(str(entry.get("type", ""))),
                        server=str(entry["server"]) if entry.get("server") else None,
                        port=int(port) if isinstance(port, int) else None,
                    )
                )
                refs.append((key, position))
        return cls(doc, nodes, refs)

    def render(self, keep: Collection[int]) -> str:
        keep_set = set(keep)
        doc = json.loads(json.dumps(self._doc))

        drop: dict[str, set[int]] = {"outbounds": set(), "endpoints": set()}
        removed_tags: set[str] = set()
        for index, (key, position) in enumerate(self._node_refs):
            if index in keep_set:
                continue
            drop[key].add(position)
            entries = self._doc.get(key) or []
            if position < len(entries) and isinstance(entries[position], dict):
                tag = entries[position].get("tag")
                if tag:
                    removed_tags.add(str(tag))

        for key, positions in drop.items():
            entries = doc.get(key)
            if isinstance(entries, list) and positions:
                doc[key] = [e for pos, e in enumerate(entries) if pos not in positions]

        _cascade_group_removal(doc, removed_tags)
        _prune_route(doc, removed_tags)
        return json.dumps(doc, ensure_ascii=False, indent=2)

    def content_type(self) -> str:
        return "application/json; charset=utf-8"

    @property
    def document(self) -> dict[str, Any]:
        """The parsed document itself — the skeleton a merge builds on."""
        return self._doc

    def node_entry(self, index: int) -> tuple[str, dict[str, Any]]:
        """``("outbounds" | "endpoints", entry)`` for a node, as it arrived."""
        key, position = self._node_refs[index]
        return key, self._doc[key][position]


def _cascade_group_removal(doc: dict[str, Any], removed_tags: set[str]) -> None:
    """Strip dead tags from groups; delete groups that end up empty, repeatedly."""
    while True:
        outbounds = doc.get("outbounds")
        if not isinstance(outbounds, list):
            return
        newly_removed: set[str] = set()
        surviving: list[Any] = []
        for entry in outbounds:
            if not isinstance(entry, dict) or entry.get("type") not in GROUP_TYPES:
                surviving.append(entry)
                continue
            members = entry.get("outbounds")
            if isinstance(members, list):
                entry["outbounds"] = [m for m in members if str(m) not in removed_tags]
                if not entry["outbounds"]:
                    tag = entry.get("tag")
                    if tag:
                        newly_removed.add(str(tag))
                    continue
                default = entry.get("default")
                if default is not None and str(default) not in entry["outbounds"]:
                    entry["default"] = entry["outbounds"][0]
            surviving.append(entry)
        doc["outbounds"] = surviving
        if not newly_removed:
            return
        removed_tags |= newly_removed


def _prune_route(doc: dict[str, Any], removed_tags: set[str]) -> None:
    if not removed_tags:
        return
    route = doc.get("route")
    if not isinstance(route, dict):
        return
    rules = route.get("rules")
    if isinstance(rules, list):
        route["rules"] = [
            rule
            for rule in rules
            if not (isinstance(rule, dict) and str(rule.get("outbound", "")) in removed_tags)
        ]
    final = route.get("final")
    if final is not None and str(final) in removed_tags:
        fallback = _first_live_tag(doc)
        if fallback is None:
            route.pop("final", None)
        else:
            route["final"] = fallback


def _first_live_tag(doc: dict[str, Any]) -> str | None:
    """Prefer a group, then any node, then a direct outbound."""
    outbounds = doc.get("outbounds")
    if not isinstance(outbounds, list):
        return None
    for wanted_group in (True, False):
        for entry in outbounds:
            if not isinstance(entry, dict) or not entry.get("tag"):
                continue
            is_group = entry.get("type") in GROUP_TYPES
            if is_group is wanted_group:
                return str(entry["tag"])
    return None
