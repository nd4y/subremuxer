"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    """Everything tunable without touching the database."""

    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    db_path: Path = field(init=False)
    generated_password: bool = field(default=False, init=False)

    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))

    # Skips the admin login entirely. Meant for public demo instances; anyone who
    # can reach the app can then change anything in it.
    demo_mode: bool = field(default_factory=lambda: _env_bool("DEMO_MODE", False))
    session_ttl_hours: int = field(default_factory=lambda: _env_int("SESSION_TTL_HOURS", 24 * 14))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", False))

    # Public base URL used when building subscription links shown in the admin UI.
    # Empty means "derive it from the incoming request".
    public_base_url: str = field(
        default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    )

    # Upstream fetching
    upstream_timeout: float = field(
        default_factory=lambda: float(os.getenv("UPSTREAM_TIMEOUT", "20"))
    )
    upstream_max_bytes: int = field(
        default_factory=lambda: _env_int("UPSTREAM_MAX_BYTES", 8 * 1024 * 1024)
    )
    upstream_proxy: str | None = field(default_factory=lambda: os.getenv("UPSTREAM_PROXY") or None)
    upstream_verify_tls: bool = field(
        default_factory=lambda: _env_bool("UPSTREAM_VERIFY_TLS", True)
    )

    # Log retention
    log_retention_days: int = field(default_factory=lambda: _env_int("LOG_RETENTION_DAYS", 30))
    log_max_rows: int = field(default_factory=lambda: _env_int("LOG_MAX_ROWS", 20000))

    # Trust X-Forwarded-For when behind a reverse proxy.
    trust_forwarded_for: bool = field(
        default_factory=lambda: _env_bool("TRUST_FORWARDED_FOR", True)
    )

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.db_path = self.data_dir / "subremuxer.db"
        if self.demo_mode:
            # There is nothing to log into, so a generated password would only be
            # noise in the log.
            self.admin_password = self.admin_password or "demo"
            return
        if not self.admin_password:
            # A generated password is printed once at startup so a fresh container is
            # never silently wide open.
            self.admin_password = secrets.token_urlsafe(12)
            self.generated_password = True


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Used by tests to re-read the environment."""
    global _settings
    _settings = None
