"""Clash / Mihomo / Stash YAML subscriptions.

Nodes are entries of ``proxies``. As with sing-box, removing a proxy means
scrubbing its name out of every ``proxy-groups`` entry, deleting groups that end
up with no members at all, and repointing any rule that targeted a deleted group
at ``DIRECT`` so the resulting config still loads.
"""

from __future__ import annotations

import copy
from collections.abc import Collection
from typing import Any

import yaml

from .base import Node, ParsedSubscription, SubFormat, canonical_protocol

#: Targets that always exist, whatever the proxy list looks like.
BUILTIN_TARGETS = frozenset({"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"})


class ClashSubscription(ParsedSubscription):
    format = SubFormat.CLASH

    def __init__(self, doc: dict[str, Any], nodes: list[Node], node_positions: list[int]) -> None:
        super().__init__(nodes)
        self._doc = doc
        self._node_positions = node_positions

    @classmethod
    def parse(cls, doc: dict[str, Any]) -> ClashSubscription:
        proxies = doc.get("proxies")
        nodes: list[Node] = []
        positions: list[int] = []
        if isinstance(proxies, list):
            for position, proxy in enumerate(proxies):
                if not isinstance(proxy, dict):
                    continue
                port = proxy.get("port")
                nodes.append(
                    Node(
                        index=len(nodes),
                        name=str(proxy.get("name") or f"proxy #{len(nodes) + 1}"),
                        protocol=canonical_protocol(str(proxy.get("type", ""))),
                        server=str(proxy["server"]) if proxy.get("server") else None,
                        port=int(port) if isinstance(port, int) else None,
                    )
                )
                positions.append(position)
        return cls(doc, nodes, positions)

    def render(self, keep: Collection[int]) -> str:
        keep_set = set(keep)
        doc = _deep_copy(self._doc)

        drop_positions = {
            position for i, position in enumerate(self._node_positions) if i not in keep_set
        }
        proxies = doc.get("proxies")
        removed_names: set[str] = set()
        if isinstance(proxies, list):
            for position in drop_positions:
                if position < len(proxies) and isinstance(proxies[position], dict):
                    name = proxies[position].get("name")
                    if name is not None:
                        removed_names.add(str(name))
            doc["proxies"] = [p for pos, p in enumerate(proxies) if pos not in drop_positions]

        _cascade_group_removal(doc, removed_names)
        _prune_rules(doc, removed_names)
        return yaml.safe_dump(
            doc,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=4096,
        )

    def content_type(self) -> str:
        return "text/yaml; charset=utf-8"


def _deep_copy(doc: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(doc)


def _cascade_group_removal(doc: dict[str, Any], removed_names: set[str]) -> None:
    while True:
        groups = doc.get("proxy-groups")
        if not isinstance(groups, list):
            return
        newly_removed: set[str] = set()
        surviving: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                surviving.append(group)
                continue
            members = group.get("proxies")
            if isinstance(members, list):
                group["proxies"] = [m for m in members if str(m) not in removed_names]
                uses_provider = bool(group.get("use"))
                if not group["proxies"] and not uses_provider:
                    name = group.get("name")
                    if name is not None:
                        newly_removed.add(str(name))
                    continue
            surviving.append(group)
        doc["proxy-groups"] = surviving
        if not newly_removed:
            return
        removed_names |= newly_removed


def _prune_rules(doc: dict[str, Any], removed_names: set[str]) -> None:
    """Repoint rules whose target disappeared. Never drops a rule silently."""
    if not removed_names:
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
            if part in removed_names:
                parts[i] = "DIRECT"
                changed = True
        rewritten.append(",".join(parts) if changed else rule)
    doc["rules"] = rewritten
