"""Xray JSON subscriptions.

Two shapes exist in the wild and both are handled:

* a JSON **array of complete configs**, each with a ``remarks`` label — the shape
  v2rayN/Happ-style clients expect;
* a **single config object** with an ``outbounds`` array, where each proxy outbound
  is a node identified by its ``tag``.

A bare ``outbounds`` array is accepted as a third, degenerate case.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from .base import NON_NODE_KINDS, Node, ParsedSubscription, SubFormat, canonical_protocol


def is_node_outbound(outbound: Any) -> bool:
    if not isinstance(outbound, dict):
        return False
    protocol = canonical_protocol(str(outbound.get("protocol", "")))
    return protocol not in NON_NODE_KINDS and protocol != "unknown"


def _outbound_node(index: int, outbound: dict[str, Any]) -> Node:
    settings = outbound.get("settings")
    server: str | None = None
    port: int | None = None
    if isinstance(settings, dict):
        for key in ("vnext", "servers"):
            entries = settings.get(key)
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                server = entries[0].get("address") or entries[0].get("host")
                raw_port = entries[0].get("port")
                if isinstance(raw_port, int | str) and str(raw_port).isdigit():
                    port = int(raw_port)
                break
    return Node(
        index=index,
        name=str(outbound.get("tag") or f"outbound #{index + 1}"),
        protocol=canonical_protocol(str(outbound.get("protocol", ""))),
        server=str(server) if server else None,
        port=port,
    )


class XrayConfigListSubscription(ParsedSubscription):
    """A JSON array where every element is a full Xray config for one server."""

    format = SubFormat.XRAY_JSON

    def __init__(self, configs: list[Any], nodes: list[Node], node_positions: list[int]) -> None:
        super().__init__(nodes)
        self._configs = configs
        self._node_positions = node_positions

    @classmethod
    def parse(cls, configs: list[Any]) -> XrayConfigListSubscription:
        nodes: list[Node] = []
        positions: list[int] = []
        for position, config in enumerate(configs):
            if not isinstance(config, dict):
                continue
            outbounds = config.get("outbounds")
            proxy = None
            if isinstance(outbounds, list):
                proxy = next((ob for ob in outbounds if is_node_outbound(ob)), None)
            if isinstance(proxy, dict):
                node = _outbound_node(len(nodes), proxy)
            else:
                node = Node(index=len(nodes), name="", protocol="unknown")
            label = config.get("remarks") or config.get("remark") or node.name
            node.name = str(label or f"config #{position + 1}")
            positions.append(position)
            nodes.append(node)
        return cls(configs, nodes, positions)

    def render(self, keep: Collection[int]) -> str:
        keep_set = set(keep)
        kept_positions = {
            self._node_positions[i] for i in keep_set if i < len(self._node_positions)
        }
        result = [cfg for pos, cfg in enumerate(self._configs) if pos in kept_positions]
        return json.dumps(result, ensure_ascii=False, indent=2)

    def node_config(self, index: int) -> dict[str, Any]:
        """The whole config behind a node, as it arrived. Used when merging sources."""
        return self._configs[self._node_positions[index]]

    def content_type(self) -> str:
        return "application/json; charset=utf-8"


class XrayConfigSubscription(ParsedSubscription):
    """A single Xray config whose ``outbounds`` hold the nodes."""

    format = SubFormat.XRAY_JSON

    def __init__(
        self,
        doc: dict[str, Any] | list[Any],
        nodes: list[Node],
        node_positions: list[int],
        *,
        bare_outbounds: bool,
    ) -> None:
        super().__init__(nodes)
        self._doc = doc
        self._node_positions = node_positions
        self._bare = bare_outbounds

    @classmethod
    def parse(cls, doc: dict[str, Any] | list[Any]) -> XrayConfigSubscription:
        bare = isinstance(doc, list)
        outbounds = doc if bare else doc.get("outbounds", [])  # type: ignore[union-attr]
        if not isinstance(outbounds, list):
            outbounds = []
        nodes: list[Node] = []
        positions: list[int] = []
        for position, outbound in enumerate(outbounds):
            if not is_node_outbound(outbound):
                continue
            nodes.append(_outbound_node(len(nodes), outbound))
            positions.append(position)
        return cls(doc, nodes, positions, bare_outbounds=bare)

    def render(self, keep: Collection[int]) -> str:
        keep_set = set(keep)
        drop_positions = {
            position for i, position in enumerate(self._node_positions) if i not in keep_set
        }

        if self._bare:
            outbounds = [ob for pos, ob in enumerate(self._doc) if pos not in drop_positions]  # type: ignore[arg-type]
            return json.dumps(outbounds, ensure_ascii=False, indent=2)

        doc = json.loads(json.dumps(self._doc))  # cheap deep copy, keeps rendering pure
        original = doc.get("outbounds", [])
        removed_tags = {
            str(original[pos].get("tag"))
            for pos in drop_positions
            if pos < len(original) and isinstance(original[pos], dict) and original[pos].get("tag")
        }
        doc["outbounds"] = [ob for pos, ob in enumerate(original) if pos not in drop_positions]
        prune_routing(doc, removed_tags)
        return json.dumps(doc, ensure_ascii=False, indent=2)

    def content_type(self) -> str:
        return "application/json; charset=utf-8"

    @property
    def document(self) -> dict[str, Any] | list[Any]:
        """The parsed document itself — the skeleton a merge builds on."""
        return self._doc

    @property
    def bare_outbounds(self) -> bool:
        """True when the document is a naked ``outbounds`` array, not a config."""
        return self._bare

    def node_outbound(self, index: int) -> dict[str, Any]:
        outbounds = self._doc if self._bare else self._doc.get("outbounds", [])  # type: ignore[union-attr]
        return outbounds[self._node_positions[index]]


def prune_routing(doc: dict[str, Any], removed_tags: set[str]) -> None:
    """Drop routing rules that point at outbounds which no longer exist."""
    if not removed_tags:
        return
    routing = doc.get("routing")
    if not isinstance(routing, dict):
        return
    rules = routing.get("rules")
    if isinstance(rules, list):
        routing["rules"] = [
            rule
            for rule in rules
            if not (isinstance(rule, dict) and str(rule.get("outboundTag", "")) in removed_tags)
        ]
    balancers = routing.get("balancers")
    if isinstance(balancers, list):
        for balancer in balancers:
            if isinstance(balancer, dict) and isinstance(balancer.get("selector"), list):
                balancer["selector"] = [
                    sel for sel in balancer["selector"] if str(sel) not in removed_tags
                ]


def parse(data: Any) -> ParsedSubscription:
    """Pick the right Xray shape for an already-decoded JSON document."""
    if isinstance(data, list):
        dict_items = [item for item in data if isinstance(item, dict)]
        if dict_items and all("outbounds" in item for item in dict_items):
            return XrayConfigListSubscription.parse(data)
        return XrayConfigSubscription.parse(data)
    if isinstance(data, dict):
        return XrayConfigSubscription.parse(data)
    raise ValueError("not an Xray JSON document")
