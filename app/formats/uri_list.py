"""The oldest and most widely supported format: a newline-separated list of
``scheme://...`` URIs, optionally base64-encoded as a whole."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Collection
from urllib.parse import quote, unquote, urlsplit

from .base import Node, ParsedSubscription, SubFormat, canonical_protocol

_URI_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def looks_like_uri(line: str) -> bool:
    return bool(_URI_RE.match(line.strip()))


def decode_base64(text: str) -> str | None:
    """Decode a whole-body base64 blob, tolerating padding and URL-safe alphabets."""
    compact = "".join(text.split())
    if len(compact) < 8:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/_\-=]+", compact):
        return None
    candidate = compact.replace("-", "+").replace("_", "/")
    candidate += "=" * (-len(candidate) % 4)
    try:
        raw = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return decoded


def _vmess_name(uri: str) -> str | None:
    """vmess:// carries its label inside a base64 JSON payload, not in the fragment."""
    payload = uri[len("vmess://") :]
    payload = payload.split("#", 1)[0]
    decoded = decode_base64(payload)
    if decoded is None:
        return None
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    label = data.get("ps") or data.get("remarks") or data.get("remark")
    return str(label) if label else None


def rename_uri(uri: str, name: str) -> str:
    """Relabel a URI. vmess:// hides its label inside the payload, everyone else
    uses the fragment."""
    scheme = uri.split("://", 1)[0].lower()
    if scheme == "vmess":
        renamed = _rename_vmess(uri, name)
        if renamed is not None:
            return renamed
    return f"{uri.split('#', 1)[0]}#{quote(name, safe='')}"


def _rename_vmess(uri: str, name: str) -> str | None:
    payload = uri[len("vmess://") :].split("#", 1)[0]
    decoded = decode_base64(payload)
    if decoded is None:
        return None
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["ps"] = name
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return "vmess://" + base64.b64encode(body).decode("ascii")


def _host_port(uri: str) -> tuple[str | None, int | None]:
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None, None
    try:
        return parts.hostname, parts.port
    except ValueError:
        return parts.hostname, None


def parse_uri(index: int, uri: str) -> Node:
    scheme = uri.split("://", 1)[0].lower()
    protocol = canonical_protocol(scheme)

    name = ""
    if "#" in uri:
        name = unquote(uri.rsplit("#", 1)[1])
    if scheme == "vmess":
        name = _vmess_name(uri) or name

    server, port = _host_port(uri)
    if scheme in {"vmess", "ss", "ssr"} and server is None:
        # Legacy fully-base64 payloads have no parseable authority section.
        server = None

    return Node(
        index=index,
        name=name.strip() or f"{protocol} #{index + 1}",
        protocol=protocol,
        server=server,
        port=port,
    )


class UriListSubscription(ParsedSubscription):
    """Keeps every original line verbatim; filtering only drops lines."""

    def __init__(
        self,
        lines: list[str],
        node_line_index: dict[int, int],
        nodes: list[Node],
        *,
        base64_encoded: bool,
        trailing_newline: bool,
    ) -> None:
        super().__init__(nodes)
        self.format = SubFormat.BASE64 if base64_encoded else SubFormat.URI_LIST
        self._lines = lines
        self._node_line_index = node_line_index
        self._base64_encoded = base64_encoded
        self._trailing_newline = trailing_newline

    @classmethod
    def parse(cls, text: str, *, base64_encoded: bool) -> UriListSubscription:
        raw_lines = text.split("\n")
        trailing_newline = bool(raw_lines) and raw_lines[-1] == ""
        if trailing_newline:
            raw_lines = raw_lines[:-1]

        nodes: list[Node] = []
        node_line_index: dict[int, int] = {}
        for line_no, line in enumerate(raw_lines):
            stripped = line.strip()
            if not stripped or not looks_like_uri(stripped):
                continue
            node = parse_uri(len(nodes), stripped)
            node_line_index[node.index] = line_no
            nodes.append(node)
        return cls(
            raw_lines,
            node_line_index,
            nodes,
            base64_encoded=base64_encoded,
            trailing_newline=trailing_newline,
        )

    def node_uri(self, index: int) -> str:
        """The original line behind a node, verbatim. Used when merging sources."""
        return self._lines[self._node_line_index[index]].strip()

    def set_base64(self, enabled: bool) -> None:
        """Switch the envelope. Base64 and plain text carry the same document."""
        self._base64_encoded = enabled
        self.format = SubFormat.BASE64 if enabled else SubFormat.URI_LIST

    def render(self, keep: Collection[int]) -> str:
        keep_set = set(keep)
        dropped_lines = {
            line_no
            for index, line_no in self._node_line_index.items()
            if index not in keep_set
        }
        kept_lines = [
            line for line_no, line in enumerate(self._lines) if line_no not in dropped_lines
        ]
        body = "\n".join(kept_lines)
        if self._trailing_newline and body:
            body += "\n"
        if self._base64_encoded:
            return base64.b64encode(body.encode("utf-8")).decode("ascii")
        return body

    def content_type(self) -> str:
        return "text/plain; charset=utf-8"
