"""Signing in through an identity provider: the flow, the token checks, the roles.

The provider is a real one in miniature (see tests/idp.py) rather than a stub,
so signature and audience checks are exercised the way they will be in front of
Keycloak.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from .conftest import ADMIN_PASSWORD
from .idp import ADMIN_GROUP, CLIENT_ID, ISSUER, VIEWER_GROUP, sign_in, start_login

# ------------------------------------------------------------------ the flow


def test_the_login_redirect_carries_pkce_and_the_client_id(oidc_client, idp):
    client = oidc_client()
    response = client.get("/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert query["redirect_uri"] == ["https://sub.example.org/auth/oidc/callback"]


def test_groups_are_not_requested_as_a_scope(oidc_client, idp):
    """Keycloak has no `groups` client scope, and asking for one it does not know
    gets the whole login refused with `invalid_scope` — before the user ever sees
    a password prompt. The group mapper sits on the client's dedicated scope and
    needs no request."""
    client = oidc_client()
    response = client.get("/auth/oidc/login", follow_redirects=False)
    scope = parse_qs(urlsplit(response.headers["location"]).query)["scope"][0]
    assert scope == "openid profile email"


def test_scopes_can_be_extended_for_realms_that_need_it(oidc_client, idp):
    client = oidc_client(OIDC_SCOPES="openid profile email team-groups")
    response = client.get("/auth/oidc/login", follow_redirects=False)
    scope = parse_qs(urlsplit(response.headers["location"]).query)["scope"][0]
    assert scope == "openid profile email team-groups"


def test_a_full_sign_in_grants_an_admin_session(oidc_client, idp):
    client = oidc_client()
    response = sign_in(client, idp)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["role"] == "admin"
    assert me["method"] == "oidc"
    assert me["user"] == "Тестовый Пользователь"
    assert client.get("/api/profiles").status_code == 200


def test_the_code_is_exchanged_with_the_verifier_that_started_the_login(oidc_client, idp):
    client = oidc_client()
    sign_in(client, idp)
    exchange = idp.token_requests[-1]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "abc"
    assert exchange["code_verifier"]
    assert exchange["redirect_uri"] == "https://sub.example.org/auth/oidc/callback"


def test_a_state_cannot_be_replayed(oidc_client, idp):
    """Otherwise a leaked callback URL would be a reusable key to the instance."""
    client = oidc_client()
    state = start_login(client, idp)
    assert client.get(f"/auth/oidc/callback?code=abc&state={state}").status_code == 200
    client.cookies.clear()
    second = client.get(f"/auth/oidc/callback?code=abc&state={state}")
    assert second.status_code == 400
    assert "устарел" in second.text


def test_an_unknown_state_is_refused(oidc_client, idp):
    client = oidc_client()
    response = client.get("/auth/oidc/callback?code=abc&state=made-up")
    assert response.status_code == 400
    assert client.get("/api/profiles").status_code == 401


def test_the_provider_error_is_shown_rather_than_swallowed(oidc_client, idp):
    client = oidc_client()
    response = client.get(
        "/auth/oidc/callback?error=access_denied&error_description=User+said+no"
    )
    assert response.status_code == 400
    assert "access_denied" in response.text
    assert "User said no" in response.text


def test_a_relative_next_is_honoured_and_an_absolute_one_is_not(oidc_client, idp):
    """An open redirect on a login endpoint is how a phishing link gets its polish."""
    client = oidc_client()
    assert sign_in(client, idp, "/logs").headers["location"] == "/logs"
    client.cookies.clear()
    assert sign_in(client, idp, "https://evil.example/pwn").headers["location"] == "/"


# ------------------------------------------------------------ token checking


def test_a_token_signed_by_the_wrong_key_is_rejected(oidc_client, idp):
    client = oidc_client()
    idp.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # The JWKS still advertises the original key, so the signature cannot match.
    state = start_login(client, idp)
    response = client.get(f"/auth/oidc/callback?code=abc&state={state}")
    assert response.status_code == 400
    assert client.get("/api/profiles").status_code == 401


def test_a_token_for_another_audience_is_rejected(oidc_client, idp):
    client = oidc_client()
    idp.audience = "some-other-client"
    state = start_login(client, idp)
    response = client.get(f"/auth/oidc/callback?code=abc&state={state}")
    assert response.status_code == 400
    assert client.get("/api/profiles").status_code == 401


def test_a_replayed_token_from_another_login_is_rejected(oidc_client, idp):
    """The nonce is what ties a token to the login attempt that asked for it."""
    client = oidc_client()
    state = start_login(client, idp)
    idp.nonce = "a-nonce-from-some-other-attempt"
    response = client.get(f"/auth/oidc/callback?code=abc&state={state}")
    assert response.status_code == 400
    assert "nonce" in response.text


def test_an_expired_token_is_rejected(oidc_client, idp, monkeypatch):
    client = oidc_client()
    state = start_login(client, idp)
    original = idp.id_token
    monkeypatch.setattr(idp, "id_token", lambda **kw: original(exp=int(time.time()) - 3600))
    response = client.get(f"/auth/oidc/callback?code=abc&state={state}")
    assert response.status_code == 400


# ------------------------------------------------------------------- groups


def test_group_membership_picks_the_role(oidc_client, idp):
    client = oidc_client()
    idp.groups = [f"/{VIEWER_GROUP}"]
    sign_in(client, idp)
    assert client.get("/api/auth/me").json()["role"] == "viewer"


def test_keycloak_style_paths_and_bare_names_both_match(oidc_client, idp):
    """Keycloak's group mapper emits `/name`, or `/parent/name` when nested."""
    for spelling in (ADMIN_GROUP, f"/{ADMIN_GROUP}", f"/apps/{ADMIN_GROUP}"):
        client = oidc_client()
        idp.groups = [spelling]
        sign_in(client, idp)
        assert client.get("/api/auth/me").json()["role"] == "admin", spelling


def test_belonging_to_both_groups_gives_the_stronger_role(oidc_client, idp):
    client = oidc_client()
    idp.groups = [f"/{VIEWER_GROUP}", f"/{ADMIN_GROUP}"]
    sign_in(client, idp)
    assert client.get("/api/auth/me").json()["role"] == "admin"


def test_a_user_without_a_known_group_is_told_what_they_do_have(oidc_client, idp):
    """The blank refusal is what turns a mapper typo into an evening of guesswork."""
    client = oidc_client()
    idp.groups = ["/some_other_team", "/default-roles-test"]
    response = sign_in(client, idp)

    assert response.status_code == 400
    assert "some_other_team" in response.text
    assert "default-roles-test" in response.text
    assert ADMIN_GROUP in response.text  # what it looked for
    assert "groups" in response.text  # and in which claim
    assert client.get("/api/profiles").status_code == 401


def test_an_empty_groups_claim_says_the_mapper_is_probably_missing(oidc_client, idp):
    client = oidc_client()
    idp.groups = []
    response = sign_in(client, idp)
    assert response.status_code == 400
    assert "mapper" in response.text


def test_the_claim_carrying_groups_can_be_renamed(oidc_client, idp):
    client = oidc_client(OIDC_GROUPS_CLAIM="roles")
    idp.claim_name = "roles"
    idp.groups = [ADMIN_GROUP]
    sign_in(client, idp)
    assert client.get("/api/auth/me").json()["role"] == "admin"


def test_groups_arriving_as_a_string_are_understood(oidc_client, idp):
    client = oidc_client()
    idp.groups = f"{VIEWER_GROUP} other"  # type: ignore[assignment]
    sign_in(client, idp)
    assert client.get("/api/auth/me").json()["role"] == "viewer"


# ------------------------------------------------------- an unreachable IdP


def test_a_dead_provider_fails_the_login_without_taking_the_app_down(oidc_settings):
    """The password path has to keep working — that is the whole point of it."""
    from app.main import create_app

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app = create_app(oidc_settings())
    with TestClient(app) as client:
        app.state.oidc._client = httpx.AsyncClient(transport=httpx.MockTransport(dead))
        response = client.get("/auth/oidc/login")
        assert response.status_code == 400
        assert "недоступен" in response.text

        assert client.get("/healthz").status_code == 200
        assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 200
        assert client.get("/api/profiles").status_code == 200


def test_the_error_page_always_offers_the_escape_hatch(oidc_client, idp):
    client = oidc_client(OIDC_AUTO_LOGIN="true")
    idp.groups = []
    response = sign_in(client, idp)
    assert "disableAutoLogin=true" in response.text


def test_the_pending_login_survives_a_restart(oidc_settings, idp, tmp_path):
    """State lives in SQLite, so the callback may land on a different worker —
    or on the same one after a restart — without becoming a dead end."""
    from app.main import create_app

    settings = oidc_settings()
    first = create_app(settings)
    with TestClient(first) as client:
        first.state.oidc._client = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
        state = start_login(client, idp)

    second = create_app(settings)
    with TestClient(second) as client:
        second.state.oidc._client = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
        response = client.get(
            f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
        )
        assert response.status_code == 303
        assert client.get("/api/auth/me").json()["role"] == "admin"


def test_the_json_api_advertises_the_configured_methods(oidc_client, idp):
    client = oidc_client(OIDC_DISPLAY_NAME="Keycloak")
    body = client.get("/api/auth/me").json()
    assert body["methods"] == {"password": True, "oidc": True}
    assert body["oidc_name"] == "Keycloak"
    assert body["auto_login"] is False


def test_login_is_a_404_when_the_provider_is_not_configured(client):
    assert client.get("/auth/oidc/login").status_code == 404
    assert client.get("/api/auth/me").json()["methods"]["oidc"] is False


def test_the_login_page_is_reachable_through_a_reverse_proxy_rewrite(oidc_client, idp):
    client = oidc_client(OIDC_REDIRECT_URL="https://outside.example.org/auth/oidc/callback")
    started = client.get("/auth/oidc/login", follow_redirects=False)
    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert query["redirect_uri"] == ["https://outside.example.org/auth/oidc/callback"]


def test_the_discovery_stub_is_itself_sound(idp):
    """Guards the fixture: a broken stub would make every test above lie."""
    response = idp.handle(httpx.Request("GET", f"{ISSUER}/.well-known/openid-configuration"))
    assert response.json()["issuer"] == ISSUER
