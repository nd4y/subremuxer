"""Sample subscription bodies, one per format the proxy understands."""

from __future__ import annotations

import base64
import json

import yaml

URI_LIST = "\n".join(
    [
        "vless://11111111-1111-1111-1111-111111111111@nl1.example.net:443?type=tcp&security=reality#NL-1%20LTE",
        "vless://22222222-2222-2222-2222-222222222222@ru1.example.net:443?type=tcp&security=reality#RU-1%20LTE",
        "trojan://secret@de1.example.net:443#DE-1",
        "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@fi1.example.net:8388#FI-1%20LTE",
        "hysteria2://pass@se1.example.net:443#SE-1",
    ]
)

BASE64_LIST = base64.b64encode(URI_LIST.encode()).decode()


SINGBOX = json.dumps(
    {
        "log": {"level": "warn"},
        "outbounds": [
            {"type": "vless", "tag": "NL-1 LTE", "server": "nl1.example.net", "server_port": 443},
            {"type": "vless", "tag": "RU-1 LTE", "server": "ru1.example.net", "server_port": 443},
            {"type": "trojan", "tag": "DE-1", "server": "de1.example.net", "server_port": 443},
            {
                "type": "selector",
                "tag": "proxy",
                "outbounds": ["auto", "NL-1 LTE", "RU-1 LTE", "DE-1"],
                "default": "auto",
            },
            {"type": "urltest", "tag": "auto", "outbounds": ["NL-1 LTE", "RU-1 LTE", "DE-1"]},
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "rules": [{"outbound": "direct", "domain_suffix": [".lan"]}],
            "final": "proxy",
        },
    },
    ensure_ascii=False,
)


XRAY_SINGLE = json.dumps(
    {
        "log": {"loglevel": "warning"},
        "outbounds": [
            {"protocol": "vless", "tag": "NL-1 LTE", "settings": {"vnext": [{"address": "nl1.example.net", "port": 443}]}},
            {"protocol": "vless", "tag": "RU-1 LTE", "settings": {"vnext": [{"address": "ru1.example.net", "port": 443}]}},
            {"protocol": "trojan", "tag": "DE-1", "settings": {"servers": [{"address": "de1.example.net", "port": 443}]}},
            {"protocol": "freedom", "tag": "direct"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "outboundTag": "RU-1 LTE", "domain": ["example.ru"]},
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:private"]},
            ]
        },
    },
    ensure_ascii=False,
)


XRAY_CONFIG_LIST = json.dumps(
    [
        {
            "remarks": "NL-1 LTE",
            "outbounds": [
                {"protocol": "vless", "tag": "proxy", "settings": {"vnext": [{"address": "nl1.example.net", "port": 443}]}},
                {"protocol": "freedom", "tag": "direct"},
            ],
        },
        {
            "remarks": "RU-1 LTE",
            "outbounds": [
                {"protocol": "vless", "tag": "proxy", "settings": {"vnext": [{"address": "ru1.example.net", "port": 443}]}},
                {"protocol": "freedom", "tag": "direct"},
            ],
        },
        {
            "remarks": "DE-1",
            "outbounds": [
                {"protocol": "trojan", "tag": "proxy", "settings": {"servers": [{"address": "de1.example.net", "port": 443}]}},
                {"protocol": "freedom", "tag": "direct"},
            ],
        },
    ],
    ensure_ascii=False,
)


CLASH = yaml.safe_dump(
    {
        "mixed-port": 7890,
        "proxies": [
            {"name": "NL-1 LTE", "type": "vless", "server": "nl1.example.net", "port": 443},
            {"name": "RU-1 LTE", "type": "vless", "server": "ru1.example.net", "port": 443},
            {"name": "DE-1", "type": "trojan", "server": "de1.example.net", "port": 443},
        ],
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": ["AUTO", "NL-1 LTE", "RU-1 LTE", "DE-1"]},
            {"name": "AUTO", "type": "url-test", "proxies": ["NL-1 LTE", "RU-1 LTE", "DE-1"]},
            {"name": "RU-ONLY", "type": "select", "proxies": ["RU-1 LTE"]},
        ],
        "rules": [
            "DOMAIN-SUFFIX,example.ru,RU-ONLY",
            "GEOIP,CN,DIRECT",
            "MATCH,PROXY",
        ],
    },
    sort_keys=False,
    allow_unicode=True,
)


BROWSER_HTML = "<!doctype html><html><body><h1>Subscription</h1></body></html>"

ALL_FORMATS = {
    "uri_list": URI_LIST,
    "base64": BASE64_LIST,
    "singbox": SINGBOX,
    "xray_single": XRAY_SINGLE,
    "xray_list": XRAY_CONFIG_LIST,
    "clash": CLASH,
}
