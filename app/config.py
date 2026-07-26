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


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass(slots=True)
class Settings:
    """Everything tunable without touching the database."""

    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    db_path: Path = field(init=False)
    generated_password: bool = field(default=False, init=False)
    #: Configuration problems worth shouting about at startup. Collected rather
    #: than logged here so that constructing Settings stays free of side effects.
    warnings: list[str] = field(default_factory=list, init=False)

    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))

    # Hides the password form and switches the endpoint off, for installations
    # that sign in through the identity provider only. Named after Grafana's
    # auth.disable_login_form, and refused when there is no other way in.
    disable_login_form: bool = field(
        default_factory=lambda: _env_bool("AUTH_DISABLE_LOGIN_FORM", False)
    )

    # Skips the admin login entirely. Meant for public demo instances; anyone who
    # can reach the app can then change anything in it.
    demo_mode: bool = field(default_factory=lambda: _env_bool("DEMO_MODE", False))

    # ------------------------------------------------------------------- OIDC
    oidc_issuer: str = field(default_factory=lambda: _env_str("OIDC_ISSUER").rstrip("/"))
    oidc_client_id: str = field(default_factory=lambda: _env_str("OIDC_CLIENT_ID"))
    oidc_client_secret: str = field(default_factory=lambda: _env_str("OIDC_CLIENT_SECRET"))
    oidc_scopes: str = field(
        default_factory=lambda: _env_str("OIDC_SCOPES", "openid profile email groups")
    )
    #: Claim carrying group membership. Keycloak group mappers usually write
    #: `groups`; some realms use `roles` instead.
    oidc_groups_claim: str = field(default_factory=lambda: _env_str("OIDC_GROUPS_CLAIM", "groups"))
    oidc_admin_group: str = field(default_factory=lambda: _env_str("OIDC_ADMIN_GROUP"))
    oidc_viewer_group: str = field(default_factory=lambda: _env_str("OIDC_VIEWER_GROUP"))
    #: Overrides the redirect URI. Only needed when the app cannot see its own
    #: public address, e.g. behind a proxy that rewrites the Host header.
    oidc_redirect_url: str = field(default_factory=lambda: _env_str("OIDC_REDIRECT_URL"))
    #: Sends anyone who is not signed in straight to the provider, with no login
    #: screen in between. Grafana calls this auth.oauth_auto_login.
    oidc_auto_login: bool = field(default_factory=lambda: _env_bool("OIDC_AUTO_LOGIN", False))
    #: Name shown on the sign-in button.
    oidc_display_name: str = field(default_factory=lambda: _env_str("OIDC_DISPLAY_NAME", "OIDC"))
    oidc_verify_tls: bool = field(default_factory=lambda: _env_bool("OIDC_VERIFY_TLS", True))
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

    #: Verbosity of the application's own log. INFO carries the startup checks —
    #: the identity provider answering, templates being seeded.
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO").upper())

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

        if self.oidc_enabled and not (self.oidc_admin_group or self.oidc_viewer_group):
            self.warnings.append(
                "OIDC настроен, но не заданы OIDC_ADMIN_GROUP и OIDC_VIEWER_GROUP — "
                "войти через провайдера не сможет никто, роль будет неопределённой"
            )
        if self.disable_login_form and not self.oidc_enabled:
            # Honouring this would leave the instance with no way in at all.
            self.disable_login_form = False
            self.warnings.append(
                "AUTH_DISABLE_LOGIN_FORM=true проигнорирован: OIDC не настроен, "
                "и вход по паролю остался бы единственным — отключать его нечем заменить"
            )
        if self.oidc_auto_login and not self.oidc_enabled:
            self.oidc_auto_login = False
            self.warnings.append("OIDC_AUTO_LOGIN=true проигнорирован: OIDC не настроен")

    # ------------------------------------------------------------- derived

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)

    @property
    def password_login_enabled(self) -> bool:
        """Whether the master password is accepted at all.

        Kept as a property so the endpoint and the UI can never disagree about
        it: both read this one value.
        """
        return not self.disable_login_form

    @property
    def oidc_group_map(self) -> dict[str, str]:
        """Group name from the token to the role it grants."""
        mapping: dict[str, str] = {}
        if self.oidc_viewer_group:
            mapping[self.oidc_viewer_group] = "viewer"
        # Admin last: if one group is listed as both, the stronger role wins.
        if self.oidc_admin_group:
            mapping[self.oidc_admin_group] = "admin"
        return mapping


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
