"""Browser-facing sign-in: the redirect to the provider and the way back.

These two endpoints are not part of the JSON API — they are navigated to, and
they answer with redirects or with a self-contained HTML page. That matters for
the failure path: when a login goes wrong the app shell may be exactly what the
user cannot reach, so the diagnosis has to arrive as a plain page that needs
nothing but a browser.
"""

from __future__ import annotations

import html
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import APP_NAME
from ..oidc import OIDCError, OIDCProvider
from ..security import SESSION_COOKIE, client_ip, issue_session

logger = logging.getLogger("subremuxer.auth")

router = APIRouter(prefix="/auth", include_in_schema=False)

#: Appended to the app URL to reach the login screen when automatic sign-in is
#: on. Same spelling as Grafana's, so the habit carries over.
ESCAPE_HATCH = "disableAutoLogin=true"


def _provider(request: Request) -> OIDCProvider | None:
    return getattr(request.app.state, "oidc", None)


def _redirect_uri(request: Request) -> str:
    settings = request.app.state.settings
    if settings.oidc_redirect_url:
        return settings.oidc_redirect_url
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/callback"


def _safe_next(raw: str | None) -> str:
    """Only ever redirect back inside this app.

    An open redirect on a login endpoint is the classic way to make a phishing
    link look legitimate, so anything absolute is dropped on the floor.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    parts = urlsplit(raw)
    # The fragment carries the in-app route, so dropping it would land every
    # sign-in on the front page regardless of where it started.
    return (
        parts.path
        + (f"?{parts.query}" if parts.query else "")
        + (f"#{parts.fragment}" if parts.fragment else "")
    )


@router.get("/oidc/login")
async def oidc_login(request: Request, next: str | None = None) -> Response:
    provider = _provider(request)
    if provider is None:
        return _error_page(request, "Вход через OIDC не настроен", status_code=404)
    try:
        url = await provider.begin(request.app.state.db, _redirect_uri(request), _safe_next(next))
    except OIDCError as exc:
        logger.warning("не удалось начать вход через OIDC: %s (%s)", exc.message, exc.detail)
        return _error_page(request, exc.message, detail=exc.detail)
    return RedirectResponse(url, status_code=303)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    provider = _provider(request)
    if provider is None:
        return _error_page(request, "Вход через OIDC не настроен", status_code=404)

    if error:
        # The provider refused before we ever saw a code — its own wording is
        # the most useful thing we can show.
        return _error_page(
            request, f"Провайдер отказал во входе: {error}", detail=error_description or ""
        )
    if not code or not state:
        return _error_page(request, "Провайдер вернул неполный ответ", detail="нет code или state")

    settings = request.app.state.settings
    try:
        identity, next_url = await provider.complete(request.app.state.db, code, state)
    except OIDCError as exc:
        logger.warning("вход через OIDC не удался: %s (%s)", exc.message, exc.detail)
        return _error_page(request, exc.message, detail=exc.detail, groups=exc.groups)

    token = issue_session(
        request.app.state.db,
        settings,
        client_ip(request, settings),
        request.headers.get("user-agent"),
        role=identity.role,
        method="oidc",
        subject=identity.subject,
        display_name=identity.display_name,
    )
    logger.info(
        "вход через OIDC: %s (%s), роль %s",
        identity.display_name or identity.subject,
        identity.email or "без почты",
        identity.role,
    )
    response = RedirectResponse(next_url, status_code=303)
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


def _error_page(
    request: Request,
    message: str,
    *,
    detail: str = "",
    groups: list[str] | None = None,
    status_code: int = 400,
) -> HTMLResponse:
    """A standalone page, styled but dependency-free.

    It always offers the escape hatch, because the most likely reader is an
    administrator who has just locked themselves out of their own instance.
    """
    settings = request.app.state.settings
    blocks = [f"<p class=lead>{html.escape(message)}</p>"]
    if detail:
        blocks.append(f"<p class=detail>{html.escape(detail)}</p>")
    if groups is not None:
        if groups:
            items = "".join(f"<li><code>{html.escape(name)}</code></li>" for name in groups)
            blocks.append(f"<p class=detail>В токене пришли группы:</p><ul>{items}</ul>")
        else:
            blocks.append(
                "<p class=detail>В токене не пришло ни одной группы — вероятно, "
                "в клиенте не настроен mapper, который кладёт их в claim, "
                "или на группу не назначен пользователь.</p>"
            )

    actions = [f'<a class="btn primary" href="/?{ESCAPE_HATCH}">Открыть экран входа</a>']
    if settings.oidc_enabled:
        actions.append('<a class="btn" href="/auth/oidc/login">Попробовать ещё раз</a>')

    body = "".join(blocks) + f'<div class=actions>{"".join(actions)}</div>'
    if settings.oidc_auto_login and settings.password_login_enabled:
        body += (
            "<p class=hint>Автоматический вход включён. Ссылка выше открывает "
            "экран входа в обход него — там доступен вход по мастер-паролю.</p>"
        )
    elif not settings.password_login_enabled:
        body += (
            "<p class=hint>Вход по мастер-паролю выключен настройкой "
            "<code>AUTH_DISABLE_LOGIN_FORM</code>. Чтобы вернуть его, снимите эту "
            "переменную окружения и перезапустите приложение.</p>"
        )

    page = f"""<!doctype html>
<html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Вход — {html.escape(APP_NAME)}</title>
<style>
  :root {{ color-scheme: light dark; --bg:#fdf7ff; --fg:#1d1b20; --card:#fff;
    --muted:#49454f; --line:#cac4d0; --accent:#65558f; --on-accent:#fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141218; --fg:#e6e0e9; --card:#211f26; --muted:#cac4d0;
      --line:#49454f; --accent:#d0bcff; --on-accent:#381e72; }}
  }}
  body {{ margin:0; min-height:100dvh; display:grid; place-items:center; padding:24px;
    background:var(--bg); color:var(--fg);
    font:16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width:34rem; width:100%; background:var(--card); border-radius:28px;
    padding:32px; box-sizing:border-box; }}
  h1 {{ font-size:1.5rem; margin:0 0 16px; font-weight:600; }}
  .lead {{ font-size:1.05rem; margin:0 0 12px; }}
  .detail {{ color:var(--muted); margin:0 0 12px; }}
  ul {{ margin:0 0 12px; padding-left:1.25rem; color:var(--muted); }}
  code {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.9em;
    background:color-mix(in srgb, var(--fg) 8%, transparent);
    padding:.1em .35em; border-radius:6px; }}
  .actions {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:24px; }}
  .btn {{ display:inline-block; padding:10px 24px; border-radius:100px; text-decoration:none;
    border:1px solid var(--line); color:var(--fg); }}
  .btn.primary {{ background:var(--accent); color:var(--on-accent); border-color:transparent; }}
  .hint {{ margin:24px 0 0; color:var(--muted); font-size:.9rem; }}
</style></head>
<body><main><h1>Не удалось войти</h1>{body}</main></body></html>"""
    return HTMLResponse(page, status_code=status_code)
