"""Admin authentication: one password, server-side sessions, basic brute-force damping."""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from .config import Settings
from .db import Database

SESSION_COOKIE = "subremuxer_session"

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


def check_password(settings: Settings, candidate: str) -> bool:
    return hmac.compare_digest(settings.admin_password.encode(), candidate.encode())


def issue_session(db: Database, settings: Settings, ip: str | None, ua: str | None) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(token, settings.session_ttl_hours * 3600, ip, ua)
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


def require_admin(request: Request) -> None:
    """FastAPI dependency guarding every admin endpoint."""
    db: Database = request.app.state.db
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not db.session_valid(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация"
        )
