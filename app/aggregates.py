"""Aggregates: several profiles republished under a single subscription link.

An aggregate owns nothing but the list of sources and how to label them. Each
source is an ordinary profile and keeps its own upstream URL, HWID mimicry,
filter and protocol list — so the per-source settings a user already knows are
exactly the ones that apply here, and a source can still be tested and served
on its own link.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .profiles import OUTPUT_FORMATS, new_token

#: More than this and the client waits on a dozen panels for every refresh.
MAX_SOURCES = 24

#: Long prefixes eat the node name in a client's server list.
MAX_PREFIX = 40


class AggregateError(ValueError):
    pass


@dataclass(slots=True)
class AggregateSource:
    profile_id: int
    #: Shown in front of every node this source contributes. Empty means "use
    #: the profile's own name", which is what the interface offers by default.
    prefix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, "prefix": self.prefix}


@dataclass(slots=True)
class Aggregate:
    id: int
    name: str
    token: str
    enabled: bool = True
    sources: list[AggregateSource] = field(default_factory=list)
    prefix_names: bool = True
    dedupe: bool = True
    output_format: str = "auto"
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Aggregate:
        return cls(
            id=row["id"],
            name=row["name"],
            token=row["token"],
            enabled=bool(row["enabled"]),
            sources=[
                AggregateSource(profile_id=int(item["profile_id"]), prefix=str(item["prefix"]))
                for item in json.loads(row["sources_json"] or "[]")
            ],
            prefix_names=bool(row["prefix_names"]),
            dedupe=bool(row["dedupe"]),
            output_format=row["output_format"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "token": self.token,
            "enabled": self.enabled,
            "sources": [source.as_dict() for source in self.sources],
            "prefix_names": self.prefix_names,
            "dedupe": self.dedupe,
            "output_format": self.output_format,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def profile_ids(self) -> list[int]:
        return [source.profile_id for source in self.sources]


def parse_sources(raw: Any) -> list[AggregateSource]:
    """Accept both ``[1, 2]`` and ``[{"profile_id": 1, "prefix": "A"}]``.

    The bare-id spelling is what a hand-written config file wants to say; the
    interface always sends the long one.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AggregateError("список источников должен быть массивом")
    sources: list[AggregateSource] = []
    seen: set[int] = set()
    for item in raw:
        if isinstance(item, dict):
            raw_id = item.get("profile_id", item.get("id"))
            prefix = str(item.get("prefix") or "").strip()
        else:
            raw_id, prefix = item, ""
        try:
            profile_id = int(raw_id)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise AggregateError(f"источник «{item}» не похож на идентификатор профиля") from exc
        if profile_id in seen:
            raise AggregateError("один и тот же профиль указан в сборке дважды")
        if len(prefix) > MAX_PREFIX:
            raise AggregateError(f"префикс длиннее {MAX_PREFIX} символов")
        seen.add(profile_id)
        sources.append(AggregateSource(profile_id=profile_id, prefix=prefix))
    if len(sources) > MAX_SOURCES:
        raise AggregateError(f"в сборке не может быть больше {MAX_SOURCES} источников")
    return sources


def validate_aggregate_payload(
    payload: dict[str, Any], *, known_profile_ids: set[int] | None = None
) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise AggregateError("укажите название сборки")
    if len(name) > 120:
        raise AggregateError("название слишком длинное")

    sources = parse_sources(payload.get("sources"))
    if known_profile_ids is not None:
        missing = [s.profile_id for s in sources if s.profile_id not in known_profile_ids]
        if missing:
            listed = ", ".join(str(item) for item in missing)
            raise AggregateError(f"профиль не найден: {listed}")

    output_format = str(payload.get("output_format", "auto"))
    if output_format not in OUTPUT_FORMATS:
        raise AggregateError(f"неизвестный формат вывода: {output_format}")

    return {
        "name": name,
        "enabled": bool(payload.get("enabled", True)),
        "sources_json": json.dumps(
            [source.as_dict() for source in sources], ensure_ascii=False
        ),
        "prefix_names": bool(payload.get("prefix_names", True)),
        "dedupe": bool(payload.get("dedupe", True)),
        "output_format": output_format,
    }


#: Columns written from a validated payload, in one place so INSERT and UPDATE
#: cannot drift apart.
_PAYLOAD_COLUMNS = (
    "name",
    "enabled",
    "sources_json",
    "prefix_names",
    "dedupe",
    "output_format",
)

_BOOL_COLUMNS = frozenset({"enabled", "prefix_names", "dedupe"})


def _payload_values(fields: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        int(fields[column]) if column in _BOOL_COLUMNS else fields[column]
        for column in _PAYLOAD_COLUMNS
    )


class AggregateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Aggregate]:
        rows = self.db.query("SELECT * FROM aggregates WHERE deleted_at IS NULL ORDER BY id DESC")
        return [Aggregate.from_row(row) for row in rows]

    def get(self, aggregate_id: int, *, include_deleted: bool = False) -> Aggregate | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = self.db.query_one(f"SELECT * FROM aggregates WHERE id = ?{clause}", (aggregate_id,))
        return Aggregate.from_row(row) if row else None

    def get_by_token(self, token: str) -> Aggregate | None:
        row = self.db.query_one(
            "SELECT * FROM aggregates WHERE token = ? AND deleted_at IS NULL", (token,)
        )
        return Aggregate.from_row(row) if row else None

    def _known_profile_ids(self) -> set[int]:
        rows = self.db.query("SELECT id FROM profiles WHERE deleted_at IS NULL")
        return {int(row["id"]) for row in rows}

    def _free_token(self, wanted: str | None) -> str:
        """A token nothing else answers on — profiles and aggregates share /s/."""
        token = (wanted or "").strip() or new_token()
        taken = self.db.query_one(
            "SELECT 1 FROM profiles WHERE token = ? "
            "UNION ALL SELECT 1 FROM aggregates WHERE token = ?",
            (token, token),
        )
        return new_token() if taken is not None else token

    def create(self, payload: dict[str, Any]) -> Aggregate:
        fields = validate_aggregate_payload(payload, known_profile_ids=self._known_profile_ids())
        now = int(time.time())
        token = self._free_token(str(payload.get("token") or ""))
        cursor = self.db.execute(
            f"INSERT INTO aggregates({', '.join(_PAYLOAD_COLUMNS)}, token, created_at, updated_at) "
            f"VALUES({','.join('?' * (len(_PAYLOAD_COLUMNS) + 3))})",
            (*_payload_values(fields), token, now, now),
        )
        created = self.get(int(cursor.lastrowid or 0))
        if created is None:  # pragma: no cover - only on a storage failure
            raise AggregateError("не удалось создать сборку")
        return created

    def update(self, aggregate_id: int, payload: dict[str, Any]) -> Aggregate:
        if self.get(aggregate_id) is None:
            raise AggregateError("сборка не найдена")
        fields = validate_aggregate_payload(payload, known_profile_ids=self._known_profile_ids())
        assignments = ", ".join(f"{column}=?" for column in _PAYLOAD_COLUMNS)
        self.db.execute(
            f"UPDATE aggregates SET {assignments}, updated_at=? WHERE id=?",
            (*_payload_values(fields), int(time.time()), aggregate_id),
        )
        updated = self.get(aggregate_id)
        assert updated is not None
        return updated

    def delete(self, aggregate_id: int) -> bool:
        """Soft delete, so Undo works exactly as it does for a profile."""
        cursor = self.db.execute(
            "UPDATE aggregates SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            (int(time.time()), aggregate_id),
        )
        return cursor.rowcount > 0

    def restore(self, aggregate_id: int) -> Aggregate:
        cursor = self.db.execute(
            "UPDATE aggregates SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
            (aggregate_id,),
        )
        if cursor.rowcount == 0:
            raise AggregateError("сборка уже удалена окончательно")
        aggregate = self.get(aggregate_id)
        if aggregate is None:  # pragma: no cover - only on a storage failure
            raise AggregateError("сборка не найдена")
        return aggregate

    def purge_deleted(self, older_than_seconds: int) -> int:
        cutoff = int(time.time()) - older_than_seconds
        cursor = self.db.execute(
            "DELETE FROM aggregates WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff,)
        )
        return cursor.rowcount

    def rotate_token(self, aggregate_id: int) -> Aggregate:
        self.db.execute(
            "UPDATE aggregates SET token = ?, updated_at = ? WHERE id = ?",
            (self._free_token(None), int(time.time()), aggregate_id),
        )
        aggregate = self.get(aggregate_id)
        if aggregate is None:
            raise AggregateError("сборка не найдена")
        return aggregate

    def using_profile(self, profile_id: int) -> list[Aggregate]:
        """Which aggregates would lose a source if this profile went away."""
        return [item for item in self.list() if profile_id in item.profile_ids()]
