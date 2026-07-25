"""Admin REST API. Everything except /auth/login requires a session cookie."""

from __future__ import annotations

import io
from typing import Any

import segno
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ..filtering import (
    CONDITION_OPS,
    PRESETS,
    CompiledFilter,
    FilterConfig,
    FilterError,
    build_regex,
)
from ..formats import FORMAT_LABELS, KNOWN_PROTOCOLS
from ..logs import LogRepository
from ..pipeline import Defaults, preview_filter
from ..profiles import (
    OUTPUT_FORMATS,
    USER_AGENT_PRESETS,
    Profile,
    ProfileError,
    ProfileRepository,
)
from ..security import (
    SESSION_COOKIE,
    check_password,
    client_ip,
    issue_session,
    require_admin,
)
from ..upstream import HWID_MODES, hwid_is_valid

router = APIRouter(prefix="/api")
guarded = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


def _repo(request: Request) -> ProfileRepository:
    return ProfileRepository(request.app.state.db)


def _logs(request: Request) -> LogRepository:
    return LogRepository(request.app.state.db)


# ----------------------------------------------------------------------- auth


@router.post("/auth/login")
async def login(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    settings = request.app.state.settings
    throttle = request.app.state.throttle
    ip = client_ip(request, settings) or "unknown"

    if throttle.blocked(ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток, подождите пять минут")

    password = str(payload.get("password", ""))
    if not check_password(settings, password):
        throttle.record_failure(ip)
        raise HTTPException(status_code=401, detail="Неверный пароль")

    throttle.reset(ip)
    token = issue_session(request.app.state.db, settings, ip, request.headers.get("user-agent"))
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        request.app.state.db.delete_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/auth/me")
async def me(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    authenticated = bool(token and request.app.state.db.session_valid(token))
    return {"authenticated": authenticated}


# ----------------------------------------------------------------------- meta


@guarded.get("/meta")
async def meta() -> dict[str, Any]:
    return {
        "protocols": list(KNOWN_PROTOCOLS),
        "condition_ops": CONDITION_OPS,
        "presets": PRESETS,
        "user_agent_presets": USER_AGENT_PRESETS,
        "hwid_modes": list(HWID_MODES),
        "output_formats": list(OUTPUT_FORMATS),
        "format_labels": {key.value: value for key, value in FORMAT_LABELS.items()},
    }


@guarded.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    stored = request.app.state.db.all_settings()
    defaults = Defaults.from_settings(stored)
    data = defaults.as_dict()
    data["hwid_valid"] = hwid_is_valid(defaults.hwid) if defaults.hwid else None
    return data


@guarded.put("/settings")
async def put_settings(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db = request.app.state.db
    for key in ("default_hwid", "default_device_os", "default_device_ver", "default_device_model"):
        if key in payload:
            db.set_setting(key, str(payload[key] or "").strip())
    request.app.state.cache = type(request.app.state.cache)()
    return await get_settings(request)


# ------------------------------------------------------------------- profiles


@guarded.get("/profiles")
async def list_profiles(request: Request) -> list[dict[str, Any]]:
    base = _public_base(request)
    return [
        {**profile.as_dict(), "subscription_url": f"{base}/s/{profile.token}"}
        for profile in _repo(request).list()
    ]


@guarded.post("/profiles", status_code=201)
async def create_profile(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        profile = _repo(request).create(payload)
    except (ProfileError, FilterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**profile.as_dict(), "subscription_url": f"{_public_base(request)}/s/{profile.token}"}


@guarded.get("/profiles/{profile_id}")
async def get_profile(request: Request, profile_id: int) -> dict[str, Any]:
    profile = _repo(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return {**profile.as_dict(), "subscription_url": f"{_public_base(request)}/s/{profile.token}"}


@guarded.put("/profiles/{profile_id}")
async def update_profile(
    request: Request, profile_id: int, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        profile = _repo(request).update(profile_id, payload)
    except (ProfileError, FilterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.cache.invalidate_profile(profile_id)
    return {**profile.as_dict(), "subscription_url": f"{_public_base(request)}/s/{profile.token}"}


@guarded.delete("/profiles/{profile_id}")
async def delete_profile(request: Request, profile_id: int) -> dict[str, bool]:
    if not _repo(request).delete(profile_id):
        raise HTTPException(status_code=404, detail="Профиль не найден")
    request.app.state.cache.invalidate_profile(profile_id)
    return {"ok": True}


@guarded.post("/profiles/{profile_id}/rotate-token")
async def rotate_token(request: Request, profile_id: int) -> dict[str, Any]:
    try:
        profile = _repo(request).rotate_token(profile_id)
    except ProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    request.app.state.cache.invalidate_profile(profile_id)
    return {**profile.as_dict(), "subscription_url": f"{_public_base(request)}/s/{profile.token}"}


@guarded.get("/profiles/{profile_id}/qr.svg")
async def profile_qr(request: Request, profile_id: int) -> Response:
    profile = _repo(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    url = f"{_public_base(request)}/s/{profile.token}"
    return Response(content=qr_svg(url), media_type="image/svg+xml")


def qr_svg(data: str) -> str:
    """A QR always renders dark-on-light — scanners need that contrast."""
    qr = segno.make(data, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=8, border=2, dark="#000000", light="#ffffff")
    return buffer.getvalue().decode("utf-8")


# ------------------------------------------------------------- filter testing


@guarded.post("/filter/regex")
async def compile_regex(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Compile builder conditions into the regexp that will actually run."""
    try:
        config = FilterConfig.from_dict(payload.get("filter"))
        CompiledFilter.build(config, payload.get("protocols") or [])
    except (FilterError, ProfileError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"regex": build_regex(config)}


@guarded.post("/filter/dry-run")
async def dry_run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Apply a filter to a list of names typed by hand — no upstream call."""
    try:
        config = FilterConfig.from_dict(payload.get("filter"))
        compiled = CompiledFilter.build(config, payload.get("protocols") or [])
    except FilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    names = payload.get("names") or []
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names должен быть списком строк")

    results = []
    for name in names[:500]:
        kept, reason, detail = compiled.check_name(str(name))
        results.append({"name": str(name), "kept": kept, "reason": reason, "detail": detail})
    return {"regex": compiled.builder_regex, "results": results}


@guarded.post("/filter/test")
async def test_filter(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Fetch the real upstream and show which servers survive the current filter."""
    profile_id = payload.get("profile_id")
    repo = _repo(request)

    if profile_id:
        stored = repo.get(int(profile_id))
        if stored is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        upstream_url = str(payload.get("upstream_url") or stored.upstream_url)
        hwid_mode = str(payload.get("hwid_mode") or stored.hwid_mode)
        hwid = payload.get("hwid", stored.hwid)
        upstream_ua = payload.get("upstream_ua", stored.upstream_ua)
        device_os = payload.get("device_os", stored.device_os)
        device_ver = payload.get("device_ver", stored.device_ver)
        device_model = payload.get("device_model", stored.device_model)
    else:
        upstream_url = str(payload.get("upstream_url") or "")
        hwid_mode = str(payload.get("hwid_mode") or "override")
        hwid = payload.get("hwid")
        upstream_ua = payload.get("upstream_ua")
        device_os = payload.get("device_os")
        device_ver = payload.get("device_ver")
        device_model = payload.get("device_model")

    if not upstream_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Укажите корректную ссылку на подписку")
    if hwid_mode not in HWID_MODES:
        raise HTTPException(status_code=400, detail=f"неизвестный режим HWID: {hwid_mode}")

    try:
        config = FilterConfig.from_dict(payload.get("filter"))
        compiled = CompiledFilter.build(config, payload.get("protocols") or [])
    except FilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    transient = Profile(
        id=0,
        name="test",
        token="test",
        upstream_url=upstream_url,
        hwid_mode=hwid_mode,
        hwid=(str(hwid).strip() or None) if hwid else None,
        device_os=device_os or None,
        device_ver=device_ver or None,
        device_model=device_model or None,
        filter_config=config,
        protocols=payload.get("protocols") or [],
        upstream_ua=(str(upstream_ua).strip() or None) if upstream_ua else None,
    )

    defaults = Defaults.from_settings(request.app.state.db.all_settings())
    result = await preview_filter(
        profile=transient,
        defaults=defaults,
        fetcher=request.app.state.fetcher,
        compiled=compiled,
    )
    return {
        "detected_format": result.detected_format,
        "format_label": result.format_label,
        "upstream_status": result.upstream_status,
        "upstream_ms": result.upstream_ms,
        "hwid_sent": result.hwid_sent,
        "hwid_action": result.hwid_action,
        "regex": result.regex,
        "nodes": result.nodes,
        "total": result.total,
        "kept": result.kept,
        "error": result.error,
        "body_preview": result.body_preview,
    }


# ----------------------------------------------------------------------- logs


@guarded.get("/logs")
async def list_logs(
    request: Request,
    profile_id: int | None = None,
    limit: int = 50,
    before_id: int | None = None,
    only_errors: bool = False,
) -> dict[str, Any]:
    entries = _logs(request).list(
        profile_id=profile_id, limit=limit, before_id=before_id, only_errors=only_errors
    )
    return {"entries": entries, "next_before_id": entries[-1]["id"] if entries else None}


@guarded.get("/logs/{log_id}/nodes")
async def log_nodes(request: Request, log_id: int) -> dict[str, Any]:
    return {"nodes": _logs(request).nodes(log_id)}


@guarded.delete("/logs")
async def clear_logs(request: Request, profile_id: int | None = None) -> dict[str, Any]:
    return {"deleted": _logs(request).clear(profile_id)}


@guarded.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    repo = _repo(request)
    profiles = repo.list()
    data = _logs(request).stats()
    data["profiles_total"] = len(profiles)
    data["profiles_enabled"] = sum(1 for profile in profiles if profile.enabled)
    return data


# ---------------------------------------------------------------------- utils


def _public_base(request: Request) -> str:
    configured = request.app.state.settings.public_base_url
    if configured:
        return configured
    return str(request.base_url).rstrip("/")
