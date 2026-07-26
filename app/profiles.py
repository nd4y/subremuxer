"""Profile storage: one row per subscription the proxy republishes."""

from __future__ import annotations

import json
import re
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

#: Overriding the User-Agent does exactly one thing: it decides which format the
#: panel answers with. It is NOT how HWID mimicry works — the panel counts devices
#: by the `x-hwid` header, which we send regardless. Faking the User-Agent when it
#: is not needed actively breaks clients: a panel told "this is Happ" answers with
#: an Xray-JSON array, and a client that only reads base64 imports zero servers.
CLIENT_PRESETS: list[dict[str, object]] = [
    {
        "id": "passthrough",
        "label": "Как у клиента (рекомендуется)",
        "user_agent": "",
        "family": "панель выберет под настоящий клиент",
        "forces_format": False,
        "hint": "HWID и данные устройства подменяются всё равно — для них User-Agent не нужен",
    },
    {
        "id": "happ",
        "label": "Happ",
        "user_agent": "Happ/2.16.0",
        "family": "Xray JSON",
        "forces_format": True,
        "hint": "Задал стандарт HWID-заголовков",
    },
    {
        "id": "v2raytun",
        "label": "v2RayTun",
        "user_agent": "v2RayTun/3.9.0",
        "family": "Xray JSON",
        "forces_format": True,
        "hint": "Поддерживает HWID, широко распространён",
    },
    {
        "id": "streisand",
        "label": "Streisand",
        "user_agent": "Streisand/1.6.60",
        "family": "Xray JSON",
        "forces_format": True,
        "hint": "iOS-клиент на ядре Xray",
    },
    {
        "id": "singbox",
        "label": "sing-box (SFA / SFI / SFM)",
        "user_agent": "SFA/1.11.0 sing-box/1.11.0",
        "family": "Sing-box JSON",
        "forces_format": True,
        "hint": "Официальные клиенты sing-box",
    },
    {
        "id": "karing",
        "label": "Karing",
        "user_agent": "Karing/1.1.4.600",
        "family": "Sing-box JSON",
        "forces_format": True,
        "hint": "HWID по умолчанию выключен в самом клиенте",
    },
    {
        "id": "hiddify",
        "label": "Hiddify",
        "user_agent": "HiddifyNext/2.5.7 sing-box",
        "family": "Sing-box JSON",
        "forces_format": True,
        "hint": "Кроссплатформенный клиент на ядре sing-box",
    },
    {
        "id": "mihomo",
        "label": "Clash Verge / Mihomo",
        "user_agent": "clash-verge/v2.0.3 mihomo",
        "family": "Clash / Mihomo YAML",
        "forces_format": True,
        "hint": "Десктопный Clash-клиент",
    },
    {
        "id": "flclash",
        "label": "FlClash",
        "user_agent": "FlClash/0.8.80 clash-meta",
        "family": "Clash / Mihomo YAML",
        "forces_format": True,
        "hint": "Мобильный Clash-клиент",
    },
    {
        "id": "stash",
        "label": "Stash",
        "user_agent": "Stash/3.1.0 Clash",
        "family": "Clash / Mihomo YAML",
        "forces_format": True,
        "hint": "iOS-клиент на ядре Clash",
    },
    {
        "id": "shadowrocket",
        "label": "Shadowrocket",
        "user_agent": "Shadowrocket/2.2.45",
        "family": "Base64",
        "forces_format": True,
        "hint": "HWID по умолчанию выключен в самом клиенте",
    },
    {
        "id": "v2rayn",
        "label": "v2rayN",
        "user_agent": "v2rayN/7.12.4",
        "family": "Base64",
        "forces_format": True,
        "hint": "Десктопный клиент для Windows",
    },
    {
        "id": "v2rayng",
        "label": "v2rayNG",
        "user_agent": "v2rayNG/1.9.30",
        "family": "Base64",
        "forces_format": True,
        "hint": "Android-клиент",
    },
    {
        "id": "throne",
        "label": "Throne",
        "user_agent": "Throne/1.0.4",
        "family": "Sing-box JSON",
        "forces_format": True,
        "hint": "Продолжение NekoBox",
    },
    {
        "id": "browser",
        "label": "Браузер",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
        "family": "HTML-страница",
        "forces_format": True,
        "hint": "Панель отдаст человекочитаемую страницу, а не подписку",
    },
]

#: Device identity sent alongside the User-Agent. The panel uses it to tell
#: devices apart in its list, so a believable pair matters.
DEVICE_PRESETS: list[dict[str, str]] = [
    {"id": "pixel9", "label": "Google Pixel 9", "os": "Android", "ver": "15", "model": "Pixel 9"},
    {
        "id": "pixel9pro",
        "label": "Google Pixel 9 Pro",
        "os": "Android",
        "ver": "15",
        "model": "Pixel 9 Pro",
    },
    {
        "id": "galaxys24",
        "label": "Samsung Galaxy S24 Ultra",
        "os": "Android",
        "ver": "14",
        "model": "SM-S928B",
    },
    {"id": "xiaomi14", "label": "Xiaomi 14", "os": "Android", "ver": "14", "model": "23127PN0CG"},
    {
        "id": "iphone16pro",
        "label": "iPhone 16 Pro",
        "os": "iOS",
        "ver": "18.5",
        "model": "iPhone 16 Pro",
    },
    {
        "id": "iphone14promax",
        "label": "iPhone 14 Pro Max",
        "os": "iOS",
        "ver": "18.3",
        "model": "iPhone 14 Pro Max",
    },
    {
        "id": "ipadpro",
        "label": "iPad Pro 11″",
        "os": "iPadOS",
        "ver": "18.5",
        "model": "iPad Pro 11",
    },
    {"id": "windows11", "label": "ПК на Windows 11", "os": "Windows", "ver": "11", "model": "PC"},
    {"id": "macos", "label": "Mac", "os": "macOS", "ver": "15.5", "model": "MacBook Pro"},
    {"id": "none", "label": "Не отправлять данные устройства", "os": "", "ver": "", "model": ""},
]

#: What a brand-new profile looks like. Mimicry is on by default — that is the
#: point of this app — but it is *device* mimicry: the panel is told it is talking
#: to a Pixel 9 with our HWID, while the client's own User-Agent goes through
#: untouched so the panel still answers in a format that client can actually read.
DEFAULT_CLIENT_PRESET = "passthrough"
DEFAULT_DEVICE_PRESET = "pixel9"


def _preset(presets: list[dict[str, Any]], preset_id: str) -> dict[str, Any]:
    return next(item for item in presets if item["id"] == preset_id)


def default_profile_fields() -> dict[str, Any]:
    client = _preset(CLIENT_PRESETS, DEFAULT_CLIENT_PRESET)
    device = _preset(DEVICE_PRESETS, DEFAULT_DEVICE_PRESET)
    return {
        "hwid_mode": "override",
        "upstream_ua": str(client["user_agent"]),
        "client_preset": str(client["id"]),
        "device_preset": str(device["id"]),
        "device_os": str(device["os"]),
        "device_ver": str(device["ver"]),
        "device_model": str(device["model"]),
    }


class ProfileError(ValueError):
    pass


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def unique_name(
    name: str,
    taken: set[str],
    *,
    marker: str,
    fallback: str = "Профиль",
    keep_base: bool = False,
) -> str:
    """«Имя (marker)», «Имя (marker 2)»… — first spelling not already taken.

    A marker already present in the name is stripped first, so copying a copy
    yields «Имя (копия 2)» rather than «Имя (копия) (копия)». With ``keep_base``
    the name itself is used when free — import wants that, cloning does not.
    """
    base = re.sub(rf"\s*\({re.escape(marker)}(?:\s+\d+)?\)$", "", name).strip() or fallback
    if keep_base and base not in taken:
        return base[:120]
    candidate = f"{base} ({marker})"
    index = 2
    while candidate in taken:
        candidate = f"{base} ({marker} {index})"
        index += 1
    return candidate[:120]


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


def validate_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
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


#: Columns written from a validated payload — the keys `validate_profile_payload`
#: returns, in one place so INSERT and UPDATE can never drift apart.
_PAYLOAD_COLUMNS = (
    "name",
    "upstream_url",
    "enabled",
    "hwid_mode",
    "hwid",
    "device_os",
    "device_ver",
    "device_model",
    "filter_json",
    "protocols_json",
    "output_format",
    "upstream_ua",
    "cache_ttl",
)


def _payload_values(fields: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        int(fields[column]) if column == "enabled" else fields[column]
        for column in _PAYLOAD_COLUMNS
    )


class ProfileRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Profile]:
        rows = self.db.query("SELECT * FROM profiles WHERE deleted_at IS NULL ORDER BY id DESC")
        return [Profile.from_row(row) for row in rows]

    def get(self, profile_id: int, *, include_deleted: bool = False) -> Profile | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = self.db.query_one(f"SELECT * FROM profiles WHERE id = ?{clause}", (profile_id,))
        return Profile.from_row(row) if row else None

    def get_by_token(self, token: str) -> Profile | None:
        row = self.db.query_one(
            "SELECT * FROM profiles WHERE token = ? AND deleted_at IS NULL", (token,)
        )
        return Profile.from_row(row) if row else None

    def create(self, payload: dict[str, Any]) -> Profile:
        fields = validate_profile_payload(payload)
        now = int(time.time())
        token = str(payload.get("token") or "").strip() or new_token()
        # A caller-supplied token (import, config editor) may already belong to a
        # row — including a soft-deleted one. Falling back to a fresh token beats
        # failing the whole import over a link that was going to change anyway.
        if self.db.query_one("SELECT 1 FROM profiles WHERE token = ?", (token,)) is not None:
            token = new_token()
        cursor = self.db.execute(
            f"INSERT INTO profiles({', '.join(_PAYLOAD_COLUMNS)}, token, created_at, updated_at) "
            f"VALUES({','.join('?' * (len(_PAYLOAD_COLUMNS) + 3))})",
            (*_payload_values(fields), token, now, now),
        )
        created = self.get(int(cursor.lastrowid or 0))
        if created is None:  # pragma: no cover - only on a storage failure
            raise ProfileError("не удалось создать профиль")
        return created

    def update(self, profile_id: int, payload: dict[str, Any]) -> Profile:
        existing = self.get(profile_id)
        if existing is None:
            raise ProfileError("профиль не найден")
        fields = validate_profile_payload(payload)
        assignments = ", ".join(f"{column}=?" for column in _PAYLOAD_COLUMNS)
        self.db.execute(
            f"UPDATE profiles SET {assignments}, updated_at=? WHERE id=?",
            (*_payload_values(fields), int(time.time()), profile_id),
        )
        updated = self.get(profile_id)
        assert updated is not None
        return updated

    def delete(self, profile_id: int) -> bool:
        """Soft delete: the link stops working at once, but Undo can still bring it back.

        The row is purged for real by the maintenance pass once the undo window has
        long expired.
        """
        cursor = self.db.execute(
            "UPDATE profiles SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            (int(time.time()), profile_id),
        )
        return cursor.rowcount > 0

    def restore(self, profile_id: int) -> Profile:
        cursor = self.db.execute(
            "UPDATE profiles SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
            (profile_id,),
        )
        if cursor.rowcount == 0:
            raise ProfileError("профиль уже удалён окончательно")
        profile = self.get(profile_id)
        if profile is None:  # pragma: no cover - only on a storage failure
            raise ProfileError("профиль не найден")
        return profile

    def purge_deleted(self, older_than_seconds: int) -> int:
        # Inclusive, so purge_deleted(0) means "everything deleted so far" rather
        # than silently sparing whatever was deleted in the current second.
        cutoff = int(time.time()) - older_than_seconds
        cursor = self.db.execute(
            "DELETE FROM profiles WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff,)
        )
        return cursor.rowcount

    def clone(self, profile_id: int) -> Profile:
        source = self.get(profile_id)
        if source is None:
            raise ProfileError("профиль не найден")
        payload = source.as_dict()
        payload["name"] = unique_name(source.name, {p.name for p in self.list()}, marker="копия")
        payload.pop("token", None)
        return self.create(payload)

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
