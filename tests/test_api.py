from __future__ import annotations

from .conftest import ADMIN_PASSWORD

PROFILE = {
    "name": "Мобильные без РФ",
    "upstream_url": "https://panel.example.org/sub/abc",
    "hwid": "UE42LJXu4DbiCaBv",
    "hwid_mode": "override",
    "filter": {
        "mode": "builder",
        "match": "all",
        "conditions": [
            {"op": "contains", "value": "LTE"},
            {"op": "not_contains", "value": "RU"},
        ],
    },
    "protocols": ["vless", "trojan"],
}


def test_health_needs_no_auth(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_admin_endpoints_are_closed_without_a_session(client):
    for method, path in [
        ("get", "/api/profiles"),
        ("get", "/api/meta"),
        ("get", "/api/logs"),
        ("get", "/api/settings"),
        ("get", "/api/stats"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path


def test_wrong_password_is_rejected(client):
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert client.get("/api/auth/me").json() == {"authenticated": False, "demo": False}


def test_login_then_logout(client):
    assert client.post("/api/auth/login", json={"password": ADMIN_PASSWORD}).status_code == 200
    assert client.get("/api/auth/me").json() == {"authenticated": True, "demo": False}
    assert client.get("/api/profiles").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/profiles").status_code == 401


def test_repeated_failures_are_throttled(client):
    for _ in range(10):
        client.post("/api/auth/login", json={"password": "nope"})
    response = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 429


def test_profile_crud(admin):
    created = admin.post("/api/profiles", json=PROFILE)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == PROFILE["name"]
    assert body["protocols"] == ["vless", "trojan"]
    assert body["subscription_url"].startswith("https://sub.example.org/s/")
    assert len(body["token"]) >= 20

    profile_id = body["id"]
    listed = admin.get("/api/profiles").json()
    assert [item["id"] for item in listed] == [profile_id]

    updated = admin.put(
        f"/api/profiles/{profile_id}", json={**PROFILE, "name": "Переименован", "enabled": False}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Переименован"
    assert updated.json()["enabled"] is False

    rotated = admin.post(f"/api/profiles/{profile_id}/rotate-token").json()
    assert rotated["token"] != body["token"]

    assert admin.delete(f"/api/profiles/{profile_id}").status_code == 200
    assert admin.get(f"/api/profiles/{profile_id}").status_code == 404


def test_profile_validation_errors_are_human_readable(admin):
    bad_url = admin.post("/api/profiles", json={**PROFILE, "upstream_url": "panel.example.org"})
    assert bad_url.status_code == 400
    assert "http" in bad_url.json()["detail"]

    no_name = admin.post("/api/profiles", json={**PROFILE, "name": "  "})
    assert no_name.status_code == 400

    bad_protocol = admin.post("/api/profiles", json={**PROFILE, "protocols": ["carrier-pigeon"]})
    assert bad_protocol.status_code == 400

    bad_regex = admin.post(
        "/api/profiles",
        json={**PROFILE, "filter": {"mode": "raw", "include_regex": "([unclosed"}},
    )
    assert bad_regex.status_code == 400


def test_meta_exposes_everything_the_ui_needs(admin):
    meta = admin.get("/api/meta").json()
    assert "vless" in meta["protocols"]
    assert "contains" in meta["condition_ops"]
    assert {preset["id"] for preset in meta["presets"]} >= {"contains_lte", "lte_not_ru"}
    assert meta["hwid_modes"] == ["override", "fallback", "passthrough"]
    assert "base64" in meta["format_labels"]


def test_defaults_are_stored_and_reported(admin):
    response = admin.put(
        "/api/settings",
        json={"default_hwid": "UE42LJXu4DbiCaBv", "default_device_os": "Android"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["default_hwid"] == "UE42LJXu4DbiCaBv"
    assert body["default_device_os"] == "Android"
    assert body["hwid_valid"] is True

    admin.put("/api/settings", json={"default_hwid": "short"})
    assert admin.get("/api/settings").json()["hwid_valid"] is False


def test_regex_preview_matches_the_builder(admin):
    response = admin.post(
        "/api/filter/regex",
        json={"filter": {"conditions": [{"op": "contains", "value": "LTE"}]}},
    )
    assert response.json()["regex"] == r"(?i)^(?=.*LTE).*$"


def test_dry_run_explains_each_name(admin):
    response = admin.post(
        "/api/filter/dry-run",
        json={
            "filter": {
                "conditions": [
                    {"op": "contains", "value": "LTE"},
                    {"op": "not_contains", "value": "RU"},
                ]
            },
            "names": ["NL-1 LTE", "RU-1 LTE", "DE-1"],
        },
    )
    results = response.json()["results"]
    assert [item["kept"] for item in results] == [True, False, False]
    assert "не содержит" in results[1]["detail"]


def test_qr_code_is_served_as_svg(admin):
    profile_id = admin.post("/api/profiles", json=PROFILE).json()["id"]
    response = admin.get(f"/api/profiles/{profile_id}/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<?xml") or response.text.lstrip().startswith("<svg")
