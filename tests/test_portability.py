"""Export, import, and the config editor's validate/apply cycle."""

from __future__ import annotations

import json

import yaml

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
}


def bundle(admin, fmt="yaml") -> dict:
    content = admin.get(f"/api/config?format={fmt}").json()["content"]
    return yaml.safe_load(content) if fmt == "yaml" else json.loads(content)


# ----------------------------------------------------------------- export


def test_single_profile_export_in_both_formats(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()

    as_yaml = admin.get(f"/api/profiles/{profile['id']}/export?format=yaml")
    assert as_yaml.status_code == 200
    assert "attachment" in as_yaml.headers["content-disposition"]
    document = yaml.safe_load(as_yaml.text)
    assert document["kind"] == "subremuxer.profile"
    assert document["profile"]["name"] == "Основной"
    assert document["profile"]["token"] == profile["token"]
    assert document["profile"]["device_model"] == "Pixel 9"

    as_json = admin.get(f"/api/profiles/{profile['id']}/export?format=json")
    assert json.loads(as_json.text)["profile"]["name"] == "Основной"


def test_the_token_can_be_left_out_of_an_export(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    document = yaml.safe_load(
        admin.get(f"/api/profiles/{profile['id']}/export?format=yaml&with_token=false").text
    )
    assert "token" not in document["profile"]


def test_exporting_a_missing_profile_is_404(admin):
    assert admin.get("/api/profiles/999/export").status_code == 404


def test_an_unknown_export_format_is_rejected(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    assert admin.get(f"/api/profiles/{profile['id']}/export?format=toml").status_code == 400


def test_the_full_bundle_carries_settings_templates_and_profiles(admin):
    admin.post("/api/profiles", json=PROFILE)
    admin.put("/api/settings", json={"default_hwid": "GLOBALhwid98765"})

    document = yaml.safe_load(admin.get("/api/export?format=yaml").text)
    assert document["kind"] == "subremuxer.bundle"
    assert document["settings"]["default_hwid"] == "GLOBALhwid98765"
    assert [item["name"] for item in document["profiles"]] == ["Основной"]
    assert len(document["templates"]) >= 7
    assert all("payload" in template for template in document["templates"])


def test_the_bundle_never_leaks_the_admin_password(admin, settings):
    text = admin.get("/api/export?format=yaml").text
    assert settings.admin_password not in text


# ----------------------------------------------------------------- import


def test_importing_a_single_profile(admin):
    original = admin.post("/api/profiles", json=PROFILE).json()
    document = admin.get(f"/api/profiles/{original['id']}/export?format=yaml").text

    result = admin.post("/api/import", json={"content": document}).json()
    assert result["profiles_created"] == 1
    assert result["errors"] == []

    profiles = admin.get("/api/profiles").json()
    assert len(profiles) == 2
    imported = next(item for item in profiles if item["id"] != original["id"])
    # A fresh token by default, so the two links cannot collide.
    assert imported["token"] != original["token"]
    assert imported["name"] == "Основной (импорт)"
    assert imported["device_model"] == "Pixel 9"


def test_import_can_keep_the_original_token(client, settings):
    from .conftest import ADMIN_PASSWORD

    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    original = client.post("/api/profiles", json=PROFILE).json()
    document = client.get(f"/api/profiles/{original['id']}/export?format=yaml").text
    client.delete(f"/api/profiles/{original['id']}")

    # The old row still exists (soft-deleted), so the token is taken and a new one
    # is issued rather than failing the import.
    result = client.post("/api/import", json={"content": document, "keep_tokens": True}).json()
    assert result["profiles_created"] == 1

    from app.profiles import ProfileRepository

    ProfileRepository(client.app.state.db).purge_deleted(0)
    fresh = client.post("/api/import", json={"content": document, "keep_tokens": True}).json()
    assert fresh["profiles_created"] == 1
    assert any(item["token"] == original["token"] for item in client.get("/api/profiles").json())


def test_importing_a_bundle_restores_settings_and_templates(admin):
    admin.post("/api/profiles", json=PROFILE)
    admin.put("/api/settings", json={"default_hwid": "GLOBALhwid98765"})
    document = admin.get("/api/export?format=yaml").text

    admin.put("/api/settings", json={"default_hwid": ""})
    result = admin.post("/api/import", json={"content": document}).json()

    assert result["profiles_created"] == 1
    assert "default_hwid" in result["settings_applied"]
    assert admin.get("/api/settings").json()["default_hwid"] == "GLOBALhwid98765"


def test_settings_can_be_left_alone_on_import(admin):
    admin.put("/api/settings", json={"default_hwid": "GLOBALhwid98765"})
    document = admin.get("/api/export?format=yaml").text
    admin.put("/api/settings", json={"default_hwid": "DIFFERENThwid77"})

    admin.post("/api/import", json={"content": document, "with_settings": False})
    assert admin.get("/api/settings").json()["default_hwid"] == "DIFFERENThwid77"


def test_json_and_yaml_are_both_accepted(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    as_json = admin.get(f"/api/profiles/{profile['id']}/export?format=json").text
    assert admin.post("/api/import", json={"content": as_json}).json()["profiles_created"] == 1


def test_a_foreign_document_is_refused(admin):
    for content in ("", "just text", "{}", yaml.safe_dump({"kind": "something.else"})):
        response = admin.post("/api/import", json={"content": content})
        assert response.status_code == 400, content


def test_a_newer_document_version_is_refused(admin):
    content = yaml.safe_dump({"kind": "subremuxer.profile", "version": 99, "profile": {}})
    response = admin.post("/api/import", json={"content": content})
    assert response.status_code == 400
    assert "более новой версией" in response.json()["detail"]


def test_a_broken_entry_is_reported_without_stopping_the_rest(admin):
    content = yaml.safe_dump(
        {
            "kind": "subremuxer.bundle",
            "version": 1,
            "profiles": [
                {**PROFILE, "name": "Хороший"},
                {"name": "Плохой", "upstream_url": "not-a-url"},
            ],
            "templates": [],
        },
        allow_unicode=True,
    )
    result = admin.post("/api/import", json={"content": content}).json()
    assert result["profiles_created"] == 1
    assert len(result["errors"]) == 1
    assert "Плохой" in result["errors"][0]


# ------------------------------------------------------------ config editor


def test_the_editor_reads_the_whole_configuration(admin):
    admin.post("/api/profiles", json=PROFILE)
    body = admin.get("/api/config?format=yaml").json()
    assert body["format"] == "yaml"
    document = yaml.safe_load(body["content"])
    assert document["kind"] == "subremuxer.bundle"
    assert [item["name"] for item in document["profiles"]] == ["Основной"]


def test_validate_reports_no_change_for_an_untouched_document(admin):
    admin.post("/api/profiles", json=PROFILE)
    content = admin.get("/api/config?format=yaml").json()["content"]

    result = admin.post("/api/config/validate", json={"content": content}).json()
    assert result["ok"] is True
    assert result["errors"] == []
    summary = result["summary"]
    assert summary["profiles_created"] == 0
    assert summary["profiles_removed"] == []
    assert summary["profiles_updated"] == 1


def test_validate_describes_additions_and_removals(admin):
    admin.post("/api/profiles", json=PROFILE)
    document = bundle(admin)
    document["profiles"] = [{**PROFILE, "name": "Совсем другой"}]

    result = admin.post(
        "/api/config/validate", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    ).json()
    assert result["ok"] is True
    assert result["summary"]["profiles_created"] == 1
    assert result["summary"]["profiles_removed"] == ["Основной"]


def test_validate_never_changes_anything(admin):
    admin.post("/api/profiles", json=PROFILE)
    document = bundle(admin)
    document["profiles"] = []
    admin.post("/api/config/validate", json={"content": yaml.safe_dump(document, allow_unicode=True)})
    assert len(admin.get("/api/profiles").json()) == 1


def test_validate_reports_errors_instead_of_raising(admin):
    document = bundle(admin)
    document["profiles"] = [{"name": "Без ссылки"}]
    result = admin.post(
        "/api/config/validate", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    ).json()
    assert result["ok"] is False
    assert "Без ссылки" in result["errors"][0]


def test_a_single_profile_document_is_not_accepted_by_the_editor(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    content = admin.get(f"/api/profiles/{profile['id']}/export").text
    result = admin.post("/api/config/validate", json={"content": content}).json()
    assert result["ok"] is False
    assert "полной конфигурацией" in result["errors"][0]


def test_applying_edits_a_profile_in_place_keeping_its_token(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    document = bundle(admin)
    document["profiles"][0]["name"] = "Переименован"
    document["profiles"][0]["cache_ttl"] = 42

    result = admin.put(
        "/api/config", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    ).json()
    assert result["ok"] is True
    assert result["summary"]["profiles_updated"] == 1

    profiles = admin.get("/api/profiles").json()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Переименован"
    assert profiles[0]["cache_ttl"] == 42
    assert profiles[0]["token"] == profile["token"], "the link must survive a rename"


def test_applying_removes_what_is_missing_but_keeps_it_restorable(admin):
    profile = admin.post("/api/profiles", json=PROFILE).json()
    document = bundle(admin)
    document["profiles"] = []

    result = admin.put(
        "/api/config", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    ).json()
    assert result["summary"]["profiles_removed"] == ["Основной"]
    assert admin.get("/api/profiles").json() == []

    # Soft-deleted, so a mistaken save is not the end of the world.
    assert admin.post(f"/api/profiles/{profile['id']}/restore").status_code == 200
    assert len(admin.get("/api/profiles").json()) == 1


def test_applying_a_broken_document_changes_nothing(admin):
    admin.post("/api/profiles", json=PROFILE)
    document = bundle(admin)
    document["profiles"].append({"name": "Сломанный", "upstream_url": "nope"})

    response = admin.put(
        "/api/config", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    )
    assert response.status_code == 400
    profiles = admin.get("/api/profiles").json()
    assert [item["name"] for item in profiles] == ["Основной"]


def test_duplicate_names_are_rejected(admin):
    document = bundle(admin)
    document["profiles"] = [PROFILE, {**PROFILE}]
    result = admin.post(
        "/api/config/validate", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    ).json()
    assert result["ok"] is False
    assert "дважды" in result["errors"][0]


def test_applying_returns_the_reserialised_document(admin):
    admin.post("/api/profiles", json=PROFILE)
    document = bundle(admin)
    result = admin.put(
        "/api/config",
        json={"content": yaml.safe_dump(document, allow_unicode=True), "format": "json"},
    ).json()
    assert result["format"] == "json"
    assert json.loads(result["content"])["kind"] == "subremuxer.bundle"


def test_editing_templates_through_the_editor(admin):
    document = bundle(admin)
    before = len(document["templates"])
    document["templates"] = document["templates"][:2]
    document["templates"][0]["name"] = "Переименованный шаблон"

    result = admin.put(
        "/api/config", json={"content": yaml.safe_dump(document, allow_unicode=True)}
    ).json()
    assert len(result["summary"]["templates_removed"]) == before - 2

    templates = admin.get("/api/templates").json()
    assert len(templates) == 2
    assert templates[0]["name"] == "Переименованный шаблон"
    # Renaming a built-in must not turn it into a user template.
    assert templates[0]["builtin_id"] is not None


def test_settings_are_applied_through_the_editor(admin):
    document = bundle(admin)
    document["settings"]["default_hwid"] = "EDITEDhwid12345"
    admin.put("/api/config", json={"content": yaml.safe_dump(document, allow_unicode=True)})
    assert admin.get("/api/settings").json()["default_hwid"] == "EDITEDhwid12345"


def test_config_endpoints_need_a_session(client):
    assert client.get("/api/config").status_code == 401
    assert client.post("/api/config/validate", json={"content": ""}).status_code == 401
    assert client.put("/api/config", json={"content": ""}).status_code == 401
    assert client.get("/api/export").status_code == 401
    assert client.post("/api/import", json={"content": ""}).status_code == 401
