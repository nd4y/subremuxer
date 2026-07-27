"""Aggregates: several profiles served under one link.

The point of these tests is the seam between "each source is filtered on its own
terms" and "the client gets one document it can actually load". So they check
both halves: that a source's own filter, HWID and prefix survive the merge, and
that the merged document is still a valid config of its family — unique tags,
groups pointing at nodes that exist, routing that does not reference the dead.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import unquote

import httpx
import pytest
import yaml

from .conftest import ADMIN_PASSWORD

PANEL_A = "https://a.example.org/sub/aaa"
PANEL_B = "https://b.example.org/sub/bbb"

A_URIS = "\n".join(
    [
        "vless://1111@nl1.example.net:443#NL-1",
        "vless://2222@ru1.example.net:443#RU-1",
    ]
)

B_URIS = "\n".join(
    [
        "trojan://secret@de1.example.net:443#DE-1",
        # The same server as A's first node: the dedupe switch has to see it.
        "vless://1111@nl1.example.net:443#NL-1",
    ]
)


def node_names(body: str) -> list[str]:
    """The label of every URI in a plain list, as a client would read it."""
    return [unquote(line.rsplit("#", 1)[1]) for line in body.strip().splitlines()]


def routed(bodies: dict[str, str], *, content_type: str = "text/plain"):
    """A mock upstream answering per URL, so two panels can differ."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for url, body in bodies.items():
            if str(request.url).startswith(url):
                return httpx.Response(200, text=body, headers={"content-type": content_type})
        return httpx.Response(404, text="unknown upstream")

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def make_profile(client, name: str, url: str, **overrides) -> dict:
    payload = {"name": name, "upstream_url": url, "protocols": []}
    payload.update(overrides)
    response = client.post("/api/profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def make_aggregate(client, **payload) -> dict:
    body = {"name": "Всё сразу", **payload}
    response = client.post("/api/aggregates", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def two_panels(make_client):
    """An admin session with a profile per panel, both serving URI lists."""
    client = make_client(routed({PANEL_A: A_URIS, PANEL_B: B_URIS}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "Панель A", PANEL_A)
    b = make_profile(client, "Панель B", PANEL_B)
    return client, a, b


# ------------------------------------------------------------------- merging


def test_one_link_serves_every_source(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}], dedupe=False
    )

    response = client.get(f"/s/{aggregate['token']}")
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 4


def test_names_are_prefixed_with_the_source(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client,
        sources=[{"profile_id": a["id"]}, {"profile_id": b["id"], "prefix": "B"}],
        dedupe=False,
    )

    names = node_names(client.get(f"/s/{aggregate['token']}").text)
    # An empty prefix falls back to the profile's own name.
    assert names == ["Панель A · NL-1", "Панель A · RU-1", "B · DE-1", "B · NL-1"]


def test_prefixes_can_be_switched_off_entirely(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client,
        sources=[{"profile_id": a["id"]}, {"profile_id": b["id"], "prefix": "B"}],
        prefix_names=False,
        dedupe=False,
    )

    names = node_names(client.get(f"/s/{aggregate['token']}").text)
    # Both panels offer an «NL-1»; the second one has to be told apart anyway.
    assert names == ["NL-1", "RU-1", "DE-1", "NL-1 (2)"]


def test_each_source_keeps_its_own_filter(two_panels):
    client, a, b = two_panels
    client.put(
        f"/api/profiles/{a['id']}",
        json={
            "name": "Панель A",
            "upstream_url": PANEL_A,
            "filter": {
                "mode": "builder",
                "match": "all",
                "conditions": [{"op": "not_contains", "value": "RU"}],
            },
        },
    )
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}], dedupe=False
    )

    body = client.get(f"/s/{aggregate['token']}").text
    assert "RU-1" not in body
    assert "NL-1" in body and "DE-1" in body


def test_duplicates_are_dropped_when_asked(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}], dedupe=True
    )

    lines = client.get(f"/s/{aggregate['token']}").text.strip().splitlines()
    assert len(lines) == 3
    assert sum(1 for line in lines if "nl1.example.net" in line) == 1


def test_a_disabled_source_is_left_out(two_panels):
    client, a, b = two_panels
    client.put(
        f"/api/profiles/{b['id']}",
        json={"name": "Панель B", "upstream_url": PANEL_B, "enabled": False},
    )
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}]
    )

    body = client.get(f"/s/{aggregate['token']}").text
    assert "DE-1" not in body
    assert "NL-1" in body


def test_the_output_envelope_can_be_forced(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}], output_format="base64"
    )

    body = client.get(f"/s/{aggregate['token']}").text
    assert base64.b64decode(body).decode().count("://") == 3


def test_a_query_override_still_wins(two_panels):
    client, a, _ = two_panels
    aggregate = make_aggregate(client, sources=[{"profile_id": a["id"]}])

    body = client.get(f"/s/{aggregate['token']}?format=base64").text
    assert "vless://" in base64.b64decode(body).decode()


# ------------------------------------------------------- structured formats

SINGBOX_A = json.dumps(
    {
        "log": {"level": "warn"},
        "outbounds": [
            {"type": "vless", "tag": "NL-1", "server": "nl1.example.net", "server_port": 443},
            {"type": "selector", "tag": "proxy", "outbounds": ["auto", "NL-1"], "default": "NL-1"},
            {"type": "urltest", "tag": "auto", "outbounds": ["NL-1"]},
            {"type": "direct", "tag": "direct"},
        ],
        "route": {"rules": [{"outbound": "NL-1", "domain_suffix": [".nl"]}], "final": "proxy"},
    }
)

SINGBOX_B = json.dumps(
    {
        "outbounds": [
            {"type": "trojan", "tag": "DE-1", "server": "de1.example.net", "server_port": 443},
            {"type": "selector", "tag": "proxy", "outbounds": ["DE-1"]},
        ]
    }
)


def test_singbox_groups_are_rewired_onto_the_merged_nodes(make_client):
    client = make_client(routed({PANEL_A: SINGBOX_A, PANEL_B: SINGBOX_B}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    b = make_profile(client, "B", PANEL_B)
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"]}]
    )

    doc = json.loads(client.get(f"/s/{aggregate['token']}").text)
    tags = [item["tag"] for item in doc["outbounds"]]
    assert "A · NL-1" in tags and "B · DE-1" in tags
    # The skeleton of the first source survives, groups and all.
    assert "direct" in tags
    groups = {item["tag"]: item for item in doc["outbounds"] if item["type"] == "selector"}
    assert groups["proxy"]["outbounds"] == ["auto", "A · NL-1", "B · DE-1"]
    # A default that pointed at a node under its old name has to move with it.
    assert groups["proxy"]["default"] == "A · NL-1"
    assert doc["route"]["final"] == "proxy"
    # A rule aimed at a node follows that node's new name instead of dying.
    assert doc["route"]["rules"] == [{"outbound": "A · NL-1", "domain_suffix": [".nl"]}]


CLASH_A = yaml.safe_dump(
    {
        "mixed-port": 7890,
        "proxies": [{"name": "NL-1", "type": "vless", "server": "nl1.example.net", "port": 443}],
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": ["AUTO", "NL-1"]},
            {"name": "AUTO", "type": "url-test", "proxies": ["NL-1"]},
        ],
        "rules": ["DOMAIN-SUFFIX,example.nl,NL-1", "MATCH,PROXY"],
    },
    sort_keys=False,
    allow_unicode=True,
)

CLASH_B = yaml.safe_dump(
    {
        "proxies": [{"name": "DE-1", "type": "trojan", "server": "de1.example.net", "port": 443}],
        "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["DE-1"]}],
    },
    sort_keys=False,
    allow_unicode=True,
)


def test_clash_groups_and_rules_survive_the_merge(make_client):
    client = make_client(routed({PANEL_A: CLASH_A, PANEL_B: CLASH_B}, content_type="text/yaml"))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    b = make_profile(client, "B", PANEL_B)
    aggregate = make_aggregate(
        client,
        sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"], "prefix": "B"}],
    )

    doc = yaml.safe_load(client.get(f"/s/{aggregate['token']}").text)
    assert [item["name"] for item in doc["proxies"]] == ["A · NL-1", "B · DE-1"]
    assert doc["mixed-port"] == 7890
    groups = {item["name"]: item for item in doc["proxy-groups"]}
    assert groups["PROXY"]["proxies"] == ["AUTO", "A · NL-1", "B · DE-1"]
    assert groups["AUTO"]["proxies"] == ["A · NL-1", "B · DE-1"]
    # A rule aimed at a renamed proxy follows the rename.
    assert doc["rules"][0] == "DOMAIN-SUFFIX,example.nl,A · NL-1"
    assert doc["rules"][1] == "MATCH,PROXY"


XRAY_A = json.dumps(
    {
        "outbounds": [
            {
                "protocol": "vless",
                "tag": "NL-1",
                "settings": {"vnext": [{"address": "nl1.example.net", "port": 443}]},
            },
            {"protocol": "freedom", "tag": "direct"},
        ],
        "routing": {"rules": [{"outboundTag": "NL-1", "domain": ["example.nl"]}]},
    }
)

XRAY_B = json.dumps(
    {
        "outbounds": [
            {
                "protocol": "trojan",
                "tag": "DE-1",
                "settings": {"servers": [{"address": "de1.example.net", "port": 443}]},
            }
        ]
    }
)


def test_xray_config_keeps_its_non_node_outbounds(make_client):
    client = make_client(routed({PANEL_A: XRAY_A, PANEL_B: XRAY_B}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    b = make_profile(client, "B", PANEL_B)
    aggregate = make_aggregate(
        client,
        sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"], "prefix": "B"}],
    )

    doc = json.loads(client.get(f"/s/{aggregate['token']}").text)
    tags = [item["tag"] for item in doc["outbounds"]]
    assert tags == ["A · NL-1", "B · DE-1", "direct"]
    assert doc["routing"]["rules"] == [{"outboundTag": "A · NL-1", "domain": ["example.nl"]}]


XRAY_LIST_A = json.dumps(
    [
        {
            "remarks": "NL-1",
            "outbounds": [
                {
                    "protocol": "vless",
                    "tag": "proxy",
                    "settings": {"vnext": [{"address": "nl1.example.net", "port": 443}]},
                }
            ],
        }
    ]
)

XRAY_LIST_B = json.dumps(
    [
        {
            "remarks": "DE-1",
            "outbounds": [
                {
                    "protocol": "trojan",
                    "tag": "proxy",
                    "settings": {"servers": [{"address": "de1.example.net", "port": 443}]},
                }
            ],
        }
    ]
)


def test_xray_config_list_relabels_each_entry(make_client):
    client = make_client(routed({PANEL_A: XRAY_LIST_A, PANEL_B: XRAY_LIST_B}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    b = make_profile(client, "B", PANEL_B)
    aggregate = make_aggregate(
        client,
        sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"], "prefix": "B"}],
    )

    doc = json.loads(client.get(f"/s/{aggregate['token']}").text)
    assert [item["remarks"] for item in doc] == ["A · NL-1", "B · DE-1"]


# ------------------------------------------------------------ things go wrong


def test_a_source_in_another_format_is_left_out_and_reported(make_client):
    client = make_client(routed({PANEL_A: A_URIS, PANEL_B: CLASH_B}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    b = make_profile(client, "Кламш", PANEL_B)
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}]
    )

    response = client.get(f"/s/{aggregate['token']}")
    assert response.status_code == 200
    assert "de1.example.net" not in response.text

    summary = client.get("/api/logs").json()["entries"][0]
    assert summary["profile_name"] == "Всё сразу"
    assert "Кламш" in summary["error"]


def test_a_dead_source_does_not_take_the_others_down(make_client):
    client = make_client(routed({PANEL_A: A_URIS}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    b = make_profile(client, "B", PANEL_B)
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}]
    )

    response = client.get(f"/s/{aggregate['token']}")
    assert response.status_code == 200
    assert response.text.count("://") == 2

    summary = client.get("/api/logs").json()["entries"][0]
    assert "«B»" in summary["error"]


def test_an_aggregate_with_nothing_alive_answers_502(make_client):
    client = make_client(routed({}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    a = make_profile(client, "A", PANEL_A)
    aggregate = make_aggregate(client, sources=[{"profile_id": a["id"]}])

    response = client.get(f"/s/{aggregate['token']}")
    assert response.status_code == 502
    assert "ни один источник" in response.text


def test_an_empty_aggregate_says_so(make_client):
    client = make_client(routed({}))
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    aggregate = make_aggregate(client, sources=[])

    response = client.get(f"/s/{aggregate['token']}")
    assert response.status_code == 502
    assert "нет включённых источников" in response.text


def test_a_disabled_aggregate_is_indistinguishable_from_a_missing_one(two_panels):
    client, a, _ = two_panels
    aggregate = make_aggregate(client, sources=[{"profile_id": a["id"]}], enabled=False)

    assert client.get(f"/s/{aggregate['token']}").status_code == 404
    assert client.get("/s/nothing-like-a-token").status_code == 404


# ------------------------------------------------------------------- logging


def test_every_source_is_logged_under_its_own_name(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}], dedupe=False
    )
    client.get(f"/s/{aggregate['token']}")

    entries = client.get("/api/logs").json()["entries"]
    assert [entry["profile_name"] for entry in entries] == ["Всё сразу", "Панель B", "Панель A"]
    summary = entries[0]
    assert summary["profile_id"] is None
    assert summary["nodes_total"] == 4
    assert summary["nodes_kept"] == 4
    assert summary["status_code"] == 200
    # The per-source rows are the ones that carry the node-by-node verdicts.
    per_source = client.get(f"/api/logs/{entries[1]['id']}/nodes").json()["nodes"]
    assert len(per_source) == 2


# ----------------------------------------------------------------------- API


def test_sources_come_back_resolved_to_names(two_panels):
    client, a, b = two_panels
    aggregate = make_aggregate(
        client, sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"]}]
    )
    assert aggregate["sources"][0] == {
        "profile_id": a["id"],
        "prefix": "A",
        "name": "Панель A",
        "enabled": True,
        "missing": False,
    }


def test_a_deleted_source_is_flagged_rather_than_hidden(two_panels):
    client, a, b = two_panels
    make_aggregate(client, sources=[{"profile_id": a["id"]}, {"profile_id": b["id"]}])
    client.delete(f"/api/profiles/{b['id']}")

    listed = client.get("/api/aggregates").json()[0]
    assert listed["sources"][1]["missing"] is True
    assert listed["sources"][1]["name"] is None


def test_an_unknown_source_is_refused(two_panels):
    client, a, _ = two_panels
    response = client.post(
        "/api/aggregates", json={"name": "x", "sources": [{"profile_id": 9999}]}
    )
    assert response.status_code == 400
    assert "не найден" in response.json()["detail"]


def test_the_same_profile_cannot_be_added_twice(two_panels):
    client, a, _ = two_panels
    response = client.post(
        "/api/aggregates",
        json={"name": "x", "sources": [{"profile_id": a["id"]}, {"profile_id": a["id"]}]},
    )
    assert response.status_code == 400
    assert "дважды" in response.json()["detail"]


def test_an_aggregate_without_a_name_is_refused(two_panels):
    client, _, _ = two_panels
    response = client.post("/api/aggregates", json={"name": "  ", "sources": []})
    assert response.status_code == 400


def test_rotating_the_token_breaks_the_old_link(two_panels):
    client, a, _ = two_panels
    aggregate = make_aggregate(client, sources=[{"profile_id": a["id"]}])
    old = aggregate["token"]

    rotated = client.post(f"/api/aggregates/{aggregate['id']}/rotate-token").json()
    assert rotated["token"] != old
    assert client.get(f"/s/{old}").status_code == 404
    assert client.get(f"/s/{rotated['token']}").status_code == 200


def test_delete_is_undoable(two_panels):
    client, a, _ = two_panels
    aggregate = make_aggregate(client, sources=[{"profile_id": a["id"]}])

    assert client.delete(f"/api/aggregates/{aggregate['id']}").status_code == 200
    assert client.get(f"/s/{aggregate['token']}").status_code == 404
    assert client.get("/api/aggregates").json() == []

    assert client.post(f"/api/aggregates/{aggregate['id']}/restore").status_code == 200
    assert client.get(f"/s/{aggregate['token']}").status_code == 200


def test_the_qr_code_encodes_the_public_link(two_panels):
    client, a, _ = two_panels
    aggregate = make_aggregate(client, sources=[{"profile_id": a["id"]}])
    response = client.get(f"/api/aggregates/{aggregate['id']}/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


def test_the_stats_count_aggregates(two_panels):
    client, a, _ = two_panels
    make_aggregate(client, sources=[{"profile_id": a["id"]}])
    assert client.get("/api/stats").json()["aggregates_total"] == 1


# -------------------------------------------------------------- export/import


def test_a_bundle_carries_aggregates_by_profile_name(two_panels):
    client, a, b = two_panels
    make_aggregate(
        client, sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"]}]
    )

    document = yaml.safe_load(client.get("/api/export").text)
    assert document["aggregates"][0]["sources"] == [
        {"profile": "Панель A", "prefix": "A"},
        {"profile": "Панель B", "prefix": ""},
    ]


def test_importing_a_bundle_points_sources_at_the_imported_copies(two_panels):
    """Import renames what collides; a source must follow the copy, not the original."""
    client, a, b = two_panels
    make_aggregate(
        client, sources=[{"profile_id": a["id"], "prefix": "A"}, {"profile_id": b["id"]}]
    )
    document = client.get("/api/export").text

    result = client.post("/api/import", json={"content": document, "keep_tokens": True})
    assert result.status_code == 200, result.text
    assert result.json()["aggregates_created"] == 1
    assert result.json()["errors"] == []

    restored = client.get("/api/aggregates").json()[0]
    assert [source["name"] for source in restored["sources"]] == [
        "Панель A (импорт)",
        "Панель B (импорт)",
    ]
    assert {source["profile_id"] for source in restored["sources"]} & {a["id"], b["id"]} == set()
    assert client.get(f"/s/{restored['token']}").status_code == 200


def test_the_config_editor_can_add_an_aggregate(two_panels):
    client, a, b = two_panels
    document = yaml.safe_load(client.get("/api/config").json()["content"])
    document["aggregates"] = [
        {
            "name": "Из редактора",
            "enabled": True,
            "prefix_names": True,
            "dedupe": True,
            "output_format": "auto",
            "sources": [{"profile": "Панель A", "prefix": ""}],
        }
    ]
    content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    plan = client.post("/api/config/validate", json={"content": content}).json()
    assert plan["ok"] is True, plan
    assert plan["summary"]["aggregates_created"] == 1

    applied = client.put("/api/config", json={"content": content})
    assert applied.status_code == 200, applied.text
    created = client.get("/api/aggregates").json()[0]
    assert created["sources"][0]["name"] == "Панель A"


def test_the_config_editor_refuses_a_source_that_is_not_in_the_document(two_panels):
    client, _, _ = two_panels
    document = yaml.safe_load(client.get("/api/config").json()["content"])
    document["aggregates"] = [{"name": "x", "sources": [{"profile": "Которого нет"}]}]
    content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    plan = client.post("/api/config/validate", json={"content": content}).json()
    assert plan["ok"] is False
    assert "Которого нет" in plan["errors"][0]


def test_the_config_editor_removes_an_aggregate_left_out_of_the_document(two_panels):
    client, a, _ = two_panels
    make_aggregate(client, sources=[{"profile_id": a["id"]}])

    document = yaml.safe_load(client.get("/api/config").json()["content"])
    document["aggregates"] = []
    content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    plan = client.post("/api/config/validate", json={"content": content}).json()
    assert plan["summary"]["aggregates_removed"] == ["Всё сразу"]
    assert client.put("/api/config", json={"content": content}).status_code == 200
    assert client.get("/api/aggregates").json() == []
