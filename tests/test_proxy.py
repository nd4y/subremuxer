"""End-to-end: a client hits /s/{token} and gets a filtered subscription back."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import yaml

from . import fixtures
from .conftest import ADMIN_PASSWORD

UPSTREAM_URL = "https://panel.example.org/sub/abc"
CONFIGURED_HWID = "UE42LJXu4DbiCaBv"

LTE_NOT_RU = {
    "mode": "builder",
    "match": "all",
    "conditions": [
        {"op": "contains", "value": "LTE"},
        {"op": "not_contains", "value": "RU"},
    ],
}


def upstream_serving(body: str, *, content_type="text/plain", status=200, extra_headers=None):
    """A mock upstream that also records what it was asked."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {"content-type": content_type}
        headers.update(extra_headers or {})
        return httpx.Response(status, text=body, headers=headers)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def make_profile(client, **overrides) -> dict:
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    payload = {
        "name": "test",
        "upstream_url": UPSTREAM_URL,
        "hwid": CONFIGURED_HWID,
        "hwid_mode": "override",
        "filter": LTE_NOT_RU,
        "protocols": [],
    }
    payload.update(overrides)
    response = client.post("/api/profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ per format


def test_uri_list_is_filtered(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client)

    response = client.get(f"/s/{profile['token']}")
    assert response.status_code == 200
    lines = response.text.splitlines()
    assert len(lines) == 2
    assert "NL-1" in lines[0]
    assert "FI-1" in lines[1]


def test_base64_stays_base64(make_client):
    handler = upstream_serving(fixtures.BASE64_LIST)
    client = make_client(handler)
    profile = make_profile(client)

    response = client.get(f"/s/{profile['token']}")
    decoded = base64.b64decode(response.text).decode()
    assert decoded.count("://") == 2
    assert "RU-1" not in decoded


def test_singbox_is_filtered_and_stays_loadable(make_client):
    handler = upstream_serving(fixtures.SINGBOX, content_type="application/json")
    client = make_client(handler)
    profile = make_profile(client)

    response = client.get(f"/s/{profile['token']}")
    assert response.headers["content-type"].startswith("application/json")
    doc = json.loads(response.text)
    tags = [ob["tag"] for ob in doc["outbounds"]]
    assert "NL-1 LTE" in tags
    assert "RU-1 LTE" not in tags
    selector = next(ob for ob in doc["outbounds"] if ob["tag"] == "proxy")
    assert "RU-1 LTE" not in selector["outbounds"]


def test_clash_is_filtered(make_client):
    handler = upstream_serving(fixtures.CLASH, content_type="text/yaml")
    client = make_client(handler)
    profile = make_profile(client)

    response = client.get(f"/s/{profile['token']}")
    doc = yaml.safe_load(response.text)
    assert [proxy["name"] for proxy in doc["proxies"]] == ["NL-1 LTE"]


def test_xray_config_list_is_filtered(make_client):
    handler = upstream_serving(fixtures.XRAY_CONFIG_LIST, content_type="application/json")
    client = make_client(handler)
    profile = make_profile(client)

    doc = json.loads(client.get(f"/s/{profile['token']}").text)
    assert [cfg["remarks"] for cfg in doc] == ["NL-1 LTE"]


# ----------------------------------------------------------------------- hwid


def test_configured_hwid_replaces_the_client_one(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client)

    client.get(f"/s/{profile['token']}", headers={"x-hwid": "CLIENTsHWID1234"})
    assert handler.seen[-1].headers["x-hwid"] == CONFIGURED_HWID


def test_fallback_mode_keeps_a_client_supplied_hwid(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client, hwid_mode="fallback")

    client.get(f"/s/{profile['token']}", headers={"x-hwid": "CLIENTsHWID1234"})
    assert handler.seen[-1].headers["x-hwid"] == "CLIENTsHWID1234"

    client.get(f"/s/{profile['token']}")
    assert handler.seen[-1].headers["x-hwid"] == CONFIGURED_HWID


def test_hwid_is_added_when_the_client_sent_none(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client)

    client.get(f"/s/{profile['token']}")
    assert handler.seen[-1].headers["x-hwid"] == CONFIGURED_HWID


def test_global_default_hwid_is_used_when_the_profile_has_none(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    client.put("/api/settings", json={"default_hwid": "GLOBALhwid98765", "default_device_os": "iOS"})
    profile = make_profile(client, hwid=None)

    client.get(f"/s/{profile['token']}")
    assert handler.seen[-1].headers["x-hwid"] == "GLOBALhwid98765"
    assert handler.seen[-1].headers["x-device-os"] == "iOS"


def test_client_user_agent_reaches_the_panel(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client)

    client.get(f"/s/{profile['token']}", headers={"user-agent": "Happ/2.0"})
    assert handler.seen[-1].headers["user-agent"] == "Happ/2.0"


def test_user_agent_override_forces_a_format_family(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client, upstream_ua="clash-verge/v2.0.0 mihomo")

    client.get(f"/s/{profile['token']}", headers={"user-agent": "Happ/2.0"})
    assert handler.seen[-1].headers["user-agent"] == "clash-verge/v2.0.0 mihomo"


def negotiating_upstream():
    """A panel that picks the response format from the User-Agent — as real ones do."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        ua = request.headers.get("user-agent", "").lower()
        if "happ" in ua:
            return httpx.Response(
                200, text=fixtures.XRAY_CONFIG_LIST, headers={"content-type": "application/json"}
            )
        if "clash" in ua or "mihomo" in ua:
            return httpx.Response(200, text=fixtures.CLASH, headers={"content-type": "text/yaml"})
        return httpx.Response(200, text=fixtures.BASE64_LIST)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def test_by_default_each_client_gets_the_format_it_can_read(make_client):
    """The NekoBox regression: forcing a User-Agent made the panel answer with an
    Xray-JSON array to a client that only parses base64, so it imported nothing.
    Passing the client's own User-Agent through is what prevents that — and HWID
    substitution does not depend on the User-Agent at all."""
    handler = negotiating_upstream()
    client = make_client(handler)
    profile = make_profile(client, upstream_ua=None, filter={"mode": "builder", "conditions": []})

    nekobox = client.get(f"/s/{profile['token']}", headers={"user-agent": "NekoBox/1.3.8"})
    assert base64.b64decode(nekobox.text).decode().startswith("vless://")
    assert handler.seen[-1].headers["user-agent"] == "NekoBox/1.3.8"
    # The whole point of the app still happens.
    assert handler.seen[-1].headers["x-hwid"] == CONFIGURED_HWID

    happ = client.get(f"/s/{profile['token']}", headers={"user-agent": "Happ/2.16.0"})
    assert json.loads(happ.text)[0]["remarks"]

    clash = client.get(f"/s/{profile['token']}", headers={"user-agent": "clash-verge/v2.0.3 mihomo"})
    assert yaml.safe_load(clash.text)["proxies"]


def test_forcing_a_user_agent_hands_every_client_the_same_format(make_client):
    """Documented trade-off, not a bug: this is what the override is for, and why
    the admin UI warns about it."""
    handler = negotiating_upstream()
    client = make_client(handler)
    profile = make_profile(
        client, upstream_ua="Happ/2.16.0", filter={"mode": "builder", "conditions": []}
    )

    body = client.get(f"/s/{profile['token']}", headers={"user-agent": "NekoBox/1.3.8"}).text
    assert json.loads(body)[0]["remarks"], "NekoBox asked, but the panel answered as if to Happ"


# ------------------------------------------------------------------ passthrough


def test_client_facing_headers_are_forwarded(make_client):
    handler = upstream_serving(
        fixtures.URI_LIST,
        extra_headers={
            "subscription-userinfo": "upload=0; download=10; total=100",
            "profile-title": "base64:TXkgcGxhbg==",
            "profile-update-interval": "12",
            "set-cookie": "leak=1",
        },
    )
    client = make_client(handler)
    profile = make_profile(client)

    response = client.get(f"/s/{profile['token']}")
    assert response.headers["subscription-userinfo"] == "upload=0; download=10; total=100"
    assert response.headers["profile-title"] == "base64:TXkgcGxhbg=="
    assert response.headers["profile-update-interval"] == "12"
    assert "leak=1" not in response.headers.get("set-cookie", "")


def test_html_landing_page_is_passed_through_untouched(make_client):
    handler = upstream_serving(fixtures.BROWSER_HTML, content_type="text/html")
    client = make_client(handler)
    profile = make_profile(client)

    response = client.get(f"/s/{profile['token']}")
    assert response.status_code == 200
    assert response.text == fixtures.BROWSER_HTML


def test_upstream_error_status_is_relayed(make_client):
    handler = upstream_serving("nope", status=404)
    client = make_client(handler)
    profile = make_profile(client)

    assert client.get(f"/s/{profile['token']}").status_code == 404


def test_upstream_failure_becomes_502(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    profile = make_profile(client)
    assert client.get(f"/s/{profile['token']}").status_code == 502


# ---------------------------------------------------------------------- tokens


def test_unknown_token_is_404(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    assert client.get("/s/does-not-exist").status_code == 404


def test_disabled_profile_is_404(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client)
    client.put(
        f"/api/profiles/{profile['id']}",
        json={
            "name": "test",
            "upstream_url": UPSTREAM_URL,
            "hwid": CONFIGURED_HWID,
            "filter": LTE_NOT_RU,
            "enabled": False,
        },
    )
    assert client.get(f"/s/{profile['token']}").status_code == 404
    assert not handler.seen, "a disabled profile must not touch the panel at all"


def test_rotating_the_token_invalidates_the_old_link(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client)
    old_token = profile["token"]
    client.post(f"/api/profiles/{profile['id']}/rotate-token")
    assert client.get(f"/s/{old_token}").status_code == 404


def test_a_path_suffix_is_ignored(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client)
    assert client.get(f"/s/{profile['token']}/subscription.txt").status_code == 200


# ------------------------------------------------------------------ formatting


@pytest.mark.parametrize(
    ("output_format", "expect_base64"),
    [("base64", True), ("plain", False)],
)
def test_output_format_switches_the_envelope(make_client, output_format, expect_base64):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client, output_format=output_format)
    body = client.get(f"/s/{profile['token']}").text
    assert body.startswith("vless://") is not expect_base64


def test_format_query_parameter_overrides_the_profile(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client)
    body = client.get(f"/s/{profile['token']}?format=base64").text
    assert "://" not in body
    assert base64.b64decode(body).decode().startswith("vless://")


# ---------------------------------------------------------------------- caching


def test_cache_ttl_prevents_a_second_upstream_hit(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client, cache_ttl=60)

    client.get(f"/s/{profile['token']}")
    client.get(f"/s/{profile['token']}")
    assert len(handler.seen) == 1


def test_a_different_hwid_never_shares_a_cached_body(make_client):
    handler = upstream_serving(fixtures.URI_LIST)
    client = make_client(handler)
    profile = make_profile(client, cache_ttl=60, hwid_mode="passthrough", hwid=None)

    client.get(f"/s/{profile['token']}", headers={"x-hwid": "DEVICEone12345"})
    client.get(f"/s/{profile['token']}", headers={"x-hwid": "DEVICEtwo12345"})
    assert len(handler.seen) == 2


# ------------------------------------------------------------------- logging


def test_the_log_records_what_was_dropped_and_why(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client)
    client.get(f"/s/{profile['token']}", headers={"user-agent": "Happ/2.0"})

    entries = client.get("/api/logs").json()["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["profile_name"] == "test"
    assert entry["user_agent"] == "Happ/2.0"
    assert entry["hwid_sent"] == CONFIGURED_HWID
    assert entry["hwid_action"] == "added"
    assert entry["detected_format"] == "uri_list"
    assert entry["nodes_total"] == 5
    assert entry["nodes_kept"] == 2
    assert entry["status_code"] == 200

    nodes = client.get(f"/api/logs/{entry['id']}/nodes").json()["nodes"]
    assert len(nodes) == 5
    by_name = {node["name"]: node for node in nodes}
    assert by_name["NL-1 LTE"]["kept"] is True
    assert by_name["RU-1 LTE"]["kept"] is False
    assert "не содержит" in by_name["RU-1 LTE"]["reason"]
    assert by_name["DE-1"]["kept"] is False


def test_protocol_rejections_are_explained_in_the_log(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client, protocols=["vless"], filter={"mode": "builder", "conditions": []})
    client.get(f"/s/{profile['token']}")

    entry = client.get("/api/logs").json()["entries"][0]
    nodes = client.get(f"/api/logs/{entry['id']}/nodes").json()["nodes"]
    dropped = {node["name"]: node["reason"] for node in nodes if not node["kept"]}
    assert "протокол trojan не разрешён" in dropped["DE-1"]
    assert "протокол hysteria2 не разрешён" in dropped["SE-1"]


def test_upstream_errors_are_logged(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    profile = make_profile(client)
    client.get(f"/s/{profile['token']}")

    entry = client.get("/api/logs").json()["entries"][0]
    assert entry["status_code"] == 502
    assert "не удалось получить подписку" in entry["error"]


def test_logs_can_be_filtered_and_cleared(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client)
    client.get(f"/s/{profile['token']}")

    assert client.get("/api/logs", params={"profile_id": profile["id"]}).json()["entries"]
    assert client.get("/api/logs", params={"only_errors": True}).json()["entries"] == []
    assert client.delete("/api/logs").json()["deleted"] == 1
    assert client.get("/api/logs").json()["entries"] == []


def test_stats_summarise_the_last_day(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    profile = make_profile(client)
    client.get(f"/s/{profile['token']}")

    stats = client.get("/api/stats").json()
    assert stats["requests_24h"] == 1
    assert stats["errors_24h"] == 0
    assert stats["nodes_seen_24h"] == 5
    assert stats["nodes_served_24h"] == 2
    assert stats["profiles_total"] == 1


# ---------------------------------------------------------------- filter test


def test_filter_test_endpoint_previews_against_the_real_upstream(make_client):
    client = make_client(upstream_serving(fixtures.URI_LIST))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})

    response = client.post(
        "/api/filter/test",
        json={
            "upstream_url": UPSTREAM_URL,
            "hwid": CONFIGURED_HWID,
            "filter": LTE_NOT_RU,
            "protocols": [],
        },
    )
    body = response.json()
    assert body["detected_format"] == "uri_list"
    assert body["total"] == 5
    assert body["kept"] == 2
    assert body["hwid_sent"] == CONFIGURED_HWID
    assert body["regex"] == r"(?i)^(?=.*LTE)(?!.*RU).*$"
    kept_names = [node["name"] for node in body["nodes"] if node["kept"]]
    assert kept_names == ["NL-1 LTE", "FI-1 LTE"]


def test_filter_test_reports_an_unreachable_upstream(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    body = client.post(
        "/api/filter/test", json={"upstream_url": UPSTREAM_URL, "filter": LTE_NOT_RU}
    ).json()
    assert body["error"]
    assert body["nodes"] == []
