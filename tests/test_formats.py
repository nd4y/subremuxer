from __future__ import annotations

import base64
import json

import pytest
import yaml

from app.formats import SubFormat, UnknownFormatError, detect_and_parse

from . import fixtures


def names(parsed) -> list[str]:
    return [node.name for node in parsed.nodes]


def test_detects_every_format():
    assert detect_and_parse(fixtures.URI_LIST).format is SubFormat.URI_LIST
    assert detect_and_parse(fixtures.BASE64_LIST).format is SubFormat.BASE64
    assert detect_and_parse(fixtures.SINGBOX).format is SubFormat.SINGBOX
    assert detect_and_parse(fixtures.XRAY_SINGLE).format is SubFormat.XRAY_JSON
    assert detect_and_parse(fixtures.XRAY_CONFIG_LIST).format is SubFormat.XRAY_JSON
    assert detect_and_parse(fixtures.CLASH).format is SubFormat.CLASH


def test_html_is_not_a_subscription():
    with pytest.raises(UnknownFormatError):
        detect_and_parse(fixtures.BROWSER_HTML)


def test_empty_body_rejected():
    with pytest.raises(UnknownFormatError):
        detect_and_parse("   \n  ")


# ------------------------------------------------------------------ uri lists


def test_uri_list_names_and_protocols():
    parsed = detect_and_parse(fixtures.URI_LIST)
    assert names(parsed) == ["NL-1 LTE", "RU-1 LTE", "DE-1", "FI-1 LTE", "SE-1"]
    assert [node.protocol for node in parsed.nodes] == [
        "vless",
        "vless",
        "trojan",
        "shadowsocks",
        "hysteria2",
    ]


def test_uri_list_keeps_everything_unchanged_when_nothing_is_filtered():
    parsed = detect_and_parse(fixtures.URI_LIST)
    assert parsed.render(range(len(parsed.nodes))) == fixtures.URI_LIST


def test_uri_list_drops_only_the_requested_lines():
    parsed = detect_and_parse(fixtures.URI_LIST)
    rendered = parsed.render([0, 2])
    lines = rendered.splitlines()
    assert len(lines) == 2
    assert "NL-1" in lines[0]
    assert "DE-1" in lines[1]


def test_base64_round_trip_stays_base64():
    parsed = detect_and_parse(fixtures.BASE64_LIST)
    rendered = parsed.render([0])
    decoded = base64.b64decode(rendered).decode()
    assert decoded.count("://") == 1
    assert "NL-1" in decoded


def test_base64_envelope_can_be_switched_to_plain():
    parsed = detect_and_parse(fixtures.BASE64_LIST)
    parsed.set_base64(False)
    rendered = parsed.render([0, 1])
    assert rendered.startswith("vless://")
    assert len(rendered.splitlines()) == 2


def test_vmess_label_comes_from_the_payload():
    payload = base64.b64encode(
        json.dumps({"v": "2", "ps": "Tokyo LTE", "add": "jp.example.net", "port": "443"}).encode()
    ).decode()
    parsed = detect_and_parse(f"vmess://{payload}")
    assert names(parsed) == ["Tokyo LTE"]
    assert parsed.nodes[0].protocol == "vmess"


def test_non_uri_lines_survive_filtering():
    body = "# comment line\n" + fixtures.URI_LIST
    parsed = detect_and_parse(body)
    assert len(parsed.nodes) == 5
    rendered = parsed.render([0])
    assert rendered.splitlines()[0] == "# comment line"


# ------------------------------------------------------------------- sing-box


def test_singbox_nodes():
    parsed = detect_and_parse(fixtures.SINGBOX)
    assert names(parsed) == ["NL-1 LTE", "RU-1 LTE", "DE-1"]


def test_singbox_prunes_group_members_of_removed_nodes():
    parsed = detect_and_parse(fixtures.SINGBOX)
    doc = json.loads(parsed.render([0]))
    tags = [ob["tag"] for ob in doc["outbounds"]]
    assert "RU-1 LTE" not in tags
    assert "DE-1" not in tags
    selector = next(ob for ob in doc["outbounds"] if ob["tag"] == "proxy")
    assert selector["outbounds"] == ["auto", "NL-1 LTE"]
    urltest = next(ob for ob in doc["outbounds"] if ob["tag"] == "auto")
    assert urltest["outbounds"] == ["NL-1 LTE"]


def test_singbox_removes_groups_left_empty_and_repoints_final():
    parsed = detect_and_parse(fixtures.SINGBOX)
    doc = json.loads(parsed.render([]))
    tags = [ob.get("tag") for ob in doc["outbounds"]]
    assert "proxy" not in tags
    assert "auto" not in tags
    assert "direct" in tags
    assert doc["route"]["final"] == "direct"


def test_singbox_selector_default_is_repointed_when_it_disappears():
    body = json.dumps(
        {
            "outbounds": [
                {"type": "vless", "tag": "A"},
                {"type": "vless", "tag": "B"},
                {"type": "selector", "tag": "proxy", "outbounds": ["A", "B"], "default": "A"},
            ]
        }
    )
    parsed = detect_and_parse(body)
    doc = json.loads(parsed.render([1]))
    selector = next(ob for ob in doc["outbounds"] if ob["tag"] == "proxy")
    assert selector["default"] == "B"


def test_singbox_endpoints_are_nodes_too():
    body = json.dumps(
        {
            "outbounds": [{"type": "vless", "tag": "A"}],
            "endpoints": [{"type": "wireguard", "tag": "WG-1"}],
        }
    )
    parsed = detect_and_parse(body)
    assert names(parsed) == ["A", "WG-1"]
    doc = json.loads(parsed.render([0]))
    assert doc["endpoints"] == []


# ----------------------------------------------------------------- xray  json


def test_xray_single_config_nodes():
    parsed = detect_and_parse(fixtures.XRAY_SINGLE)
    assert names(parsed) == ["NL-1 LTE", "RU-1 LTE", "DE-1"]
    assert parsed.nodes[0].server == "nl1.example.net"


def test_xray_single_config_drops_routing_rules_for_removed_tags():
    parsed = detect_and_parse(fixtures.XRAY_SINGLE)
    doc = json.loads(parsed.render([0]))
    tags = [ob["tag"] for ob in doc["outbounds"]]
    assert tags == ["NL-1 LTE", "direct"]
    outbound_tags = [rule["outboundTag"] for rule in doc["routing"]["rules"]]
    assert outbound_tags == ["direct"]


def test_xray_config_list_is_filtered_per_config():
    parsed = detect_and_parse(fixtures.XRAY_CONFIG_LIST)
    assert names(parsed) == ["NL-1 LTE", "RU-1 LTE", "DE-1"]
    doc = json.loads(parsed.render([0, 2]))
    assert [cfg["remarks"] for cfg in doc] == ["NL-1 LTE", "DE-1"]


# ---------------------------------------------------------------------- clash


def test_clash_nodes():
    parsed = detect_and_parse(fixtures.CLASH)
    assert names(parsed) == ["NL-1 LTE", "RU-1 LTE", "DE-1"]


def test_clash_prunes_groups_and_repoints_rules():
    parsed = detect_and_parse(fixtures.CLASH)
    doc = yaml.safe_load(parsed.render([0]))
    assert [proxy["name"] for proxy in doc["proxies"]] == ["NL-1 LTE"]

    groups = {group["name"]: group for group in doc["proxy-groups"]}
    assert "RU-ONLY" not in groups, "a group left with no members must be removed"
    assert groups["PROXY"]["proxies"] == ["AUTO", "NL-1 LTE"]
    assert groups["AUTO"]["proxies"] == ["NL-1 LTE"]
    assert "DOMAIN-SUFFIX,example.ru,DIRECT" in doc["rules"]
    assert "MATCH,PROXY" in doc["rules"]


def test_clash_group_backed_by_a_provider_survives_losing_every_proxy():
    body = yaml.safe_dump(
        {
            "proxies": [{"name": "A", "type": "vless"}],
            "proxy-groups": [
                {"name": "G", "type": "select", "proxies": ["A"], "use": ["provider1"]}
            ],
        }
    )
    parsed = detect_and_parse(body)
    doc = yaml.safe_load(parsed.render([]))
    assert [group["name"] for group in doc["proxy-groups"]] == ["G"]


def test_clash_untouched_when_nothing_is_filtered():
    parsed = detect_and_parse(fixtures.CLASH)
    doc = yaml.safe_load(parsed.render(range(len(parsed.nodes))))
    assert doc == yaml.safe_load(fixtures.CLASH)
