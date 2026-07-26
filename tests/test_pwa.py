"""Installability, the service worker, and the displayed product name."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app import APP_NAME

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


# ------------------------------------------------------------------ manifest


def test_manifest_is_served_with_the_right_media_type(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")


def test_manifest_meets_the_installability_bar(client):
    manifest = json.loads(client.get("/manifest.webmanifest").text)
    assert manifest["name"] == APP_NAME
    assert manifest["short_name"] == APP_NAME
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"

    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes, "Chrome requires both to offer installation"
    purposes = {icon.get("purpose") for icon in manifest["icons"]}
    assert "maskable" in purposes, "Android crops icons without a maskable variant"


def test_every_manifest_icon_exists_and_is_a_png(client):
    manifest = json.loads(client.get("/manifest.webmanifest").text)
    for icon in manifest["icons"]:
        response = client.get(icon["src"])
        assert response.status_code == 200, icon["src"]
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n", icon["src"]


def test_manifest_shortcut_targets_are_real_routes(client):
    manifest = json.loads(client.get("/manifest.webmanifest").text)
    assert {item["url"] for item in manifest["shortcuts"]} <= {
        "/#/profiles",
        "/#/probe",
        "/#/logs",
        "/#/settings",
    }


def test_the_apple_touch_icon_is_served_from_the_root(client):
    response = client.get("/apple-touch-icon.png")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_shell_links_the_manifest_and_icons(client):
    html = client.get("/").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'rel="apple-touch-icon"' in html


# ------------------------------------------------------------ service worker


def test_the_worker_is_served_from_the_root_scope(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert "no-cache" in response.headers["cache-control"]
    assert response.headers["content-type"].startswith("application/javascript")


def test_the_worker_cache_is_stamped_with_the_asset_version(client):
    body = client.get("/sw.js").text
    assert "__VERSION__" not in body, "the placeholder must be substituted"
    match = re.search(r'const VERSION = "([0-9a-f]{10})"', body)
    assert match

    from app import main

    assert match.group(1) == main.assets_version()


def test_the_asset_version_changes_when_an_asset_does(tmp_path, monkeypatch):
    from app import main

    before = main.assets_version()
    copy = tmp_path / "static"
    copy.mkdir()
    for name in main.VERSIONED_ASSETS:
        (copy / name).write_text("// changed", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", copy)
    assert main.assets_version() != before


def test_the_worker_never_intercepts_the_proxy_or_the_api():
    source = (STATIC / "sw.js").read_text(encoding="utf-8")
    # Caching a subscription would hand a stale server list to a client, and
    # caching the capture endpoint would lose the very request it exists to see.
    for path in ('"/api/"', '"/s/"', '"/probe/"', '"/healthz"'):
        assert path in source, path


def test_the_worker_precaches_enough_to_open_offline():
    source = (STATIC / "sw.js").read_text(encoding="utf-8")
    for asset in ('"/"', "styles.css", "app.js", "help.js", "manifest.webmanifest"):
        assert asset in source, asset


# ------------------------------------------------------------- back gesture


def test_sheets_use_close_watcher_for_the_predictive_back_gesture():
    """CloseWatcher is what lets Android preview what a back gesture will close;
    without it the gesture still works, but shows no preview."""
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "new CloseWatcher()" in source
    assert 'typeof window.CloseWatcher === "function"' in source
    # And the history-based path has to survive as the fallback.
    assert 'history.pushState({ sheet: true }, "")' in source
    assert "fromWatcher" in source


def test_escape_is_not_handled_twice_when_a_watcher_is_active():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'event.key === "Escape" && sheet.open && !sheet.watcher' in source


# -------------------------------------------------------------------- name


def test_the_visible_name_is_used_everywhere_it_is_shown(client):
    html = client.get("/").text
    assert f"<title>{APP_NAME}</title>" in html
    assert f'<h1 class="login__title">{APP_NAME}</h1>' in html
    assert f'content="{APP_NAME}"' in html


def test_identifiers_keep_the_old_slug(client):
    """Renaming these would log everyone out or break existing config files."""
    from app.portability import KIND_BUNDLE, KIND_PROFILE
    from app.security import SESSION_COOKIE

    assert SESSION_COOKIE == "subremuxer_session"
    assert KIND_BUNDLE == "subremuxer.bundle"
    assert KIND_PROFILE == "subremuxer.profile"


def test_the_capture_page_shows_the_new_name(admin):
    token = admin.get("/api/probe").json()["token"]
    response = admin.get(f"/probe/{token}", headers={"accept": "text/html"})
    assert f"<title>{APP_NAME} — захват клиента</title>" in response.text
