"""Profile storage: one row per subscription the proxy republishes."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .filtering import CompiledFilter, FilterConfig
from .formats import KNOWN_PROTOCOLS, canonical_protocol
from .upstream import HWID_MODES

OUTPUT_FORMATS = ("auto", "base64", "plain")

TOKEN_BYTES = 16

#: Presets for the "what should the panel think we are" selector. The upstream
#: panel picks the subscription format from the User-Agent, so overriding it here
#: is how an admin forces a particular family.
USER_AGENT_PRESETS: list[dict[str, str]] = [
    {"id": "client", "label": "Как у клиента (по умолчанию)", "value": ""},
    {"id": "happ", "label": "Happ — Xray JSON", "value": "Happ/2.0"},
    {"id": "singbox", "label": "sing-box — Sing-box JSON", "value": "SFI/1.11 sing-box"},
    {"id": "mihomo", "label": "Mihomo / Clash — YAML", "value": "clash-verge/v2.0.0 mihomo"},
    {"id": "v2rayn", "label": "v2rayN — base64", "value": "v2rayN/7.0"},
    {"id": "browser", "label": "Браузер — HTML-страница", "value": "Mozilla/5.0"},
]


class ProfileError(ValueError):
    pass


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


@dataclass(slots=True)
class Profile:
    id: int
    name: str
    token: str
    upstream_url: str
    enabled: bool = True
    hwid_mode: str = "override"
    hwid: str | None = None
    device_os: str | None = None
    device_ver: str | None = None
    device_model: str | None = None
    filter_config: FilterConfig = field(default_factory=FilterConfig)
    protocols: list[str] = field(default_factory=list)
    output_format: str = "auto"
    upstream_ua: str | None = None
    cache_ttl: int = 0
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Profile:
        return cls(
            id=row["id"],
            name=row["name"],
            token=row["token"],
            upstream_url=row["upstream_url"],
            enabled=bool(row["enabled"]),
            hwid_mode=row["hwid_mode"],
            hwid=row["hwid"],
            device_os=row["device_os"],
            device_ver=row["device_ver"],
            device_model=row["device_model"],
            filter_config=FilterConfig.from_dict(json.loads(row["filter_json"] or "{}")),
            protocols=json.loads(row["protocols_json"] or "[]"),
            output_format=row["output_format"],
            upstream_ua=row["upstream_ua"],
            cache_ttl=row["cache_ttl"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "token": self.token,
            "upstream_url": self.upstream_url,
            "enabled": self.enabled,
            "hwid_mode": self.hwid_mode,
            "hwid": self.hwid,
            "device_os": self.device_os,
            "device_ver": self.device_ver,
            "device_model": self.device_model,
            "filter": self.filter_config.as_dict(),
            "protocols": self.protocols,
            "output_format": self.output_format,
            "upstream_ua": self.upstream_ua,
            "cache_ttl": self.cache_ttl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def compiled_filter(self) -> CompiledFilter:
        return CompiledFilter.build(self.filter_config, self.protocols)


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ProfileError("укажите название профиля")
    if len(name) > 120:
        raise ProfileError("название слишком длинное")

    url = str(payload.get("upstream_url", "")).strip()
    if not url:
        raise ProfileError("укажите ссылку на исходную подписку")
    if not url.lower().startswith(("http://", "https://")):
        raise ProfileError("ссылка должна начинаться с http:// или https://")
    if len(url) > 2000:
        raise ProfileError("ссылка слишком длинная")

    hwid_mode = str(payload.get("hwid_mode", "override"))
    if hwid_mode not in HWID_MODES:
        raise ProfileError(f"неизвестный режим HWID: {hwid_mode}")

    output_format = str(payload.get("output_format", "auto"))
    if output_format not in OUTPUT_FORMATS:
        raise ProfileError(f"неизвестный формат вывода: {output_format}")

    raw_protocols = payload.get("protocols") or []
    if not isinstance(raw_protocols, list):
        raise ProfileError("protocols должен быть списком")
    protocols: list[str] = []
    for item in raw_protocols:
        canonical = canonical_protocol(str(item))
        if canonical not in KNOWN_PROTOCOLS:
            raise ProfileError(f"неизвестный протокол: {item}")
        if canonical not in protocols:
            protocols.append(canonical)

    filter_config = FilterConfig.from_dict(payload.get("filter"))
    # Fail fast: an un-compilable filter must never reach the subscription path.
    CompiledFilter.build(filter_config, protocols)

    cache_ttl = int(payload.get("cache_ttl") or 0)
    if cache_ttl < 0 or cache_ttl > 86400:
        raise ProfileError("TTL кэша должен быть от 0 до 86400 секунд")

    return {
        "name": name,
        "upstream_url": url,
        "enabled": bool(payload.get("enabled", True)),
        "hwid_mode": hwid_mode,
        "hwid": (str(payload.get("hwid") or "").strip() or None),
        "device_os": (str(payload.get("device_os") or "").strip() or None),
        "device_ver": (str(payload.get("device_ver") or "").strip() or None),
        "device_model": (str(payload.get("device_model") or "").strip() or None),
        "filter_json": json.dumps(filter_config.as_dict(), ensure_ascii=False),
        "protocols_json": json.dumps(protocols),
        "output_format": output_format,
        "upstream_ua": (str(payload.get("upstream_ua") or "").strip() or None),
        "cache_ttl": cache_ttl,
    }


class ProfileRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Profile]:
        rows = self.db.query("SELECT * FROM profiles ORDER BY id DESC")
        return [Profile.from_row(row) for row in rows]

    def get(self, profile_id: int) -> Profile | None:
        row = self.db.query_one("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        return Profile.from_row(row) if row else None

    def get_by_token(self, token: str) -> Profile | None:
        row = self.db.query_one("SELECT * FROM profiles WHERE token = ?", (token,))
        return Profile.from_row(row) if row else None

    def create(self, payload: dict[str, Any]) -> Profile:
        fields = _validate(payload)
        now = int(time.time())
        token = str(payload.get("token") or "").strip() or new_token()
        cursor = self.db.execute(
            """
            INSERT INTO profiles(
                name, token, upstream_url, enabled, hwid_mode, hwid,
                device_os, device_ver, device_model, filter_json, protocols_json,
                output_format, upstream_ua, cache_ttl, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fields["name"],
                token,
                fields["upstream_url"],
                int(fields["enabled"]),
                fields["hwid_mode"],
                fields["hwid"],
                fields["device_os"],
                fields["device_ver"],
                fields["device_model"],
                fields["filter_json"],
                fields["protocols_json"],
                fields["output_format"],
                fields["upstream_ua"],
                fields["cache_ttl"],
                now,
                now,
            ),
        )
        created = self.get(int(cursor.lastrowid or 0))
        if created is None:  # pragma: no cover - only on a storage failure
            raise ProfileError("не удалось создать профиль")
        return created

    def update(self, profile_id: int, payload: dict[str, Any]) -> Profile:
        existing = self.get(profile_id)
        if existing is None:
            raise ProfileError("профиль не найден")
        fields = _validate(payload)
        self.db.execute(
            """
            UPDATE profiles SET
                name=?, upstream_url=?, enabled=?, hwid_mode=?, hwid=?,
                device_os=?, device_ver=?, device_model=?, filter_json=?, protocols_json=?,
                output_format=?, upstream_ua=?, cache_ttl=?, updated_at=?
            WHERE id=?
            """,
            (
                fields["name"],
                fields["upstream_url"],
                int(fields["enabled"]),
                fields["hwid_mode"],
                fields["hwid"],
                fields["device_os"],
                fields["device_ver"],
                fields["device_model"],
                fields["filter_json"],
                fields["protocols_json"],
                fields["output_format"],
                fields["upstream_ua"],
                fields["cache_ttl"],
                int(time.time()),
                profile_id,
            ),
        )
        updated = self.get(profile_id)
        assert updated is not None
        return updated

    def delete(self, profile_id: int) -> bool:
        cursor = self.db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cursor.rowcount > 0

    def rotate_token(self, profile_id: int) -> Profile:
        token = new_token()
        self.db.execute(
            "UPDATE profiles SET token = ?, updated_at = ? WHERE id = ?",
            (token, int(time.time()), profile_id),
        )
        profile = self.get(profile_id)
        if profile is None:
            raise ProfileError("профиль не найден")
        return profile
