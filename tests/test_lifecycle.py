"""Cloning, and the undoable delete behind the countdown snackbar."""

from __future__ import annotations

import httpx

from . import fixtures
from .conftest import ADMIN_PASSWORD

PROFILE = {
    "name": "Основной",
    "upstream_url": "https://panel.example.org/sub/abc",
    "hwid": "UE42LJXu4DbiCaBv",
    "hwid_mode": "override",
    "device_os": "Android",
    "device_ver": "15",
    "device_model": "Pixel 9",
    "upstream_ua": "Happ/2.16.0",
    "filter": {"conditions": [{"op": "contains", "value": "LTE"}]},
    "protocols": ["vless"],
    "cache_ttl": 30,
}


# ------------------------------------------------------------------ cloning


def test_clone_copies_every_setting_but_the_token(admin):
    original = admin.post("/api/profiles", json=PROFILE).json()
    clone = admin.post(f"/api/profiles/{original['id']}/clone").json()

    assert clone["id"] != original["id"]
    assert clone["token"] != original["token"]
    assert clone["subscription_url"] != original["subscription_url"]
    for key in (
        "upstream_url",
        "hwid",
        "hwid_mode",
        "device_os",
        "device_ver",
        "device_model",
        "upstream_ua",
        "protocols",
        "cache_ttl",
        "output_format",
        "enabled",
    ):
        assert clone[key] == original[key], key
    assert clone["filter"]["conditions"] == original["filter"]["conditions"]


def test_clone_names_do_not_collide(admin):
    original = admin.post("/api/profiles", json=PROFILE).json()
    first = admin.post(f"/api/profiles/{original['id']}/clone").json()
    second = admin.post(f"/api/profiles/{original['id']}/clone").json()
    third = admin.post(f"/api/profiles/{first['id']}/clone").json()

    assert first["name"] == "Основной (копия)"
    assert second["name"] == "Основной (копия 2)"
    # Cloning a copy must not produce "Основной (копия) (копия)".
    assert third["name"] == "Основной (копия 3)"


def test_cloning_a_missing_profile_is_404(admin):
    assert admin.post("/api/profiles/999/clone").status_code == 404


def test_a_clone_serves_the_same_filtered_subscription(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixtures.URI_LIST)

    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    original = client.post("/api/profiles", json=PROFILE).json()
    clone = client.post(f"/api/profiles/{original['id']}/clone").json()

    assert client.get(f"/s/{original['token']}").text == client.get(f"/s/{clone['token']}").text


# ------------------------------------------------------- delete then undo


def test_delete_hides_the_profile_and_kills_the_link(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixtures.URI_LIST)

    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    profile = client.post("/api/profiles", json=PROFILE).json()

    assert client.get(f"/s/{profile['token']}").status_code == 200
    assert client.delete(f"/api/profiles/{profile['id']}").status_code == 200

    assert client.get("/api/profiles").json() == []
    assert client.get(f"/api/profiles/{profile['id']}").status_code == 404
    assert client.get(f"/s/{profile['token']}").status_code == 404


def test_undo_brings_the_profile_back_intact(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixtures.URI_LIST)

    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    profile = client.post("/api/profiles", json=PROFILE).json()
    client.delete(f"/api/profiles/{profile['id']}")

    restored = client.post(f"/api/profiles/{profile['id']}/restore")
    assert restored.status_code == 200
    body = restored.json()
    # The same token, so a link already imported into a client keeps working.
    assert body["token"] == profile["token"]
    assert body["name"] == profile["name"]
    assert body["filter"]["conditions"] == profile["filter"]["conditions"]

    assert [item["id"] for item in client.get("/api/profiles").json()] == [profile["id"]]
    assert client.get(f"/s/{profile['token']}").status_code == 200


def test_deleting_twice_is_a_404_the_second_time(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    assert admin.delete(f"/api/profiles/{profile['id']}").status_code == 200
    assert admin.delete(f"/api/profiles/{profile['id']}").status_code == 404


def test_restoring_something_that_was_never_deleted_is_404(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    assert admin.post(f"/api/profiles/{profile['id']}/restore").status_code == 404


def test_a_deleted_profile_is_purged_after_the_grace_period(client, settings):
    from app.profiles import ProfileRepository

    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    profile = client.post("/api/profiles", json=PROFILE).json()
    client.delete(f"/api/profiles/{profile['id']}")

    repo = ProfileRepository(client.app.state.db)
    assert repo.purge_deleted(3600) == 0, "still inside the grace period"
    assert repo.get(profile["id"], include_deleted=True) is not None

    assert repo.purge_deleted(0) == 1
    assert repo.get(profile["id"], include_deleted=True) is None
    assert client.post(f"/api/profiles/{profile['id']}/restore").status_code == 404


def test_a_deleted_profile_is_not_counted_in_stats(admin):
    admin.post("/api/profiles", json=PROFILE)
    profile = admin.post("/api/profiles", json={**PROFILE, "name": "Второй"}).json()
    assert admin.get("/api/stats").json()["profiles_total"] == 2

    admin.delete(f"/api/profiles/{profile['id']}")
    assert admin.get("/api/stats").json()["profiles_total"] == 1


def test_deleting_a_profile_leaves_its_log_entries_readable(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixtures.URI_LIST)

    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    profile = client.post("/api/profiles", json=PROFILE).json()
    client.get(f"/s/{profile['token']}")
    client.delete(f"/api/profiles/{profile['id']}")

    entries = client.get("/api/logs").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["profile_name"] == "Основной"
