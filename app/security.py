"""Who is signed in, how, and what that lets them do.

Two roles. An **admin** may see and change everything. A **viewer** may see the
subscription links to hand to a client and nothing else — not the upstream URL
behind them, not the logs, not the filters. That restriction is enforced here
and in the API, never in the interface alone: hiding a button is a matter of
tidiness, not of access control.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from .config import Settings
from .db import Database

SESSION_COOKIE = "subremuxer_session"

ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_ADMIN, ROLE_VIEWER)

MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 300


@dataclass(slots=True)
class LoginThrottle:
    """Counts failed logins per client address inside a rolling window."""

    attempts: dict[str, list[float]] = field(default_factory=dict)

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.attempts.get(key, []) if now - t < ATTEMPT_WINDOW]
        self.attempts[key] = recent
        return len(recent) >= MAX_ATTEMPTS

    def record_failure(self, key: str) -> None:
        self.attempts.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self.attempts.pop(key, None)


@dataclass(slots=True)
class Identity:
    """The signed-in user, as the request handlers see them."""

    role: str
    method: str = "password"
    subject: str = ""
    display_name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def check_password(settings: Settings, candidate: str) -> bool:
    if not settings.password_login_enabled:
        return False
    return hmac.compare_digest(settings.admin_password.encode(), candidate.encode())


def issue_session(
    db: Database,
    settings: Settings,
    ip: str | None,
    ua: str | None,
    *,
    role: str = ROLE_ADMIN,
    method: str = "password",
    subject: str = "",
    display_name: str = "",
) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(
        token,
        settings.session_ttl_hours * 3600,
        ip,
        ua,
        role=role,
        method=method,
        subject=subject or None,
        display_name=display_name or None,
    )
    return token


def client_ip(request: Request, settings: Settings) -> str | None:
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else None


def current_identity(request: Request) -> Identity | None:
    """The identity behind this request, or None when nobody is signed in."""
    if request.app.state.settings.demo_mode:
        return Identity(role=ROLE_ADMIN, method="demo", display_name="Демо")
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    db: Database = request.app.state.db
    row = db.get_session(token)
    if row is None:
        return None
    role = row["role"] if row["role"] in ROLES else ROLE_ADMIN
    return Identity(
        role=role,
        method=row["method"] or "password",
        subject=row["subject"] or "",
        display_name=row["display_name"] or "",
    )


def require_session(request: Request) -> Identity:
    """Any signed-in user, of either role."""
    identity = current_identity(request)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация"
        )
    return identity


def require_admin(request: Request) -> Identity:
    """FastAPI dependency guarding everything a viewer must not reach.

    A viewer gets 403 rather than 401 on purpose: they are signed in correctly,
    so offering them the login screen again would only be confusing.
    """
    identity = require_session(request)
    if not identity.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав: этот раздел доступен только администратору",
        )
    return identity
