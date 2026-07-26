"""The HTML shell: asset versioning and the help content it loads."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_shell_is_served_without_caching(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]


def test_asset_urls_carry_a_content_hash(client):
    """Without this, a browser keeps running the previous release's JS after the
    container is updated, with no sign that anything is stale."""
    html = client.get("/").text
    for asset in ("app.js", "help.js", "styles.css"):
        match = re.search(rf"/static/{re.escape(asset)}\?v=([0-9a-f]{{10}})", html)
        assert match, f"{asset} is not versioned"


def test_the_hash_changes_with_the_file(client, tmp_path, monkeypatch):
    from app import main

    first = main._asset_hash("app.js")
    assert first == main._asset_hash("app.js"), "the same bytes must hash the same"

    copy = tmp_path / "static"
    copy.mkdir()
    (copy / "app.js").write_text("// different", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", copy)
    assert main._asset_hash("app.js") != first


def test_a_missing_asset_does_not_break_the_shell(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    assert main._asset_hash("nope.js") == "0"


def test_assets_are_reachable(client):
    for asset in ("app.js", "help.js", "styles.css"):
        assert client.get(f"/static/{asset}").status_code == 200
    assert client.get("/favicon.svg").status_code == 200


# --------------------------------------------------------------------- help


def read_help() -> str:
    return (STATIC / "help.js").read_text(encoding="utf-8")


def test_the_shell_loads_the_help_file_before_the_app():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index("help.js") < html.index("app.js")


def test_the_login_screen_offers_both_methods():
    """Both are rendered up front and hidden by role — the alternative is
    building the screen twice and letting the two drift."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for element in ('id="login-oidc"', 'id="login-form"', 'id="login-divider"', 'id="login-note"'):
        assert element in html, element


def test_the_interface_reads_the_methods_from_the_server():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "me.methods" in source
    assert "/auth/oidc/login" in source


def test_the_escape_hatch_is_spelled_the_way_grafana_spells_it():
    """People arrive here with the habit already formed."""
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "disableAutoLogin" in source
    from app.routers.auth import ESCAPE_HATCH

    assert ESCAPE_HATCH == "disableAutoLogin=true"


def test_signing_out_disarms_automatic_sign_in():
    """Otherwise the redirect would hand back the session just ended."""
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    logout = source[source.index('for (const id of ["topbar-logout"') :][:600]
    assert "suppressAutoLogin()" in logout


def test_a_viewer_gets_its_own_help():
    source = read_help()
    assert "HELP_SECTIONS_VIEWER" in source
    viewer = source[source.index("HELP_SECTIONS_VIEWER") :]
    ids = set(re.findall(r'id:\s*"([\w-]+)"', viewer))
    assert {"start", "qr", "update", "privacy", "trouble"} <= ids


def test_the_viewer_help_stays_out_of_the_admin_subject_matter():
    """Half an explanation of a setting they cannot reach is worse than none."""
    source = read_help()
    viewer = source[source.index("HELP_SECTIONS_VIEWER") :]
    for topic in ("HWID", "мимикри", "регуляр", "User-Agent", "апстрим", "журнал"):
        assert topic not in viewer, topic
    # And it must not embed the playground, which calls an admin-only endpoint.
    assert "demo:" not in viewer


def test_help_covers_every_area_of_the_app():
    source = read_help()
    ids = set(re.findall(r'id:\s*"([\w-]+)"', source))
    assert {
        "start",
        "flow",
        "mimicry",
        "filter",
        "protocols",
        "capture",
        "templates",
        "formats",
        "editor",
        "logs",
        "access",
        "trouble",
    } <= ids


def test_help_explains_the_regression_that_prompted_the_split():
    source = read_help()
    assert "NekoBox" in source
    assert "base64" in source
    assert "User-Agent" in source


def test_help_documents_the_hwid_shape_the_panel_enforces():
    assert "10–64" in read_help()


def test_help_has_a_runnable_filter_example():
    assert 'demo: "filter"' in read_help()


def test_the_demo_is_backed_by_a_real_endpoint(admin):
    """The playground in the help is not a mock — it calls the same dry-run the
    profile editor uses, so it can never drift from the real behaviour."""
    response = admin.post(
        "/api/filter/dry-run",
        json={
            "filter": {
                "mode": "builder",
                "match": "all",
                "conditions": [
                    {"op": "contains", "value": "LTE"},
                    {"op": "not_contains", "value": "RU"},
                ],
            },
            "names": [
                "🇳🇱 NL-1 LTE",
                "🇷🇺 RU-1 LTE",
                "🇷🇺 RU-2 Home",
                "🇩🇪 DE-1 LTE",
                "🇸🇪 SE-1",
                "Трафик: осталось 42 ГБ",
            ],
        },
    )
    body = response.json()
    kept = [item["name"] for item in body["results"] if item["kept"]]
    # Exactly what the help text promises this example produces.
    assert kept == ["🇳🇱 NL-1 LTE", "🇩🇪 DE-1 LTE"]
    assert body["regex"] == r"(?i)^(?=.*LTE)(?!.*RU).*$"
