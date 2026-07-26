"""Sign-in through an OpenID Connect provider.

Authorization code flow with PKCE. The pending login lives in SQLite rather than
in memory so that a restart between the redirect and the callback — or a second
worker answering it — does not turn into an unexplainable "state mismatch".

Group membership from the token decides the role. A token that carries no known
group is refused *with the groups it did carry*, because the alternative — a
blank "access denied" — is what turns a one-line mapper typo into an evening of
guesswork.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from .config import Settings
from .db import Database

logger = logging.getLogger("subremuxer.oidc")

#: How long a started login may take to come back. Generous enough for a
#: password manager and an MFA prompt, short enough to be self-cleaning.
LOGIN_TTL_SECONDS = 600

#: Discovery and JWKS are stable documents; re-reading them on every login would
#: add a round trip to the provider for nothing.
METADATA_TTL_SECONDS = 3600


class OIDCError(Exception):
    """A login that cannot be completed, with enough context to fix the cause."""

    def __init__(self, message: str, *, detail: str = "", groups: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.groups = groups


@dataclass(slots=True)
class Identity:
    """Who signed in, as far as the provider is concerned."""

    subject: str
    role: str
    display_name: str = ""
    email: str = ""
    groups: list[str] = field(default_factory=list)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def normalize_group(name: str) -> str:
    """Compare group names the way a human reads them.

    Keycloak's group-membership mapper emits full paths (`/subremuxer_admins`,
    or `/apps/subremuxer_admins` for a nested group), while the value an
    administrator types into OIDC_ADMIN_GROUP is the bare name. Matching on the
    last path segment makes both spellings work.
    """
    return name.strip().strip("/").rsplit("/", maxsplit=1)[-1].casefold()


class OIDCProvider:
    """Everything the app needs from the identity provider, and nothing more."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            verify=settings.oidc_verify_tls,
            follow_redirects=True,
        )
        self._metadata: dict[str, Any] | None = None
        self._metadata_at: float = 0.0
        self._jwks: dict[str, Any] | None = None
        self._jwks_at: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- provider

    @property
    def discovery_url(self) -> str:
        return f"{self.settings.oidc_issuer}/.well-known/openid-configuration"

    async def metadata(self, *, force: bool = False) -> dict[str, Any]:
        fresh = time.monotonic() - self._metadata_at < METADATA_TTL_SECONDS
        if self._metadata is not None and fresh and not force:
            return self._metadata
        try:
            response = await self._client.get(self.discovery_url)
            response.raise_for_status()
            document = response.json()
        except httpx.HTTPError as exc:
            raise OIDCError(
                "Провайдер OIDC недоступен",
                detail=f"{self.discovery_url}: {exc}",
            ) from exc
        except ValueError as exc:
            raise OIDCError(
                "Провайдер OIDC вернул не JSON",
                detail=self.discovery_url,
            ) from exc

        for required in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not document.get(required):
                raise OIDCError(
                    "Ответ провайдера неполон",
                    detail=f"в {self.discovery_url} нет поля {required}",
                )
        self._metadata, self._metadata_at = document, time.monotonic()
        return document

    async def self_check(self) -> str:
        """Read discovery once at startup so a broken provider shows up in the log.

        Finding out that the realm URL has a typo when you open the login page is
        far worse than finding out in `docker logs` a second after the container
        starts.
        """
        document = await self.metadata(force=True)
        return str(document.get("issuer") or self.settings.oidc_issuer)

    async def _signing_key(self, token: str) -> Any:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        for attempt in range(2):
            document = await self._jwks_document(force=attempt > 0)
            for key in document.get("keys", []):
                if kid is None or key.get("kid") == kid:
                    return jwt.PyJWK(key).key
        raise OIDCError(
            "Не найден ключ для проверки подписи токена",
            detail=f"kid={kid!r}",
        )

    async def _jwks_document(self, *, force: bool = False) -> dict[str, Any]:
        fresh = time.monotonic() - self._jwks_at < METADATA_TTL_SECONDS
        if self._jwks is not None and fresh and not force:
            return self._jwks
        metadata = await self.metadata()
        try:
            response = await self._client.get(str(metadata["jwks_uri"]))
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("Не удалось получить ключи провайдера", detail=str(exc)) from exc
        self._jwks, self._jwks_at = document, time.monotonic()
        return document

    # ---------------------------------------------------------------- login

    async def begin(self, db: Database, redirect_uri: str, next_url: str) -> str:
        """Register a pending login and return the URL to send the browser to."""
        metadata = await self.metadata()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())

        db.start_oidc_login(state, nonce, verifier, redirect_uri, next_url, LOGIN_TTL_SECONDS)
        db.purge_oidc_logins()

        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.oidc_client_id,
                "redirect_uri": redirect_uri,
                "scope": self.settings.oidc_scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{metadata['authorization_endpoint']}?{query}"

    async def complete(self, db: Database, code: str, state: str) -> tuple[Identity, str]:
        """Exchange the code and turn the resulting token into an identity."""
        pending = db.take_oidc_login(state)
        if pending is None:
            raise OIDCError(
                "Запрос на вход не найден или устарел",
                detail=(
                    "Такое бывает, если вкладка провисела дольше десяти минут "
                    "или вход начинали в другом браузере. Попробуйте ещё раз."
                ),
            )

        tokens = await self._exchange(code, pending["redirect_uri"], pending["code_verifier"])
        id_token = tokens.get("id_token")
        if not id_token:
            raise OIDCError(
                "Провайдер не вернул id_token",
                detail="Проверьте, что клиенту разрешён scope openid",
            )

        claims = await self._verify(id_token, pending["nonce"])
        identity = self._identity(claims)
        return identity, str(pending["next_url"] or "/")

    async def _exchange(self, code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
        metadata = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.settings.oidc_client_id,
            "code_verifier": verifier,
        }
        auth: tuple[str, str] | None = None
        if self.settings.oidc_client_secret:
            auth = (self.settings.oidc_client_id, self.settings.oidc_client_secret)

        try:
            response = await self._client.post(
                str(metadata["token_endpoint"]), data=data, auth=auth
            )
        except httpx.HTTPError as exc:
            raise OIDCError("Не удалось обменять код на токен", detail=str(exc)) from exc

        if response.status_code >= 400:
            #: The provider's own error text is the useful part here — it names
            #: the mismatched redirect URI or the wrong secret outright.
            detail = response.text[:400]
            raise OIDCError(
                f"Провайдер отклонил обмен кода ({response.status_code})", detail=detail
            )
        try:
            return response.json()
        except ValueError as exc:
            raise OIDCError("Ответ token endpoint не является JSON") from exc

    async def _verify(self, id_token: str, nonce: str) -> dict[str, Any]:
        key = await self._signing_key(id_token)
        header = jwt.get_unverified_header(id_token)
        try:
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=[header.get("alg", "RS256")],
                audience=self.settings.oidc_client_id,
                issuer=self.settings.oidc_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=30,
            )
        except jwt.InvalidTokenError as exc:
            raise OIDCError("Токен не прошёл проверку", detail=str(exc)) from exc

        if nonce and claims.get("nonce") != nonce:
            # Without this a token minted for another login attempt would be
            # accepted here.
            raise OIDCError("Не совпал nonce токена")
        return claims

    # ----------------------------------------------------------------- role

    def _identity(self, claims: dict[str, Any]) -> Identity:
        settings = self.settings
        raw = claims.get(settings.oidc_groups_claim)
        if isinstance(raw, str):
            groups = [part for part in raw.replace(",", " ").split() if part]
        elif isinstance(raw, list):
            groups = [str(item) for item in raw]
        else:
            groups = []

        wanted = {normalize_group(name): role for name, role in settings.oidc_group_map.items()}
        roles = {wanted[key] for key in map(normalize_group, groups) if key in wanted}
        role = "admin" if "admin" in roles else ("viewer" if "viewer" in roles else "")

        if not role:
            raise OIDCError(
                "Учётной записи не назначена роль в этом приложении",
                detail=(
                    f"Claim «{settings.oidc_groups_claim}» проверен на группы "
                    f"{settings.oidc_admin_group or '—'} (админ) и "
                    f"{settings.oidc_viewer_group or '—'} (читатель)."
                ),
                groups=groups,
            )

        return Identity(
            subject=str(claims.get("sub") or ""),
            role=role,
            display_name=str(
                claims.get("name") or claims.get("preferred_username") or claims.get("email") or ""
            ),
            email=str(claims.get("email") or ""),
            groups=groups,
        )
