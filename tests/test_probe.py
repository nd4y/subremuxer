"""The capture endpoint: a fake subscription whose only job is to record headers."""

from __future__ import annotations

import base64
from urllib.parse import unquote

HEADERS = {
    "user-agent": "Happ/2.16.0",
    "x-hwid": "UE42LJXu4DbiCaBv",
    "x-device-os": "Android",
    "x-ver-os": "15",
    "x-device-model": "Pixel 9",
}


def probe_url(admin) -> str:
    return f"/probe/{admin.get('/api/probe').json()['token']}"


def node_names(body: str) -> list[str]:
    """Node labels live in the URI fragment, percent-encoded as they must be."""
    decoded = base64.b64decode(body).decode()
    return [unquote(line.rsplit("#", 1)[1]) for line in decoded.splitlines() if "#" in line]


def test_the_probe_url_is_stable_and_shown_with_a_qr(admin):
    first = admin.get("/api/probe").json()
    assert first["token"]
    assert first["url"].endswith(f"/probe/{first['token']}")
    assert admin.get("/api/probe").json()["token"] == first["token"]
    assert admin.get("/api/probe/qr.svg").headers["content-type"].startswith("image/svg+xml")


def test_a_wrong_token_is_404(admin):
    assert admin.get("/probe/not-the-real-token").status_code == 404


def test_the_capture_endpoint_needs_no_auth(client):
    # A client adding the subscription is of course not logged in.
    assert client.get("/probe/whatever").status_code == 404


def test_headers_are_recorded(admin):
    assert admin.get(probe_url(admin), headers=HEADERS).status_code == 200

    captures = admin.get("/api/probe").json()["captures"]
    assert len(captures) == 1
    capture = captures[0]
    assert capture["hwid"] == HEADERS["x-hwid"]
    assert capture["device_os"] == "Android"
    assert capture["device_ver"] == "15"
    assert capture["device_model"] == "Pixel 9"
    assert capture["user_agent"] == "Happ/2.16.0"
    assert capture["seen_count"] == 1


def test_the_client_gets_its_own_hwid_back_as_a_server_name(admin):
    response = admin.get(probe_url(admin), headers=HEADERS)
    names = node_names(response.text)
    assert any(HEADERS["x-hwid"] in name for name in names)
    assert any("Pixel 9" in name for name in names)
    assert base64.b64decode(response.text).decode().startswith("vless://")
    assert response.headers["profile-title"].startswith("base64:")


def test_a_client_without_hwid_is_told_so(admin):
    names = node_names(admin.get(probe_url(admin), headers={"user-agent": "curl/8"}).text)
    assert any("не прислал HWID" in name for name in names)


def test_repeat_visits_are_counted_not_duplicated(admin):
    url = probe_url(admin)
    for _ in range(3):
        admin.get(url, headers=HEADERS)

    captures = admin.get("/api/probe").json()["captures"]
    assert len(captures) == 1
    assert captures[0]["seen_count"] == 3
    assert captures[0]["last_ts"] >= captures[0]["first_ts"]


def test_different_devices_get_their_own_rows(admin):
    url = probe_url(admin)
    admin.get(url, headers=HEADERS)
    admin.get(url, headers={**HEADERS, "x-hwid": "SECONDdevice123", "x-device-model": "Pixel 8"})

    captures = admin.get("/api/probe").json()["captures"]
    assert len(captures) == 2
    assert {capture["hwid"] for capture in captures} == {"UE42LJXu4DbiCaBv", "SECONDdevice123"}


def test_a_browser_gets_a_readable_page(admin):
    response = admin.get(probe_url(admin), headers={**HEADERS, "accept": "text/html"})
    assert response.headers["content-type"].startswith("text/html")
    assert "Данные захвачены" in response.text
    assert HEADERS["x-hwid"] in response.text


def test_a_browser_without_hwid_is_warned(admin):
    response = admin.get(probe_url(admin), headers={"accept": "text/html", "user-agent": "Mozilla/5.0"})
    assert "x-hwid" in response.text
    assert "подставляет HWID сам" in response.text


def test_html_is_escaped(admin):
    response = admin.get(
        probe_url(admin),
        headers={"accept": "text/html", "user-agent": "<script>alert(1)</script>"},
    )
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_a_path_suffix_is_ignored(admin):
    assert admin.get(f"{probe_url(admin)}/subscription.txt", headers=HEADERS).status_code == 200
    assert len(admin.get("/api/probe").json()["captures"]) == 1


def test_captures_can_be_removed_one_by_one_and_all_at_once(admin):
    url = probe_url(admin)
    admin.get(url, headers=HEADERS)
    admin.get(url, headers={**HEADERS, "x-hwid": "SECONDdevice123"})

    captures = admin.get("/api/probe").json()["captures"]
    assert admin.delete(f"/api/probe/captures?capture_id={captures[0]['id']}").json()["deleted"] == 1
    assert len(admin.get("/api/probe").json()["captures"]) == 1

    assert admin.delete("/api/probe/captures").json()["deleted"] == 1
    assert admin.get("/api/probe").json()["captures"] == []


def test_a_deleted_capture_can_be_restored(admin):
    url = probe_url(admin)
    admin.get(url, headers=HEADERS)
    admin.get(url, headers=HEADERS)

    captured = admin.get("/api/probe").json()["captures"][0]
    assert captured["seen_count"] == 2

    admin.delete(f"/api/probe/captures?capture_id={captured['id']}")
    assert admin.get("/api/probe").json()["captures"] == []

    restored = admin.post("/api/probe/captures/restore", json={"captures": [captured]})
    assert restored.json()["restored"] == 1

    back = admin.get("/api/probe").json()["captures"][0]
    assert back["hwid"] == HEADERS["x-hwid"]
    assert back["device_model"] == "Pixel 9"
    # Timestamps and the counter survive, so the row is not silently reset.
    assert back["seen_count"] == 2
    assert back["first_ts"] == captured["first_ts"]
    assert back["last_ts"] == captured["last_ts"]
    assert back["headers"] == captured["headers"]


def test_clearing_everything_can_be_restored_in_one_go(admin):
    url = probe_url(admin)
    admin.get(url, headers=HEADERS)
    admin.get(url, headers={**HEADERS, "x-hwid": "SECONDdevice123", "x-device-model": "Pixel 8"})

    captures = admin.get("/api/probe").json()["captures"]
    assert len(captures) == 2

    admin.delete("/api/probe/captures")
    assert admin.get("/api/probe").json()["captures"] == []

    assert admin.post("/api/probe/captures/restore", json={"captures": captures}).json()[
        "restored"
    ] == 2
    assert len(admin.get("/api/probe").json()["captures"]) == 2


def test_restoring_skips_a_device_that_came_back_on_its_own(admin):
    """The undo window is seven seconds; a polling client can reappear inside it."""
    url = probe_url(admin)
    admin.get(url, headers=HEADERS)
    captured = admin.get("/api/probe").json()["captures"][0]

    admin.delete("/api/probe/captures")
    admin.get(url, headers=HEADERS)  # the same device checks in again

    result = admin.post("/api/probe/captures/restore", json={"captures": [captured]})
    assert result.json()["restored"] == 0
    assert len(admin.get("/api/probe").json()["captures"]) == 1, "no duplicate row"


def test_restore_rejects_a_non_list(admin):
    assert admin.post("/api/probe/captures/restore", json={"captures": "nope"}).status_code == 400


def test_restore_ignores_junk_entries(admin):
    result = admin.post("/api/probe/captures/restore", json={"captures": ["nope", 42, None]})
    assert result.json()["restored"] == 0


def test_restore_needs_a_session(client):
    assert client.post("/api/probe/captures/restore", json={"captures": []}).status_code == 401


def test_rotating_the_token_breaks_the_old_link(admin):
    old = probe_url(admin)
    rotated = admin.post("/api/probe/rotate").json()
    assert admin.get(old, headers=HEADERS).status_code == 404
    assert admin.get(f"/probe/{rotated['token']}", headers=HEADERS).status_code == 200


def test_probe_admin_endpoints_need_a_session(client):
    assert client.get("/api/probe").status_code == 401
    assert client.post("/api/probe/rotate").status_code == 401
    assert client.delete("/api/probe/captures").status_code == 401


def test_only_interesting_headers_are_kept(admin):
    admin.get(probe_url(admin), headers={**HEADERS, "cookie": "secret=1", "x-secret": "nope"})
    stored = admin.get("/api/probe").json()["captures"][0]["headers"]
    assert "cookie" not in stored
    assert "x-secret" not in stored
    assert stored["x-hwid"] == HEADERS["x-hwid"]
