"""The public capture endpoint — a fake subscription that records who asked."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Request, Response

from .. import APP_NAME
from ..probe import Capture, ProbeRepository, get_probe_token, html_page, subscription_body
from ..security import client_ip

router = APIRouter()

# Clients show this as the profile name. Non-ASCII header values have to be
# base64-wrapped, the same convention the panels use.
_PROFILE_TITLE = "base64:" + base64.b64encode(f"{APP_NAME} · захват".encode()).decode("ascii")


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


async def _capture(request: Request, token: str) -> Response:
    db = request.app.state.db
    if token != get_probe_token(db):
        return Response(status_code=404, content="Not Found", media_type="text/plain")

    capture = Capture.from_headers(dict(request.headers))
    ProbeRepository(db).record(capture, client_ip(request, request.app.state.settings))

    headers = {"cache-control": "no-store", "profile-update-interval": "1"}
    if _wants_html(request):
        return Response(
            content=html_page(capture), media_type="text/html; charset=utf-8", headers=headers
        )

    headers["profile-title"] = _PROFILE_TITLE
    return Response(
        content=subscription_body(capture),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


@router.get("/probe/{token}")
async def probe(request: Request, token: str) -> Response:
    return await _capture(request, token)


@router.get("/probe/{token}/{suffix:path}")
async def probe_with_suffix(request: Request, token: str, suffix: str) -> Response:
    del suffix
    return await _capture(request, token)
