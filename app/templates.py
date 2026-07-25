"""Profile templates: reusable presets for everything except the panel URL.

A template answers "how do we talk to the panel and what do we keep", so a new
profile usually only needs a name and a subscription link on top of one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .filtering import CompiledFilter, FilterConfig
from .profiles import CLIENT_PRESETS, DEVICE_PRESETS, OUTPUT_FORMATS, ProfileError
from .upstream import HWID_MODES

#: Fields a template carries over into a new profile. The panel URL and the
#: profile name are deliberately not among them.
TEMPLATE_FIELDS = (
    "hwid_mode",
    "hwid",
    "device_os",
    "device_ver",
    "device_model",
    "upstream_ua",
    "filter",
    "protocols",
    "output_format",
    "cache_ttl",
)


class TemplateError(ValueError):
    pass


def _client(preset_id: str) -> str:
    return str(next(item["user_agent"] for item in CLIENT_PRESETS if item["id"] == preset_id))


def _device(preset_id: str) -> dict[str, str]:
    preset = next(item for item in DEVICE_PRESETS if item["id"] == preset_id)
    return {
        "device_os": preset["os"],
        "device_ver": preset["ver"],
        "device_model": preset["model"],
    }


def _payload(
    *,
    client: str,
    device: str,
    hwid_mode: str = "override",
    conditions: list[dict[str, str]] | None = None,
    match: str = "all",
    protocols: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "hwid_mode": hwid_mode,
        "hwid": "",
        "upstream_ua": _client(client),
        **_device(device),
        "filter": {
            "mode": "builder",
            "match": match,
            "case_sensitive": False,
            "conditions": conditions or [],
            "include_regex": "",
            "exclude_regex": "",
        },
        "protocols": protocols or [],
        "output_format": "auto",
        "cache_ttl": 0,
    }


#: Shipped with the app, seeded on first start, editable and deletable afterwards.
BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "builtin_id": "happ_pixel9",
        "name": "Мимикрия устройства (Pixel 9)",
        "description": (
            "Основной сценарий: клиент без поддержки HWID прячется за Pixel 9 с нашим "
            "HWID. User-Agent клиента идёт наверх как есть, поэтому панель отвечает "
            "форматом, который этот клиент умеет читать."
        ),
        "sort_order": 10,
        "payload": _payload(client="passthrough", device="pixel9"),
    },
    {
        "builtin_id": "happ_iphone",
        "name": "Мимикрия устройства (iPhone 16 Pro)",
        "description": "То же самое, но панель видит iOS-устройство.",
        "sort_order": 20,
        "payload": _payload(client="passthrough", device="iphone16pro"),
    },
    {
        "builtin_id": "happ_no_ru",
        "name": "Pixel 9 + только зарубежные",
        "description": "Мимикрия устройства и отсев всего, что содержит RU в названии.",
        "sort_order": 30,
        "payload": _payload(
            client="passthrough",
            device="pixel9",
            conditions=[{"op": "not_contains", "value": "RU"}],
        ),
    },
    {
        "builtin_id": "happ_lte_no_ru",
        "name": "Pixel 9 + мобильные каналы",
        "description": "Мимикрия устройства и фильтр «содержит LTE и не содержит RU».",
        "sort_order": 40,
        "payload": _payload(
            client="passthrough",
            device="pixel9",
            conditions=[
                {"op": "contains", "value": "LTE"},
                {"op": "not_contains", "value": "RU"},
            ],
        ),
    },
    {
        "builtin_id": "happ_forced",
        "name": "Заставить панель отдать Xray JSON (Happ)",
        "description": (
            "Панель выбирает формат по User-Agent. Этот шаблон представляется Happ, "
            "поэтому всегда получает Xray JSON — берите его, только если ваш клиент "
            "этот формат читает."
        ),
        "sort_order": 50,
        "payload": _payload(client="happ", device="pixel9"),
    },
    {
        "builtin_id": "singbox_pixel9",
        "name": "Заставить панель отдать sing-box",
        "description": "То же для sing-box JSON, каким бы ни был настоящий клиент.",
        "sort_order": 60,
        "payload": _payload(client="singbox", device="pixel9"),
    },
    {
        "builtin_id": "mihomo_desktop",
        "name": "Заставить панель отдать Clash / Mihomo",
        "description": "То же для YAML-формата: панель видит Clash Verge на ПК с Windows 11.",
        "sort_order": 70,
        "payload": _payload(client="mihomo", device="windows11"),
    },
    {
        "builtin_id": "passthrough",
        "name": "Без мимикрии вообще",
        "description": (
            "Чистое проксирование: HWID клиента не трогается, заголовки уходят наверх "
            "как есть, фильтра нет. Удобно как отправная точка."
        ),
        "sort_order": 80,
        "payload": _payload(client="passthrough", device="none", hwid_mode="passthrough"),
    },
]


@dataclass(slots=True)
class ProfileTemplate:
    id: int
    name: str
    description: str = ""
    builtin_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    sort_order: int = 100
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ProfileTemplate:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            builtin_id=row["builtin_id"],
            payload=payload,
            sort_order=row["sort_order"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "builtin_id": self.builtin_id,
            "payload": self.payload,
            "sort_order": self.sort_order,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def validate_payload(raw: Any) -> dict[str, Any]:
    """Keep only known fields, and refuse anything the profile layer would reject."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TemplateError("payload шаблона должен быть объектом")

    payload = {key: raw[key] for key in TEMPLATE_FIELDS if key in raw}

    hwid_mode = str(payload.get("hwid_mode", "override"))
    if hwid_mode not in HWID_MODES:
        raise TemplateError(f"неизвестный режим HWID: {hwid_mode}")
    payload["hwid_mode"] = hwid_mode

    output_format = str(payload.get("output_format", "auto"))
    if output_format not in OUTPUT_FORMATS:
        raise TemplateError(f"неизвестный формат вывода: {output_format}")
    payload["output_format"] = output_format

    protocols = payload.get("protocols") or []
    if not isinstance(protocols, list):
        raise TemplateError("protocols должен быть списком")

    cache_ttl = int(payload.get("cache_ttl") or 0)
    if cache_ttl < 0 or cache_ttl > 86400:
        raise TemplateError("TTL кэша должен быть от 0 до 86400 секунд")
    payload["cache_ttl"] = cache_ttl

    config = FilterConfig.from_dict(payload.get("filter"))
    CompiledFilter.build(config, [str(item) for item in protocols])
    payload["filter"] = config.as_dict()

    for key in ("hwid", "device_os", "device_ver", "device_model", "upstream_ua"):
        payload[key] = str(payload.get(key) or "").strip()

    return payload


class TemplateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[ProfileTemplate]:
        rows = self.db.query("SELECT * FROM profile_templates ORDER BY sort_order, id")
        return [ProfileTemplate.from_row(row) for row in rows]

    def get(self, template_id: int) -> ProfileTemplate | None:
        row = self.db.query_one("SELECT * FROM profile_templates WHERE id = ?", (template_id,))
        return ProfileTemplate.from_row(row) if row else None

    def create(self, data: dict[str, Any]) -> ProfileTemplate:
        name = str(data.get("name", "")).strip()
        if not name:
            raise TemplateError("укажите название шаблона")
        if len(name) > 120:
            raise TemplateError("название слишком длинное")
        description = str(data.get("description", "")).strip()[:400]
        payload = validate_payload(data.get("payload"))
        now = int(time.time())
        cursor = self.db.execute(
            "INSERT INTO profile_templates(name, description, builtin_id, payload_json, "
            "sort_order, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (
                name,
                description,
                data.get("builtin_id"),
                json.dumps(payload, ensure_ascii=False),
                int(data.get("sort_order") or 100),
                now,
                now,
            ),
        )
        created = self.get(int(cursor.lastrowid or 0))
        if created is None:  # pragma: no cover - only on a storage failure
            raise TemplateError("не удалось создать шаблон")
        return created

    def update(self, template_id: int, data: dict[str, Any]) -> ProfileTemplate:
        existing = self.get(template_id)
        if existing is None:
            raise TemplateError("шаблон не найден")
        name = str(data.get("name", existing.name)).strip()
        if not name:
            raise TemplateError("укажите название шаблона")
        description = str(data.get("description", existing.description)).strip()[:400]
        payload = validate_payload(data.get("payload", existing.payload))
        self.db.execute(
            "UPDATE profile_templates SET name = ?, description = ?, payload_json = ?, "
            "updated_at = ? WHERE id = ?",
            (
                name,
                description,
                json.dumps(payload, ensure_ascii=False),
                int(time.time()),
                template_id,
            ),
        )
        updated = self.get(template_id)
        assert updated is not None
        return updated

    def delete(self, template_id: int) -> bool:
        cursor = self.db.execute("DELETE FROM profile_templates WHERE id = ?", (template_id,))
        return cursor.rowcount > 0

    def from_profile(
        self, profile_dict: dict[str, Any], name: str, description: str = ""
    ) -> ProfileTemplate:
        payload = {key: profile_dict.get(key) for key in TEMPLATE_FIELDS if key in profile_dict}
        return self.create({"name": name, "description": description, "payload": payload})

    # ------------------------------------------------------------- built-ins

    def seed_builtins(self) -> int:
        """Insert any built-in that is not present yet. Never overwrites edits."""
        known = {
            row["builtin_id"]
            for row in self.db.query(
                "SELECT builtin_id FROM profile_templates WHERE builtin_id IS NOT NULL"
            )
        }
        seeded = 0
        for template in BUILTIN_TEMPLATES:
            if template["builtin_id"] in known:
                continue
            self.create(template)
            seeded += 1
        return seeded

    def restore_builtins(self) -> int:
        """Bring the shipped templates back to their original state."""
        restored = 0
        for template in BUILTIN_TEMPLATES:
            row = self.db.query_one(
                "SELECT id FROM profile_templates WHERE builtin_id = ?", (template["builtin_id"],)
            )
            if row is None:
                self.create(template)
            else:
                self.update(int(row["id"]), template)
            restored += 1
        return restored


def apply_template(template: ProfileTemplate, base: dict[str, Any]) -> dict[str, Any]:
    """Overlay a template onto a profile payload, keeping the caller's own fields."""
    merged = dict(template.payload)
    merged.update({key: value for key, value in base.items() if value not in (None, "")})
    if not merged.get("name"):
        raise ProfileError("укажите название профиля")
    return merged
