"""Application wiring."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME
from .config import Settings, get_settings
from .db import Database
from .pipeline import UpstreamCache
from .profiles import ProfileRepository
from .routers import admin, probe, subscription
from .security import LoginThrottle
from .templates import TemplateRepository
from .upstream import UpstreamFetcher

logger = logging.getLogger("subremuxer")

STATIC_DIR = Path(__file__).parent / "static"

PRUNE_INTERVAL_SECONDS = 3600

#: How long a deleted profile stays recoverable. The UI offers Undo for a few
#: seconds; this is the far larger safety net behind it.
DELETED_PROFILE_GRACE_SECONDS = 24 * 3600


async def _maintenance_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    db: Database = app.state.db
    while True:
        try:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
            db.purge_sessions()
            db.prune_logs(settings.log_retention_days, settings.log_max_rows)
            ProfileRepository(db).purge_deleted(DELETED_PROFILE_GRACE_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - maintenance must never kill the app
            logger.exception("maintenance pass failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings.db_path)
        app.state.fetcher = UpstreamFetcher(settings)
        app.state.cache = UpstreamCache()
        app.state.throttle = LoginThrottle()

        app.state.db.purge_sessions()
        app.state.db.prune_logs(settings.log_retention_days, settings.log_max_rows)
        ProfileRepository(app.state.db).purge_deleted(DELETED_PROFILE_GRACE_SECONDS)
        seeded = TemplateRepository(app.state.db).seed_builtins()
        if seeded:
            logger.info("добавлено встроенных шаблонов: %s", seeded)

        if settings.generated_password:
            logger.warning(
                "ADMIN_PASSWORD не задан — сгенерирован временный пароль: %s",
                settings.admin_password,
            )

        task = asyncio.create_task(_maintenance_loop(app))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await app.state.fetcher.aclose()
            app.state.db.close()

    app = FastAPI(
        title=APP_NAME,
        description=(
            "Фильтрующий прокси для подписок прокси-серверов: подменяет HWID, "
            "отсеивает серверы по regexp и протоколам и отдаёт результат в том же формате."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.include_router(admin.router)
    app.include_router(admin.guarded)
    app.include_router(subscription.router)
    app.include_router(probe.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> HTMLResponse:
            # The shell must never be cached, and its asset URLs carry a content
            # hash — otherwise a browser keeps running the previous release's JS
            # after the container is updated.
            return HTMLResponse(
                content=_index_html(),
                headers={"cache-control": "no-cache, must-revalidate"},
            )

        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest() -> FileResponse:
            return FileResponse(
                STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json"
            )

        @app.get("/apple-touch-icon.png", include_in_schema=False)
        async def apple_touch_icon() -> FileResponse:
            return FileResponse(STATIC_DIR / "apple-touch-icon.png")

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker() -> Response:
            # Served from the root, otherwise its scope would be limited to
            # /static and it could not control the app shell.
            source = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
            return Response(
                content=source.replace("__VERSION__", assets_version()),
                media_type="application/javascript; charset=utf-8",
                headers={"cache-control": "no-cache", "service-worker-allowed": "/"},
            )

    return app


#: Assets whose URL gets a content hash appended when the shell is served.
VERSIONED_ASSETS = ("app.js", "help.js", "styles.css")


def _asset_hash(name: str) -> str:
    path = STATIC_DIR / name
    if not path.is_file():
        return "0"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def assets_version() -> str:
    """One hash covering every versioned asset, used to name the worker's cache."""
    combined = "".join(_asset_hash(name) for name in VERSIONED_ASSETS)
    return hashlib.sha256(combined.encode()).hexdigest()[:10]


def _index_html() -> str:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in VERSIONED_ASSETS:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={_asset_hash(name)}")
    return html


app = create_app()
