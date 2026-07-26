"""Request logging: who asked for what, what came back, and why each node survived."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from typing import Any

from .db import Database
from .filtering import Decision


@dataclass(slots=True)
class RequestLogEntry:
    profile_id: int | None = None
    profile_name: str | None = None
    ts: int = field(default_factory=lambda: int(time.time()))
    client_ip: str | None = None
    user_agent: str | None = None
    request_path: str | None = None
    hwid_in: str | None = None
    hwid_sent: str | None = None
    hwid_action: str | None = None
    upstream_url: str | None = None
    upstream_status: int | None = None
    upstream_ms: int | None = None
    detected_format: str | None = None
    output_format: str | None = None
    nodes_total: int = 0
    nodes_kept: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    status_code: int | None = None
    error: str | None = None


#: The entry's fields are named after the table's columns on purpose: the INSERT
#: below is derived from the dataclass, so adding a field is a two-place change
#: (here and in the schema) instead of four.
_LOG_COLUMNS = tuple(item.name for item in fields(RequestLogEntry))

_INSERT_LOG = (
    f"INSERT INTO request_logs({', '.join(_LOG_COLUMNS)}) "
    f"VALUES({','.join('?' * len(_LOG_COLUMNS))})"
)


class LogRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, entry: RequestLogEntry, decisions: list[Decision] | None = None) -> int:
        with self.db.tx() as conn:
            cursor = conn.execute(
                _INSERT_LOG, tuple(getattr(entry, name) for name in _LOG_COLUMNS)
            )
            log_id = int(cursor.lastrowid or 0)
            if decisions:
                conn.executemany(
                    "INSERT INTO request_log_nodes(log_id, position, name, protocol, kept, reason)"
                    " VALUES(?,?,?,?,?,?)",
                    [
                        (
                            log_id,
                            index,
                            decision.node.name,
                            decision.node.protocol,
                            int(decision.kept),
                            decision.detail or decision.reason,
                        )
                        for index, decision in enumerate(decisions)
                    ],
                )
        return log_id

    def list(
        self,
        *,
        profile_id: int | None = None,
        limit: int = 50,
        before_id: int | None = None,
        only_errors: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        if before_id is not None:
            clauses.append("id < ?")
            params.append(before_id)
        if only_errors:
            clauses.append("(error IS NOT NULL OR status_code >= 400)")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        rows = self.db.query(
            f"SELECT * FROM request_logs {where} ORDER BY id DESC LIMIT ?", params
        )
        return [dict(row) for row in rows]

    def nodes(self, log_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT position, name, protocol, kept, reason FROM request_log_nodes "
            "WHERE log_id = ? ORDER BY position",
            (log_id,),
        )
        return [
            {
                "position": row["position"],
                "name": row["name"],
                "protocol": row["protocol"],
                "kept": bool(row["kept"]),
                "reason": row["reason"],
            }
            for row in rows
        ]

    def clear(self, profile_id: int | None = None) -> int:
        if profile_id is None:
            cursor = self.db.execute("DELETE FROM request_logs")
        else:
            cursor = self.db.execute("DELETE FROM request_logs WHERE profile_id = ?", (profile_id,))
        return cursor.rowcount

    def stats(self) -> dict[str, Any]:
        day_ago = int(time.time()) - 86400
        row = self.db.query_one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN error IS NOT NULL OR status_code >= 400 THEN 1 ELSE 0 END) AS errors, "
            "SUM(nodes_total) AS nodes_total, SUM(nodes_kept) AS nodes_kept "
            "FROM request_logs WHERE ts >= ?",
            (day_ago,),
        )
        return {
            "requests_24h": (row["total"] if row else 0) or 0,
            "errors_24h": (row["errors"] if row else 0) or 0,
            "nodes_seen_24h": (row["nodes_total"] if row else 0) or 0,
            "nodes_served_24h": (row["nodes_kept"] if row else 0) or 0,
        }
