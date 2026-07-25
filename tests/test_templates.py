from __future__ import annotations

from app.profiles import CLIENT_PRESETS, DEVICE_PRESETS, default_profile_fields
from app.templates import BUILTIN_TEMPLATES

PROFILE = {
    "name": "Основной",
    "upstream_url": "https://panel.example.org/sub/abc",
    "hwid": "UE42LJXu4DbiCaBv",
    "filter": {"conditions": [{"op": "contains", "value": "LTE"}]},
    "protocols": ["vless"],
}


# ------------------------------------------------------------------ presets


def test_meta_exposes_client_and_device_presets(admin):
    meta = admin.get("/api/meta").json()
    client_ids = {preset["id"] for preset in meta["client_presets"]}
    assert {"happ", "v2raytun", "singbox", "mihomo", "shadowrocket", "passthrough"} <= client_ids
    device_ids = {preset["id"] for preset in meta["device_presets"]}
    assert {"pixel9", "iphone16pro", "none"} <= device_ids
    assert meta["default_client_preset"] == "happ"
    assert meta["default_device_preset"] == "pixel9"


def test_every_client_preset_has_a_user_agent_except_passthrough():
    for preset in CLIENT_PRESETS:
        assert preset["label"] and preset["family"] and preset["hint"]
        if preset["id"] != "passthrough":
            assert preset["user_agent"], preset["id"]


def test_defaults_mimic_happ_on_a_pixel_9():
    defaults = default_profile_fields()
    assert defaults["hwid_mode"] == "override"
    assert "Happ" in defaults["upstream_ua"]
    assert defaults["device_os"] == "Android"
    assert defaults["device_model"] == "Pixel 9"


def test_the_none_device_preset_clears_every_field():
    preset = next(item for item in DEVICE_PRESETS if item["id"] == "none")
    assert preset["os"] == preset["ver"] == preset["model"] == ""


# ---------------------------------------------------------------- built-ins


def test_builtins_are_seeded_on_first_start(admin):
    templates = admin.get("/api/templates").json()
    builtin_ids = {template["builtin_id"] for template in templates}
    assert builtin_ids == {template["builtin_id"] for template in BUILTIN_TEMPLATES}


def test_the_default_builtin_is_happ_on_a_pixel_9(admin):
    templates = admin.get("/api/templates").json()
    template = next(item for item in templates if item["builtin_id"] == "happ_pixel9")
    assert "Happ" in template["payload"]["upstream_ua"]
    assert template["payload"]["device_model"] == "Pixel 9"
    assert template["payload"]["hwid_mode"] == "override"
    assert template["payload"]["filter"]["conditions"] == []


def test_builtins_are_ordered_and_described(admin):
    templates = admin.get("/api/templates").json()
    assert [t["sort_order"] for t in templates] == sorted(t["sort_order"] for t in templates)
    assert all(template["description"] for template in templates)


def test_editing_a_builtin_sticks_and_can_be_restored(admin):
    templates = admin.get("/api/templates").json()
    template = next(item for item in templates if item["builtin_id"] == "happ_pixel9")

    edited = admin.put(
        f"/api/templates/{template['id']}",
        json={"name": "Мой Happ", "payload": {**template["payload"], "hwid_mode": "passthrough"}},
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Мой Happ"
    assert edited.json()["payload"]["hwid_mode"] == "passthrough"

    assert admin.post("/api/templates/restore-builtins").json()["restored"] == len(BUILTIN_TEMPLATES)
    restored = next(
        item
        for item in admin.get("/api/templates").json()
        if item["builtin_id"] == "happ_pixel9"
    )
    assert restored["name"] != "Мой Happ"
    assert restored["payload"]["hwid_mode"] == "override"


def test_restoring_recreates_a_deleted_builtin(admin):
    templates = admin.get("/api/templates").json()
    template = next(item for item in templates if item["builtin_id"] == "passthrough")
    assert admin.delete(f"/api/templates/{template['id']}").status_code == 200
    assert all(
        item["builtin_id"] != "passthrough" for item in admin.get("/api/templates").json()
    )

    admin.post("/api/templates/restore-builtins")
    assert any(item["builtin_id"] == "passthrough" for item in admin.get("/api/templates").json())


# --------------------------------------------------------------------- crud


def test_template_crud(admin):
    created = admin.post(
        "/api/templates",
        json={
            "name": "Свой",
            "description": "тест",
            "payload": {
                "hwid_mode": "fallback",
                "upstream_ua": "Happ/2.16.0",
                "device_os": "Android",
                "device_ver": "15",
                "device_model": "Pixel 9",
                "filter": {"conditions": [{"op": "not_contains", "value": "RU"}]},
                "protocols": ["vless"],
            },
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["builtin_id"] is None
    assert body["payload"]["hwid_mode"] == "fallback"
    assert body["payload"]["protocols"] == ["vless"]

    assert admin.delete(f"/api/templates/{body['id']}").status_code == 200
    assert admin.delete(f"/api/templates/{body['id']}").status_code == 404


def test_a_template_cannot_claim_a_builtin_id(admin):
    created = admin.post(
        "/api/templates", json={"name": "Подделка", "builtin_id": "happ_pixel9", "payload": {}}
    )
    assert created.status_code == 201
    assert created.json()["builtin_id"] is None


def test_template_validation_errors(admin):
    assert admin.post("/api/templates", json={"name": "  ", "payload": {}}).status_code == 400
    bad_regex = admin.post(
        "/api/templates",
        json={"name": "x", "payload": {"filter": {"mode": "raw", "include_regex": "([bad"}}},
    )
    assert bad_regex.status_code == 400
    bad_mode = admin.post("/api/templates", json={"name": "x", "payload": {"hwid_mode": "magic"}})
    assert bad_mode.status_code == 400


def test_a_template_never_carries_the_panel_url_or_the_profile_name(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    template = admin.post(f"/api/templates/from-profile/{profile['id']}", json={}).json()
    assert "upstream_url" not in template["payload"]
    assert "name" not in template["payload"]
    assert "token" not in template["payload"]
    assert template["payload"]["hwid"] == PROFILE["hwid"]
    assert template["payload"]["protocols"] == ["vless"]
    assert template["payload"]["filter"]["conditions"] == PROFILE["filter"]["conditions"]


def test_saving_a_template_from_a_missing_profile_is_404(admin):
    assert admin.post("/api/templates/from-profile/999", json={}).status_code == 404


# ------------------------------------------------------- creating from one


def test_creating_a_profile_from_a_template(admin):
    template = next(
        item
        for item in admin.get("/api/templates").json()
        if item["builtin_id"] == "happ_lte_no_ru"
    )
    created = admin.post(
        "/api/profiles",
        json={
            "template_id": template["id"],
            "name": "Из шаблона",
            "upstream_url": "https://panel.example.org/sub/abc",
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["name"] == "Из шаблона"
    assert profile["upstream_url"] == "https://panel.example.org/sub/abc"
    assert profile["device_model"] == "Pixel 9"
    assert "Happ" in profile["upstream_ua"]
    assert [c["value"] for c in profile["filter"]["conditions"]] == ["LTE", "RU"]


def test_values_sent_alongside_a_template_win(admin):
    template = next(
        item for item in admin.get("/api/templates").json() if item["builtin_id"] == "happ_pixel9"
    )
    profile = admin.post(
        "/api/profiles",
        json={
            "template_id": template["id"],
            "name": "Свой HWID",
            "upstream_url": "https://panel.example.org/sub/abc",
            "hwid": "OVERRIDEhwid123",
            "hwid_mode": "fallback",
        },
    ).json()
    assert profile["hwid"] == "OVERRIDEhwid123"
    assert profile["hwid_mode"] == "fallback"
    assert profile["device_model"] == "Pixel 9"


def test_creating_from_an_unknown_template_is_404(admin):
    response = admin.post(
        "/api/profiles",
        json={"template_id": 999, "name": "x", "upstream_url": "https://example.org/s"},
    )
    assert response.status_code == 404
