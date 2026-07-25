"""Application wiring."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import Database
from .pipeline import UpstreamCache
from .routers import admin, subscription
from .security import LoginThrottle
from .upstream import UpstreamFetcher

logger = logging.getLogger("subremuxer")

STATIC_DIR = Path(__file__).parent / "static"

PRUNE_INTERVAL_SECONDS = 3600


async def _maintenance_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    db: Database = app.state.db
    while True:
        try:
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
            db.purge_sessions()
            db.prune_logs(settings.log_retention_days, settings.log_max_rows)
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
        title="subremuxer",
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

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg")

    return app


app = create_app()
