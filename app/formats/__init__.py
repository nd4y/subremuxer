"""Subscription format detection and parsing.

The proxy never converts between families — it hands the client's ``User-Agent``
and ``Accept`` to the upstream panel, lets the panel decide which family to
speak, then filters *inside* that family and hands the result back untouched in
every other respect. The one exception is the URI-list family, where base64 and
plain text are the same document with a different envelope, so switching between
them is lossless and is offered as an option.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from . import clash as clash_mod
from . import singbox as singbox_mod
from . import uri_list as uri_mod
from . import xray_json as xray_mod
from .base import (
    FORMAT_LABELS,
    KNOWN_PROTOCOLS,
    Node,
    ParsedSubscription,
    SubFormat,
    canonical_protocol,
)

__all__ = [
    "FORMAT_LABELS",
    "KNOWN_PROTOCOLS",
    "Node",
    "ParsedSubscription",
    "SubFormat",
    "UnknownFormatError",
    "canonical_protocol",
    "detect_and_parse",
]


class UnknownFormatError(ValueError):
    """The upstream body did not look like any subscription format we know."""


def _classify_json(data: Any) -> str:
    """Return ``"singbox"``, ``"xray"`` or ``"clash"`` for a decoded JSON document."""
    if isinstance(data, dict):
        if "proxies" in data and "outbounds" not in data:
            return "clash"
        outbounds = data.get("outbounds")
        if isinstance(outbounds, list):
            for entry in outbounds:
                if not isinstance(entry, dict):
                    continue
                if "protocol" in entry:
                    return "xray"
                if "type" in entry:
                    return "singbox"
            # An empty or opaque outbounds list: fall back to sibling keys.
            return "singbox" if ("endpoints" in data or "route" in data) else "xray"
        if "endpoints" in data or "route" in data:
            return "singbox"
        return "xray"

    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if "outbounds" in entry or "remarks" in entry:
                return "xray"
            if "protocol" in entry:
                return "xray"
            if "type" in entry and "tag" in entry:
                return "singbox"
        return "xray"

    raise UnknownFormatError("JSON document is neither an object nor an array")


def _parse_json(data: Any) -> ParsedSubscription:
    kind = _classify_json(data)
    if kind == "singbox":
        if not isinstance(data, dict):
            raise UnknownFormatError("sing-box configs must be JSON objects")
        return singbox_mod.SingboxSubscription.parse(data)
    if kind == "clash":
        if not isinstance(data, dict):
            raise UnknownFormatError("clash configs must be JSON objects")
        return clash_mod.ClashSubscription.parse(data)
    return xray_mod.parse(data)


def detect_and_parse(text: str) -> ParsedSubscription:
    """Sniff the body and return a parsed, filterable subscription.

    Order matters. JSON first (unambiguous), then base64 (a compact alphabet that
    nothing else matches), then a bare URI list, and YAML last — YAML would
    happily accept a base64 blob as a plain scalar and swallow the real answer.
    """
    stripped = text.strip()
    if not stripped:
        raise UnknownFormatError("empty response body")

    if stripped[0] in "{[":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise UnknownFormatError(f"looks like JSON but does not parse: {exc}") from exc
        return _parse_json(data)

    decoded = uri_mod.decode_base64(stripped)
    if decoded is not None and "://" in decoded:
        return uri_mod.UriListSubscription.parse(decoded, base64_encoded=True)

    if any(uri_mod.looks_like_uri(line) for line in stripped.splitlines()):
        return uri_mod.UriListSubscription.parse(text, base64_encoded=False)

    try:
        data = yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        raise UnknownFormatError(f"not JSON, not base64, not YAML: {exc}") from exc

    if isinstance(data, dict) and ("proxies" in data or "proxy-groups" in data):
        return clash_mod.ClashSubscription.parse(data)
    if isinstance(data, dict | list):
        # Valid YAML that also happens to be valid JSON-ish structure.
        return _parse_json(data)

    raise UnknownFormatError("unrecognised subscription body")
