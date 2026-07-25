"""Shared vocabulary for every subscription format."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum


class SubFormat(StrEnum):
    """Wire formats a subscription endpoint can speak."""

    BASE64 = "base64"
    URI_LIST = "uri_list"
    XRAY_JSON = "xray_json"
    SINGBOX = "singbox"
    CLASH = "clash"


#: Human-facing labels, also used by the admin UI.
FORMAT_LABELS: dict[SubFormat, str] = {
    SubFormat.BASE64: "Base64 (список URI)",
    SubFormat.URI_LIST: "Открытый список URI",
    SubFormat.XRAY_JSON: "Xray JSON",
    SubFormat.SINGBOX: "Sing-box JSON",
    SubFormat.CLASH: "Clash / Mihomo YAML",
}

#: Canonical protocol names. Everything the parsers see is mapped onto these so the
#: protocol filter behaves identically no matter which format arrived.
KNOWN_PROTOCOLS: tuple[str, ...] = (
    "vless",
    "vmess",
    "trojan",
    "shadowsocks",
    "shadowsocksr",
    "hysteria",
    "hysteria2",
    "tuic",
    "wireguard",
    "anytls",
    "snell",
    "shadowtls",
    "juicity",
    "mieru",
    "socks",
    "http",
    "ssh",
)

_PROTOCOL_ALIASES: dict[str, str] = {
    "ss": "shadowsocks",
    "ssr": "shadowsocksr",
    "hy": "hysteria",
    "hy2": "hysteria2",
    "hysteria1": "hysteria",
    "socks5": "socks",
    "socks4": "socks",
    "socks4a": "socks",
    "https": "http",
    "wg": "wireguard",
    "wireguard-go": "wireguard",
}

#: Outbound kinds that describe routing behaviour rather than a server.
NON_NODE_KINDS: frozenset[str] = frozenset(
    {
        "freedom",
        "blackhole",
        "dns",
        "loopback",
        "direct",
        "block",
        "reject",
        "selector",
        "urltest",
        "dns-out",
        "direct-out",
        "block-out",
    }
)


def canonical_protocol(raw: str | None) -> str:
    """Normalise a protocol/type string to one canonical spelling."""
    if not raw:
        return "unknown"
    key = raw.strip().lower()
    key = _PROTOCOL_ALIASES.get(key, key)
    return key


@dataclass(slots=True)
class Node:
    """One proxy server, as seen inside whatever format arrived."""

    index: int
    name: str
    protocol: str
    server: str | None = None
    port: int | None = None
    extra: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "name": self.name,
            "protocol": self.protocol,
            "server": self.server,
            "port": self.port,
        }


class ParsedSubscription(ABC):
    """A subscription body parsed into nodes, able to re-emit itself minus some nodes.

    The contract is deliberately *lossless*: parsers never rebuild a node from
    scratch, they keep the original representation and only drop entries. A config
    that this app passes through unfiltered is byte-comparable in meaning to the
    upstream one, so no client ever breaks because of a re-serialisation quirk.
    """

    format: SubFormat

    def __init__(self, nodes: list[Node]) -> None:
        self.nodes = nodes

    @abstractmethod
    def render(self, keep: Collection[int]) -> str:
        """Serialise the subscription keeping only nodes whose index is in ``keep``."""

    def content_type(self) -> str:
        return "text/plain; charset=utf-8"
