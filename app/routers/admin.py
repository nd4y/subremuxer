"""Admin REST API. Everything except /auth/login requires a session cookie.

Three routers come out of this module. `router` is open, and holds only the
sign-in endpoints. `shared` is what a viewer may read — profiles, redacted down
to the links they are meant to hand out. `guarded` is everything else, and is
administrator-only by default: a new endpoint added there is closed to viewers
unless somebody deliberately moves it.
"""

from __future__ import annotations

import io
import logging
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
from ..portability import (
    ConfigApplier,
    Importer,
    PortabilityError,
    dump,
    export_bundle,
    export_profile,
)
from ..portability import parse as parse_document
from ..probe import ProbeRepository, get_probe_token, rotate_probe_token
from ..profiles import (
    CLIENT_PRESETS,
    DEFAULT_CLIENT_PRESET,
    DEFAULT_DEVICE_PRESET,
    DEVICE_PRESETS,
    OUTPUT_FORMATS,
    Profile,
    ProfileError,
    ProfileRepository,
    default_profile_fields,
)
from ..security import (
    SESSION_COOKIE,
    Identity,
    check_password,
    client_ip,
    current_identity,
    issue_session,
    require_admin,
    require_session,
)
from ..templates import TemplateError, TemplateRepository, apply_template
from ..upstream import HWID_MODES, hwid_is_valid

logger = logging.getLogger("subremuxer.auth")

router = APIRouter(prefix="/api")
shared = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
guarded = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


def _repo(request: Request) -> ProfileRepository:
    return ProfileRepository(request.app.state.db)


def _logs(request: Request) -> LogRepository:
    return LogRepository(request.app.state.db)


def _templates(request: Request) -> TemplateRepository:
    return TemplateRepository(request.app.state.db)


def _probes(request: Request) -> ProbeRepository:
    return ProbeRepository(request.app.state.db)


# ----------------------------------------------------------------------- auth


@router.post("/auth/login")
async def login(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    settings = request.app.state.settings
    throttle = request.app.state.throttle
    ip = client_ip(request, settings) or "unknown"

    if not settings.password_login_enabled:
        # Switched off means switched off, not merely hidden: the endpoint is a
        # matter of public record in a public repository.
        raise HTTPException(status_code=404, detail="Вход по паролю отключён")

    if throttle.blocked(ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток, подождите пять минут")

    password = str(payload.get("password", ""))
    if not check_password(settings, password):
        throttle.record_failure(ip)
        raise HTTPException(status_code=401, detail="Неверный пароль")

    throttle.reset(ip)
    token = issue_session(request.app.state.db, settings, ip, request.headers.get("user-agent"))
    if settings.oidc_enabled:
        # With an identity provider configured, the master password is the way
        # in when that provider is unavailable. Its use should be visible in the
        # log rather than indistinguishable from an ordinary sign-in.
        logger.warning("вход по мастер-паролю с адреса %s в обход OIDC", ip)
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
    """Who is signed in, and which sign-in methods this instance offers.

    The interface reads its whole login screen from here, so that what it shows
    and what the server accepts cannot drift apart.
    """
    settings = request.app.state.settings
    identity = current_identity(request)
    return {
        "authenticated": identity is not None,
        "demo": settings.demo_mode,
        "role": identity.role if identity else None,
        "method": identity.method if identity else None,
        "user": (identity.display_name or None) if identity else None,
        "methods": {
            "password": settings.password_login_enabled,
            "oidc": settings.oidc_enabled,
        },
        "oidc_name": settings.oidc_display_name,
        "auto_login": settings.oidc_auto_login,
    }


# ----------------------------------------------------------------------- meta


@guarded.get("/meta")
async def meta() -> dict[str, Any]:
    return {
        "protocols": list(KNOWN_PROTOCOLS),
        "condition_ops": CONDITION_OPS,
        "presets": PRESETS,
        "client_presets": CLIENT_PRESETS,
        "device_presets": DEVICE_PRESETS,
        "default_client_preset": DEFAULT_CLIENT_PRESET,
        "default_device_preset": DEFAULT_DEVICE_PRESET,
        "default_profile": default_profile_fields(),
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


#: What a viewer is allowed to learn about a profile. Everything else — the
#: upstream URL above all, but also the HWID, the filter and the mimicry — is
#: removed on the server, so it never reaches the browser to be un-hidden.
VIEWER_PROFILE_FIELDS = ("id", "name", "enabled", "subscription_url", "updated_at")


def _profile_view(profile: Profile, request: Request, identity: Identity) -> dict[str, Any]:
    data = {
        **profile.as_dict(),
        "subscription_url": f"{_public_base(request)}/s/{profile.token}",
    }
    if identity.is_admin:
        return data
    return {key: data[key] for key in VIEWER_PROFILE_FIELDS if key in data}


@shared.get("/profiles")
async def list_profiles(
    request: Request, identity: Identity = Depends(require_session)
) -> list[dict[str, Any]]:
    return [_profile_view(profile, request, identity) for profile in _repo(request).list()]


@shared.get("/profiles/{profile_id}")
async def get_profile(
    request: Request, profile_id: int, identity: Identity = Depends(require_session)
) -> dict[str, Any]:
    profile = _repo(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return _profile_view(profile, request, identity)


@shared.get("/profiles/{profile_id}/qr.svg")
async def profile_qr(request: Request, profile_id: int) -> Response:
    profile = _repo(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    url = f"{_public_base(request)}/s/{profile.token}"
    return Response(content=qr_svg(url), media_type="image/svg+xml")


@guarded.post("/profiles", status_code=201)
async def create_profile(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    template_id = payload.pop("template_id", None)
    try:
        if template_id:
            template = _templates(request).get(int(template_id))
            if template is None:
                raise HTTPException(status_code=404, detail="Шаблон не найден")
            payload = apply_template(template, payload)
        profile = _repo(request).create(payload)
    except (ProfileError, FilterError, TemplateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**profile.as_dict(), "subscription_url": f"{_public_base(request)}/s/{profile.token}"}


@guarded.post("/profiles/{profile_id}/clone", status_code=201)
async def clone_profile(request: Request, profile_id: int) -> dict[str, Any]:
    try:
        profile = _repo(request).clone(profile_id)
    except (ProfileError, FilterError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**profile.as_dict(), "subscription_url": f"{_public_base(request)}/s/{profile.token}"}


@guarded.post("/profiles/{profile_id}/restore")
async def restore_profile(request: Request, profile_id: int) -> dict[str, Any]:
    """Undo a delete. The row is still there until the maintenance pass purges it."""
    try:
        profile = _repo(request).restore(profile_id)
    except ProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    request.app.state.cache.invalidate_profile(profile_id)
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


def qr_svg(data: str) -> str:
    """A QR always renders dark-on-light — scanners need that contrast."""
    qr = segno.make(data, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=8, border=2, dark="#000000", light="#ffffff")
    return buffer.getvalue().decode("utf-8")


# ------------------------------------------------------------ export/import

EXPORT_MEDIA_TYPES = {
    "json": "application/json; charset=utf-8",
    "yaml": "application/yaml; charset=utf-8",
}


def _export_response(document: dict[str, Any], fmt: str, filename: str) -> Response:
    if fmt not in EXPORT_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"неизвестный формат: {fmt}")
    try:
        body = dump(document, fmt)
    except PortabilityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=EXPORT_MEDIA_TYPES[fmt],
        headers={"content-disposition": f'attachment; filename="{filename}.{fmt}"'},
    )


@guarded.get("/profiles/{profile_id}/export")
async def export_single_profile(
    request: Request, profile_id: int, format: str = "yaml", with_token: bool = True
) -> Response:
    profile = _repo(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    document = export_profile(profile.as_dict(), with_token=with_token)
    return _export_response(document, format, f"subremuxer-profile-{profile.id}")


@guarded.get("/export")
async def export_everything(
    request: Request, format: str = "yaml", with_tokens: bool = True
) -> Response:
    """The whole configuration: settings, templates and every profile."""
    document = export_bundle(
        [profile.as_dict() for profile in _repo(request).list()],
        [template.as_dict() for template in _templates(request).list()],
        request.app.state.db.all_settings(),
        with_tokens=with_tokens,
    )
    return _export_response(document, format, "subremuxer-config")


@guarded.post("/import")
async def import_configuration(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        document = parse_document(str(payload.get("content") or ""))
    except PortabilityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    importer = Importer(_repo(request), _templates(request), request.app.state.db)
    try:
        result = importer.apply(
            document,
            keep_tokens=bool(payload.get("keep_tokens", False)),
            with_settings=bool(payload.get("with_settings", True)),
        )
    except PortabilityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.cache = type(request.app.state.cache)()
    return {
        "kind": document["kind"],
        "profiles_created": result["profiles_created"],
        "templates_created": result["templates_created"],
        "settings_applied": result["settings_applied"],
        "errors": result["errors"],
    }


# -------------------------------------------------------------- config editor


def _current_bundle(request: Request) -> dict[str, Any]:
    return export_bundle(
        [profile.as_dict() for profile in _repo(request).list()],
        [template.as_dict() for template in _templates(request).list()],
        request.app.state.db.all_settings(),
        with_tokens=True,
    )


@guarded.get("/config")
async def read_config(request: Request, format: str = "yaml") -> dict[str, Any]:
    """The whole configuration as editable text, for the built-in editor."""
    if format not in EXPORT_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"неизвестный формат: {format}")
    return {"format": format, "content": dump(_current_bundle(request), format)}


@guarded.post("/config/validate")
async def validate_config(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Dry run: parse, validate and describe the change without touching anything."""
    try:
        document = parse_document(str(payload.get("content") or ""))
        plan = ConfigApplier(_repo(request), _templates(request), request.app.state.db).plan(
            document
        )
    except PortabilityError as exc:
        return {"ok": False, "errors": [str(exc)], "summary": None}
    plan.pop("_plan", None)
    return plan


@guarded.put("/config")
async def write_config(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Make the instance match the edited document. Validated in full beforehand."""
    try:
        document = parse_document(str(payload.get("content") or ""))
        result = ConfigApplier(_repo(request), _templates(request), request.app.state.db).apply(
            document
        )
    except PortabilityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.cache = type(request.app.state.cache)()
    fmt = str(payload.get("format") or "yaml")
    if fmt not in EXPORT_MEDIA_TYPES:
        fmt = "yaml"
    result["content"] = dump(_current_bundle(request), fmt)
    result["format"] = fmt
    return result


# ------------------------------------------------------------------ templates


@guarded.get("/templates")
async def list_templates(request: Request) -> list[dict[str, Any]]:
    return [template.as_dict() for template in _templates(request).list()]


@guarded.post("/templates", status_code=201)
async def create_template(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    payload.pop("builtin_id", None)  # only the app itself may claim a built-in id
    try:
        template = _templates(request).create(payload)
    except (TemplateError, FilterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return template.as_dict()


@guarded.post("/templates/from-profile/{profile_id}", status_code=201)
async def template_from_profile(
    request: Request, profile_id: int, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    profile = _repo(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    name = str(payload.get("name") or f"Из профиля «{profile.name}»")
    try:
        template = _templates(request).from_profile(
            profile.as_dict(), name, str(payload.get("description") or "")
        )
    except (TemplateError, FilterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return template.as_dict()


@guarded.put("/templates/{template_id}")
async def update_template(
    request: Request, template_id: int, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    try:
        template = _templates(request).update(template_id, payload)
    except (TemplateError, FilterError) as exc:
        status = 404 if "не найден" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return template.as_dict()


@guarded.delete("/templates/{template_id}")
async def delete_template(request: Request, template_id: int) -> dict[str, bool]:
    if not _templates(request).delete(template_id):
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    return {"ok": True}


@guarded.post("/templates/restore-builtins")
async def restore_builtin_templates(request: Request) -> dict[str, Any]:
    return {"restored": _templates(request).restore_builtins()}


# ---------------------------------------------------------------- capture


@guarded.get("/probe")
async def probe_info(request: Request) -> dict[str, Any]:
    token = get_probe_token(request.app.state.db)
    return {
        "token": token,
        "url": f"{_public_base(request)}/probe/{token}",
        "captures": _probes(request).list(),
    }


@guarded.post("/probe/rotate")
async def probe_rotate(request: Request) -> dict[str, Any]:
    token = rotate_probe_token(request.app.state.db)
    return {"token": token, "url": f"{_public_base(request)}/probe/{token}"}


@guarded.get("/probe/qr.svg")
async def probe_qr(request: Request) -> Response:
    token = get_probe_token(request.app.state.db)
    return Response(
        content=qr_svg(f"{_public_base(request)}/probe/{token}"), media_type="image/svg+xml"
    )


@guarded.delete("/probe/captures")
async def clear_captures(request: Request, capture_id: int | None = None) -> dict[str, int]:
    return {"deleted": _probes(request).clear(capture_id)}


@guarded.post("/probe/captures/restore")
async def restore_captures(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, int]:
    """Undo a capture deletion by recreating the rows the caller kept."""
    rows = payload.get("captures") or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="captures должен быть списком")
    return {"restored": _probes(request).restore(rows)}


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
