"""SQLite storage.

Deliberately synchronous: every statement here touches a local WAL-mode file and
completes in microseconds, so pushing it through a thread pool would cost more
than it saves. All access goes through one connection guarded by a lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    user_agent TEXT,
    ip         TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS profiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    token          TEXT    NOT NULL UNIQUE,
    upstream_url   TEXT    NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    hwid_mode      TEXT    NOT NULL DEFAULT 'override',
    hwid           TEXT,
    device_os      TEXT,
    device_ver     TEXT,
    device_model   TEXT,
    filter_json    TEXT    NOT NULL DEFAULT '{}',
    protocols_json TEXT    NOT NULL DEFAULT '[]',
    output_format  TEXT    NOT NULL DEFAULT 'auto',
    upstream_ua    TEXT,
    cache_ttl      INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS request_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER,
    profile_name    TEXT,
    ts              INTEGER NOT NULL,
    client_ip       TEXT,
    user_agent      TEXT,
    request_path    TEXT,
    hwid_in         TEXT,
    hwid_sent       TEXT,
    hwid_action     TEXT,
    upstream_url    TEXT,
    upstream_status INTEGER,
    upstream_ms     INTEGER,
    detected_format TEXT,
    output_format   TEXT,
    nodes_total     INTEGER NOT NULL DEFAULT 0,
    nodes_kept      INTEGER NOT NULL DEFAULT 0,
    bytes_in        INTEGER NOT NULL DEFAULT 0,
    bytes_out       INTEGER NOT NULL DEFAULT 0,
    status_code     INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON request_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_logs_profile ON request_logs(profile_id, ts DESC);

CREATE TABLE IF NOT EXISTS request_log_nodes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id   INTEGER NOT NULL,
    position INTEGER NOT NULL,
    name     TEXT,
    protocol TEXT,
    kept     INTEGER NOT NULL,
    reason   TEXT,
    FOREIGN KEY(log_id) REFERENCES request_logs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_log_nodes_log ON request_log_nodes(log_id);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.migrate()

    # ------------------------------------------------------------------ core

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.tx() as conn:
            return conn.execute(sql, tuple(params))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def migrate(self) -> None:
        with self.tx() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    # -------------------------------------------------------------- settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def all_settings(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row in self.query("SELECT key, value FROM app_settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        return out

    # -------------------------------------------------------------- sessions

    def create_session(self, token: str, ttl_seconds: int, ip: str | None, ua: str | None) -> None:
        now = int(time.time())
        self.execute(
            "INSERT INTO sessions(token, created_at, expires_at, user_agent, ip) VALUES(?,?,?,?,?)",
            (token, now, now + ttl_seconds, ua, ip),
        )

    def session_valid(self, token: str) -> bool:
        row = self.query_one("SELECT expires_at FROM sessions WHERE token = ?", (token,))
        if row is None:
            return False
        if row["expires_at"] < int(time.time()):
            self.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return False
        return True

    def delete_session(self, token: str) -> None:
        self.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def purge_sessions(self) -> None:
        self.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))

    # ------------------------------------------------------------ log pruning

    def prune_logs(self, retention_days: int, max_rows: int) -> None:
        if retention_days > 0:
            cutoff = int(time.time()) - retention_days * 86400
            self.execute("DELETE FROM request_logs WHERE ts < ?", (cutoff,))
        if max_rows > 0:
            self.execute(
                "DELETE FROM request_logs WHERE id NOT IN "
                "(SELECT id FROM request_logs ORDER BY id DESC LIMIT ?)",
                (max_rows,),
            )
        # request_log_nodes rows are removed by the FK cascade.
