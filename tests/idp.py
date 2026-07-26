"""A miniature OpenID Connect provider for the tests.

It mints real RS256 tokens with its own key and publishes a matching JWKS, so
the app verifies signatures exactly as it would against Keycloak. A stub that
handed back a ready-made dict would prove nothing about the part most likely to
be wrong.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

ISSUER = "https://idp.example.org/realms/test"
CLIENT_ID = "subremuxer-test"
ADMIN_GROUP = "subremuxer_admins"
VIEWER_GROUP = "subremuxer_viewers"


class FakeIdP:
    """Discovery, JWKS and a token endpoint — the three the app actually calls."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        #: Set to sign with something other than the key the JWKS advertises.
        self.signing_key: rsa.RSAPrivateKey | None = None
        self.kid = "test-key"
        #: What the next token will claim. Tests move these around.
        self.groups: list[str] | str = [f"/{ADMIN_GROUP}"]
        self.nonce = ""
        self.subject = "user-1"
        self.claim_name = "groups"
        self.issuer = ISSUER
        self.audience = CLIENT_ID
        self.token_requests: list[dict[str, str]] = []

    # ------------------------------------------------------------- signing

    @property
    def jwk(self) -> dict[str, object]:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {**public, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def id_token(self, **overrides: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": self.subject,
            "exp": now + 300,
            "iat": now,
            "nonce": self.nonce,
            "name": "Тестовый Пользователь",
            "email": "user@example.org",
            self.claim_name: self.groups,
        }
        claims.update(overrides)
        return jwt.encode(
            claims, self.signing_key or self.key, algorithm="RS256", headers={"kid": self.kid}
        )

    # ------------------------------------------------------------ endpoints

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                    "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                },
            )
        if path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [self.jwk]})
        if path.endswith("/token"):
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            self.token_requests.append({key: value[0] for key, value in form.items()})
            return httpx.Response(
                200,
                json={"access_token": "at", "token_type": "Bearer", "id_token": self.id_token()},
            )
        return httpx.Response(404, json={"error": "not_found", "path": path})


def start_login(client: TestClient, idp: FakeIdP, next_url: str | None = None) -> str:
    """Follow the first leg and hand back the state, with the nonce armed."""
    target = "/auth/oidc/login" + (f"?next={next_url}" if next_url else "")
    started = client.get(target, follow_redirects=False)
    assert started.status_code == 303, started.text
    query = parse_qs(urlsplit(started.headers["location"]).query)
    idp.nonce = query["nonce"][0]
    return query["state"][0]


def sign_in(client: TestClient, idp: FakeIdP, next_url: str | None = None) -> httpx.Response:
    """Walk the whole redirect dance the way a browser would."""
    state = start_login(client, idp, next_url)
    return client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)


def sign_in_as(client: TestClient, idp: FakeIdP, role: str) -> TestClient:
    idp.groups = [f"/{ADMIN_GROUP if role == 'admin' else VIEWER_GROUP}"]
    response = sign_in(client, idp)
    assert response.status_code == 303, response.text
    return client
