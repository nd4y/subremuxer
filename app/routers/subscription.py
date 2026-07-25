"""The public endpoint clients point their subscription URL at."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..logs import LogRepository
from ..pipeline import Defaults, proxy_subscription
from ..profiles import OUTPUT_FORMATS, ProfileRepository
from ..security import client_ip

router = APIRouter()


async def _serve(request: Request, token: str) -> Response:
    app = request.app
    profiles = ProfileRepository(app.state.db)
    logs = LogRepository(app.state.db)
    settings = app.state.settings

    profile = profiles.get_by_token(token)
    if profile is None or not profile.enabled:
        # Same answer for "no such token" and "disabled", so the endpoint cannot be
        # used to enumerate which tokens exist.
        return Response(status_code=404, content="Not Found", media_type="text/plain")

    # A per-request format override is handy when debugging a client by hand.
    override = request.query_params.get("format")
    if override in OUTPUT_FORMATS and override != "auto":
        profile.output_format = override

    defaults = Defaults.from_settings(app.state.db.all_settings())
    result = await proxy_subscription(
        profile=profile,
        defaults=defaults,
        fetcher=app.state.fetcher,
        cache=app.state.cache,
        client_headers=dict(request.headers),
        client_ip=client_ip(request, settings),
        request_path=str(request.url.path),
    )

    logs.record(result.entry, result.decisions)

    headers = dict(result.headers)
    headers.setdefault("cache-control", "no-store")
    return Response(
        content=result.body,
        status_code=result.status_code,
        media_type=result.content_type,
        headers=headers,
    )


@router.get("/s/{token}")
async def serve_subscription(request: Request, token: str) -> Response:
    return await _serve(request, token)


@router.head("/s/{token}")
async def head_subscription(request: Request, token: str) -> Response:
    response = await _serve(request, token)
    return Response(
        status_code=response.status_code,
        media_type=response.media_type,
        headers=dict(response.headers),
    )


@router.get("/s/{token}/{suffix:path}")
async def serve_subscription_with_suffix(request: Request, token: str, suffix: str) -> Response:
    """Some clients append a filename to the URL; the suffix is ignored."""
    del suffix
    return await _serve(request, token)
