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

SCHEMA_VERSION = 4

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
    token        TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    user_agent   TEXT,
    ip           TEXT,
    role         TEXT NOT NULL DEFAULT 'admin',
    method       TEXT NOT NULL DEFAULT 'password',
    subject      TEXT,
    display_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- A login that has been started but not yet come back from the provider.
CREATE TABLE IF NOT EXISTS oidc_logins (
    state         TEXT PRIMARY KEY,
    nonce         TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_uri  TEXT NOT NULL,
    next_url      TEXT NOT NULL DEFAULT '/',
    expires_at    INTEGER NOT NULL
);

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
    updated_at     INTEGER NOT NULL,
    deleted_at     INTEGER
);

-- Several profiles republished under one link. The sources keep their own
-- filters, HWID and mimicry — a row here only says which ones to combine and
-- how to label the result.
CREATE TABLE IF NOT EXISTS aggregates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    token         TEXT    NOT NULL UNIQUE,
    enabled       INTEGER NOT NULL DEFAULT 1,
    sources_json  TEXT    NOT NULL DEFAULT '[]',
    prefix_names  INTEGER NOT NULL DEFAULT 1,
    dedupe        INTEGER NOT NULL DEFAULT 1,
    output_format TEXT    NOT NULL DEFAULT 'auto',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    deleted_at    INTEGER
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

CREATE TABLE IF NOT EXISTS probe_captures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    first_ts     INTEGER NOT NULL,
    last_ts      INTEGER NOT NULL,
    seen_count   INTEGER NOT NULL DEFAULT 1,
    client_ip    TEXT,
    user_agent   TEXT NOT NULL DEFAULT '',
    hwid         TEXT NOT NULL DEFAULT '',
    device_os    TEXT NOT NULL DEFAULT '',
    device_ver   TEXT NOT NULL DEFAULT '',
    device_model TEXT NOT NULL DEFAULT '',
    headers_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_probe_last_ts ON probe_captures(last_ts DESC);

CREATE TABLE IF NOT EXISTS profile_templates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    builtin_id   TEXT,
    payload_json TEXT    NOT NULL DEFAULT '{}',
    sort_order   INTEGER NOT NULL DEFAULT 100,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_templates_builtin ON profile_templates(builtin_id)
    WHERE builtin_id IS NOT NULL;
"""

#: Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
#: EXISTS", so upgrades are applied by inspecting the table.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("profiles", "deleted_at", "INTEGER"),
    # Sessions predate roles: anything already signed in keeps full access,
    # which is right — before this release the only way in was the master
    # password, and that is an administrator.
    ("sessions", "role", "TEXT NOT NULL DEFAULT 'admin'"),
    ("sessions", "method", "TEXT NOT NULL DEFAULT 'password'"),
    ("sessions", "subject", "TEXT"),
    ("sessions", "display_name", "TEXT"),
)


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
            for table, column, decl in _ADDED_COLUMNS:
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
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

    def create_session(
        self,
        token: str,
        ttl_seconds: int,
        ip: str | None,
        ua: str | None,
        *,
        role: str = "admin",
        method: str = "password",
        subject: str | None = None,
        display_name: str | None = None,
    ) -> None:
        now = int(time.time())
        self.execute(
            "INSERT INTO sessions(token, created_at, expires_at, user_agent, ip, "
            "role, method, subject, display_name) VALUES(?,?,?,?,?,?,?,?,?)",
            (token, now, now + ttl_seconds, ua, ip, role, method, subject, display_name),
        )

    def get_session(self, token: str) -> sqlite3.Row | None:
        """The session behind a cookie, or None if it is unknown or expired."""
        row = self.query_one("SELECT * FROM sessions WHERE token = ?", (token,))
        if row is None:
            return None
        if row["expires_at"] < int(time.time()):
            self.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        return row

    def delete_session(self, token: str) -> None:
        self.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def purge_sessions(self) -> None:
        now = int(time.time())
        self.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self.execute("DELETE FROM oidc_logins WHERE expires_at < ?", (now,))

    # ---------------------------------------------------------- oidc logins

    def start_oidc_login(
        self,
        state: str,
        nonce: str,
        verifier: str,
        redirect_uri: str,
        next_url: str,
        ttl_seconds: int,
    ) -> None:
        self.execute(
            "INSERT INTO oidc_logins(state, nonce, code_verifier, redirect_uri, next_url, "
            "expires_at) VALUES(?,?,?,?,?,?)",
            (state, nonce, verifier, redirect_uri, next_url, int(time.time()) + ttl_seconds),
        )

    def take_oidc_login(self, state: str) -> sqlite3.Row | None:
        """Fetch a pending login and consume it, so a code cannot be replayed."""
        row = self.query_one("SELECT * FROM oidc_logins WHERE state = ?", (state,))
        if row is None:
            return None
        self.execute("DELETE FROM oidc_logins WHERE state = ?", (state,))
        if row["expires_at"] < int(time.time()):
            return None
        return row

    def purge_oidc_logins(self) -> None:
        self.execute("DELETE FROM oidc_logins WHERE expires_at < ?", (int(time.time()),))

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
