from __future__ import annotations

import pytest

from app.upstream import (
    FORWARD_RESPONSE_HEADERS,
    HwidPlan,
    UpstreamRequest,
    hwid_is_valid,
    passthrough_response_headers,
    plan_hwid,
)

CONFIGURED = "CONFIGUREDhwid123"
INCOMING = "INCOMINGhwid456"


@pytest.mark.parametrize(
    ("mode", "incoming", "expected_sent", "expected_action"),
    [
        ("override", INCOMING, CONFIGURED, "replaced"),
        ("override", None, CONFIGURED, "added"),
        ("fallback", INCOMING, INCOMING, "kept"),
        ("fallback", None, CONFIGURED, "added"),
        ("passthrough", INCOMING, INCOMING, "kept"),
        ("passthrough", None, None, "none"),
    ],
)
def test_hwid_modes(mode, incoming, expected_sent, expected_action):
    plan = plan_hwid(mode, CONFIGURED, incoming)
    assert plan.hwid_sent == expected_sent
    assert plan.action == expected_action


def test_without_a_configured_hwid_the_client_value_is_left_alone():
    plan = plan_hwid("override", None, INCOMING)
    assert plan.hwid_sent == INCOMING
    assert plan.action == "kept"


def test_blank_incoming_hwid_is_treated_as_absent():
    plan = plan_hwid("fallback", CONFIGURED, "   ")
    assert plan.hwid_sent == CONFIGURED
    assert plan.action == "added"


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("UE42LJXu4DbiCaBv", True),
        ("abc-DEF=123456789", True),
        ("short", False),
        ("x" * 65, False),
        ("has space here", False),
        ("has_underscore1234", False),
        (None, False),
    ],
)
def test_hwid_shape_matches_the_panel_rule(value, valid):
    assert hwid_is_valid(value) is valid


# ------------------------------------------------------------------- headers


def base_request(**kwargs) -> UpstreamRequest:
    defaults = {
        "url": "https://panel.example.org/sub/abc",
        "client_headers": {
            "User-Agent": "Happ/2.0",
            "Accept": "*/*",
            "Cookie": "session=secret",
            "Host": "sub.example.org",
            "X-Hwid": INCOMING,
            "Accept-Encoding": "gzip, br",
        },
        "hwid_plan": HwidPlan(INCOMING, CONFIGURED, "replaced"),
    }
    defaults.update(kwargs)
    return UpstreamRequest(**defaults)


def test_client_headers_are_forwarded_selectively():
    headers = base_request().build_headers()
    assert headers["user-agent"] == "Happ/2.0"
    assert headers["accept"] == "*/*"
    assert "cookie" not in headers
    assert "host" not in headers
    assert "accept-encoding" not in headers


def test_hwid_is_replaced_in_the_outgoing_request():
    headers = base_request().build_headers()
    assert headers["x-hwid"] == CONFIGURED


def test_hwid_header_is_removed_when_there_is_nothing_to_send():
    headers = base_request(hwid_plan=HwidPlan(None, None, "none")).build_headers()
    assert "x-hwid" not in headers


def test_device_headers_are_added_when_configured():
    headers = base_request(
        device_os="iOS", device_ver="18.3", device_model="iPhone 14 Pro Max"
    ).build_headers()
    assert headers["x-device-os"] == "iOS"
    assert headers["x-ver-os"] == "18.3"
    assert headers["x-device-model"] == "iPhone 14 Pro Max"


def test_user_agent_override_wins_over_the_client_one():
    headers = base_request(user_agent_override="clash-verge/v2.0.0 mihomo").build_headers()
    assert headers["user-agent"] == "clash-verge/v2.0.0 mihomo"


def test_a_user_agent_is_always_sent():
    headers = base_request(client_headers={}).build_headers()
    assert headers["user-agent"] == "subremuxer"


def test_only_client_facing_response_headers_are_passed_back():
    upstream = {
        "subscription-userinfo": "upload=0; download=10; total=100",
        "profile-title": "base64:TXkgcGxhbg==",
        "profile-update-interval": "12",
        "announce": "base64:aGk=",
        "x-hwid-active": "true",
        "set-cookie": "leak=1",
        "server": "nginx",
        "content-length": "999",
    }
    passed = passthrough_response_headers(upstream)
    assert "set-cookie" not in passed
    assert "server" not in passed
    assert "content-length" not in passed
    assert passed["subscription-userinfo"] == upstream["subscription-userinfo"]
    assert passed["profile-title"] == upstream["profile-title"]
    assert passed["x-hwid-active"] == "true"
    assert set(passed) <= set(FORWARD_RESPONSE_HEADERS)
