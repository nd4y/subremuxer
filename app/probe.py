"""The capture endpoint.

Point any client at this URL as if it were a subscription. The client sends the
same headers it would send to a real panel — `x-hwid` above all — and we record
them, so the HWID of a device can be copied straight into a profile instead of
being hunted for in the client's settings.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .db import Database

PROBE_TOKEN_SETTING = "probe_token"

#: Only the headers that say something about the client. Nothing else is stored.
INTERESTING_HEADERS = (
    "user-agent",
    "x-hwid",
    "x-device-os",
    "x-ver-os",
    "x-device-model",
    "accept",
    "accept-language",
    "x-client-version",
    "x-app-version",
)


def get_probe_token(db: Database) -> str:
    token = db.get_setting(PROBE_TOKEN_SETTING)
    if isinstance(token, str) and token:
        return token
    return rotate_probe_token(db)


def rotate_probe_token(db: Database) -> str:
    token = secrets.token_urlsafe(12)
    db.set_setting(PROBE_TOKEN_SETTING, token)
    return token


@dataclass(slots=True)
class Capture:
    user_agent: str = ""
    hwid: str = ""
    device_os: str = ""
    device_ver: str = ""
    device_model: str = ""
    headers: dict[str, str] | None = None

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> Capture:
        lowered = {key.lower(): value for key, value in headers.items()}
        return cls(
            user_agent=lowered.get("user-agent", "").strip(),
            hwid=lowered.get("x-hwid", "").strip(),
            device_os=lowered.get("x-device-os", "").strip(),
            device_ver=lowered.get("x-ver-os", "").strip(),
            device_model=lowered.get("x-device-model", "").strip(),
            headers={
                name: lowered[name] for name in INTERESTING_HEADERS if lowered.get(name)
            },
        )

    def is_empty(self) -> bool:
        return not any(
            (self.user_agent, self.hwid, self.device_os, self.device_ver, self.device_model)
        )

    def describe_device(self) -> str:
        parts = [part for part in (self.device_os, self.device_ver, self.device_model) if part]
        return " ".join(parts)


class ProbeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, capture: Capture, client_ip: str | None) -> int:
        """Upsert by device identity — a polling client must not flood the list."""
        now = int(time.time())
        key = (
            capture.user_agent,
            capture.hwid,
            capture.device_os,
            capture.device_ver,
            capture.device_model,
        )
        existing = self.db.query_one(
            "SELECT id FROM probe_captures WHERE user_agent = ? AND hwid = ? "
            "AND device_os = ? AND device_ver = ? AND device_model = ?",
            key,
        )
        if existing is not None:
            self.db.execute(
                "UPDATE probe_captures SET last_ts = ?, seen_count = seen_count + 1, "
                "client_ip = ?, headers_json = ? WHERE id = ?",
                (
                    now,
                    client_ip,
                    json.dumps(capture.headers or {}, ensure_ascii=False),
                    existing["id"],
                ),
            )
            return int(existing["id"])

        cursor = self.db.execute(
            "INSERT INTO probe_captures(first_ts, last_ts, seen_count, client_ip, user_agent, "
            "hwid, device_os, device_ver, device_model, headers_json) "
            "VALUES(?,?,1,?,?,?,?,?,?,?)",
            (now, now, client_ip, *key, json.dumps(capture.headers or {}, ensure_ascii=False)),
        )
        return int(cursor.lastrowid or 0)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM probe_captures ORDER BY last_ts DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["headers"] = json.loads(item.pop("headers_json") or "{}")
            except json.JSONDecodeError:
                item["headers"] = {}
            result.append(item)
        return result

    def get(self, capture_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM probe_captures WHERE id = ?", (capture_id,))
        return dict(row) if row else None

    def clear(self, capture_id: int | None = None) -> int:
        if capture_id is None:
            cursor = self.db.execute("DELETE FROM probe_captures")
        else:
            cursor = self.db.execute("DELETE FROM probe_captures WHERE id = ?", (capture_id,))
        return cursor.rowcount


# --------------------------------------------------------------------- output


def _node_uri(label: str) -> str:
    """A syntactically valid node whose only job is to carry a label into the client."""
    from urllib.parse import quote

    return (
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
        "?type=tcp&security=tls&sni=probe.invalid&encryption=none#" + quote(label)
    )


def subscription_body(capture: Capture) -> str:
    """What the client gets back: its own HWID, shown as a server name."""
    if capture.hwid:
        lines = [_node_uri(f"✅ HWID: {capture.hwid}")]
    else:
        lines = [_node_uri("⚠️ Клиент не прислал HWID")]

    device = capture.describe_device()
    if device:
        lines.append(_node_uri(f"📱 {device}"))
    if capture.user_agent:
        lines.append(_node_uri(f"🧩 {capture.user_agent}"))
    lines.append(_node_uri("↩️ Откройте subremuxer — данные захвачены"))
    return base64.b64encode("\n".join(lines).encode("utf-8")).decode("ascii")


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def html_page(capture: Capture) -> str:
    """Shown when the URL is opened in a browser instead of a client."""
    rows = [
        ("HWID", capture.hwid or "— не прислан —"),
        ("ОС устройства", capture.device_os or "—"),
        ("Версия ОС", capture.device_ver or "—"),
        ("Модель", capture.device_model or "—"),
        ("User-Agent", capture.user_agent or "—"),
    ]
    cells = "".join(
        f"<tr><th>{_escape(name)}</th><td>{_escape(value)}</td></tr>" for name, value in rows
    )
    warning = (
        ""
        if capture.hwid
        else "<p class='warn'>Этот клиент не прислал заголовок <code>x-hwid</code> — "
        "именно для таких случаев subremuxer и подставляет HWID сам.</p>"
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>subremuxer — захват клиента</title>
<style>
  :root {{ color-scheme: light dark; --bg:#fef7ff; --fg:#1d1b20; --card:#f7f2fa;
           --muted:#49454f; --accent:#6750a4; --warn:#7d5260; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141218; --fg:#e6e0e9; --card:#1d1b20; --muted:#cac4d0;
             --accent:#d0bcff; --warn:#efb8c8; }}
  }}
  body {{ margin:0; min-height:100dvh; display:grid; place-items:center; padding:24px;
          font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
          background:var(--bg); color:var(--fg); }}
  .card {{ width:min(560px,100%); background:var(--card); border-radius:28px; padding:28px; }}
  h1 {{ margin:0 0 4px; font-size:1.4rem; letter-spacing:-0.02em; }}
  p {{ margin:0 0 20px; color:var(--muted); font-size:.9rem; line-height:1.5; }}
  p.warn {{ color:var(--warn); }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; padding:12px 0; color:var(--muted); font-size:.8rem;
        font-weight:600; width:38%; vertical-align:top; }}
  td {{ padding:12px 0; font-family:ui-monospace,Consolas,monospace; font-size:.82rem;
        word-break:break-all;
        border-bottom:1px solid color-mix(in srgb,var(--muted) 25%,transparent); }}
  code {{ font-size:.85em; }}
</style></head>
<body><main class="card">
<h1>Данные захвачены</h1>
<p>Вернитесь в subremuxer — этот клиент появился в разделе «Захват», оттуда его
можно подставить в профиль.</p>
{warning}
<table>{cells}</table>
</main></body></html>
"""
