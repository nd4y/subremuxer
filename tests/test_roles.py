"""What each role may do, and what the sign-in flags switch off.

The viewer restriction is enforced on the server. These tests read the JSON the
API actually returns rather than what the interface chooses to draw with it,
because a field that reaches the browser is a field the browser can be made to
show.
"""

from __future__ import annotations

import pytest

from .conftest import ADMIN_PASSWORD
from .idp import sign_in_as

PROFILE = {
    "name": "Мобильные",
    "upstream_url": "https://panel.example.org/sub/secret-token",
    "hwid": "UE42LJXu4DbiCaBv",
    "protocols": ["vless"],
}

#: Everything a viewer must be refused. Read endpoints are listed too — being
#: unable to write is not the point, not seeing the instance is.
ADMIN_ONLY_READS = (
    "/api/meta",
    "/api/settings",
    "/api/logs",
    "/api/stats",
    "/api/templates",
    "/api/probe",
    "/api/config",
    "/api/export",
)


@pytest.fixture()
def seeded(oidc_client, idp):
    """An instance holding one profile, plus a way to sign in under either role."""
    admin = oidc_client()
    sign_in_as(admin, idp, "admin")
    created = admin.post("/api/profiles", json=PROFILE)
    assert created.status_code == 201, created.text
    return admin, created.json()


@pytest.fixture()
def viewer(oidc_client, idp, seeded):
    client = oidc_client()
    return sign_in_as(client, idp, "viewer")


# ------------------------------------------------------------------ a viewer


def test_a_viewer_sees_the_link_to_hand_out(viewer, seeded):
    _, profile = seeded
    listed = viewer.get("/api/profiles")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["name"] == "Мобильные"
    assert body[0]["subscription_url"] == profile["subscription_url"]


def test_a_viewer_never_receives_the_upstream_url(viewer, seeded):
    """The whole point of the role: the link out, not the link in."""
    _, profile = seeded
    for response in (viewer.get("/api/profiles"), viewer.get(f"/api/profiles/{profile['id']}")):
        assert response.status_code == 200
        assert "secret-token" not in response.text
        assert "upstream_url" not in response.text


def test_a_viewer_is_told_nothing_about_how_a_profile_is_built(viewer, seeded):
    _, profile = seeded
    body = viewer.get(f"/api/profiles/{profile['id']}").json()
    assert set(body) == {"id", "name", "enabled", "subscription_url", "updated_at"}
    for hidden in ("hwid", "filter", "protocols", "upstream_ua", "token", "device_model"):
        assert hidden not in body


def test_a_viewer_can_still_render_the_qr_code(viewer, seeded):
    _, profile = seeded
    response = viewer.get(f"/api/profiles/{profile['id']}/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


def test_a_viewer_cannot_change_anything(viewer, seeded):
    _, profile = seeded
    profile_id = profile["id"]
    forbidden = (
        ("post", "/api/profiles"),
        ("put", f"/api/profiles/{profile_id}"),
        ("delete", f"/api/profiles/{profile_id}"),
        ("post", f"/api/profiles/{profile_id}/clone"),
        ("post", f"/api/profiles/{profile_id}/rotate-token"),
        ("put", "/api/settings"),
        ("post", "/api/import"),
        ("put", "/api/config"),
        ("post", "/api/templates"),
        ("delete", "/api/logs"),
        ("post", "/api/filter/test"),
    )
    for method, path in forbidden:
        call = getattr(viewer, method)
        response = call(path) if method == "delete" else call(path, json={})
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


def test_a_viewer_sees_nothing_beyond_profiles(viewer):
    for path in ADMIN_ONLY_READS:
        assert viewer.get(path).status_code == 403, path


def test_a_viewer_cannot_read_the_capture_page_or_export(viewer, seeded):
    _, profile = seeded
    assert viewer.get(f"/api/profiles/{profile['id']}/export").status_code == 403
    assert viewer.get("/api/export?format=yaml").status_code == 403


def test_a_forbidden_answer_is_403_and_not_401(viewer):
    """401 would send a signed-in viewer back to the login screen, which is a
    dead end: signing in again changes nothing about their role."""
    response = viewer.get("/api/logs")
    assert response.status_code == 403
    assert "администратор" in response.json()["detail"]


def test_a_viewer_can_sign_out(viewer):
    assert viewer.post("/api/auth/logout").status_code == 200
    assert viewer.get("/api/profiles").status_code == 401


def test_an_admin_still_sees_everything(seeded):
    admin, profile = seeded
    body = admin.get(f"/api/profiles/{profile['id']}").json()
    assert body["upstream_url"] == PROFILE["upstream_url"]
    for path in ADMIN_ONLY_READS:
        assert admin.get(path).status_code == 200, path


# ------------------------------------------------------------------- flags


def test_disabling_the_login_form_switches_the_endpoint_off(oidc_client):
    """Hiding the button alone would mean nothing: the endpoint is public
    knowledge in a public repository."""
    client = oidc_client(AUTH_DISABLE_LOGIN_FORM="true")
    body = client.get("/api/auth/me").json()
    assert body["methods"] == {"password": False, "oidc": True}

    response = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 404
    assert client.get("/api/profiles").status_code == 401


def test_the_provider_still_works_when_the_password_is_off(oidc_client, idp):
    client = oidc_client(AUTH_DISABLE_LOGIN_FORM="true")
    sign_in_as(client, idp, "admin")
    assert client.get("/api/profiles").status_code == 200


def test_the_password_cannot_be_disabled_without_a_replacement(monkeypatch, tmp_path):
    """Honouring this would leave an instance with no way in at all."""
    from app.config import Settings, reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("AUTH_DISABLE_LOGIN_FORM", "true")
    reset_settings()
    settings = Settings()

    assert settings.password_login_enabled is True
    assert any("AUTH_DISABLE_LOGIN_FORM" in warning for warning in settings.warnings)
    reset_settings()


def test_auto_login_is_ignored_without_a_provider(monkeypatch, tmp_path):
    from app.config import Settings, reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("OIDC_AUTO_LOGIN", "true")
    reset_settings()
    settings = Settings()

    assert settings.oidc_auto_login is False
    assert any("OIDC_AUTO_LOGIN" in warning for warning in settings.warnings)
    reset_settings()


def test_auto_login_is_advertised_to_the_interface(oidc_client):
    client = oidc_client(OIDC_AUTO_LOGIN="true")
    assert client.get("/api/auth/me").json()["auto_login"] is True


def test_a_provider_without_groups_configured_is_flagged(monkeypatch, tmp_path):
    from app.config import Settings, reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.org/realms/test")
    monkeypatch.setenv("OIDC_CLIENT_ID", "subremuxer-test")
    reset_settings()
    settings = Settings()

    assert any("OIDC_ADMIN_GROUP" in warning for warning in settings.warnings)
    reset_settings()


def test_the_startup_check_reaches_the_log(oidc_client, capsys):
    """The provider check is only useful if `docker logs` actually shows it.

    Read from the stream rather than through caplog: the logger deliberately
    does not propagate to the root, so caplog would see nothing and the test
    would pass or fail depending on what ran before it.
    """
    import logging

    from app.main import logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    oidc_client()

    assert "OIDC" in capsys.readouterr().err
    assert logger.handlers, "у логгера приложения нет обработчика — INFO никуда не попадёт"
    assert logger.level <= logging.INFO


# --------------------------------------------------------------- migration


def test_sessions_from_before_roles_existed_stay_administrators(tmp_path):
    """An upgrade must not silently demote whoever is already signed in."""
    import sqlite3

    from app.db import Database

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE sessions (token TEXT PRIMARY KEY, created_at INTEGER NOT NULL, "
        "expires_at INTEGER NOT NULL, user_agent TEXT, ip TEXT)"
    )
    legacy.execute("INSERT INTO sessions VALUES('old-token', 0, 4102444800, NULL, NULL)")
    legacy.commit()
    legacy.close()

    db = Database(path)
    row = db.get_session("old-token")
    assert row is not None
    assert row["role"] == "admin"
    db.close()
