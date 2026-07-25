from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

ADMIN_PASSWORD = "test-password"


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    from app.config import Settings, reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sub.example.org")
    monkeypatch.setenv("LOG_RETENTION_DAYS", "0")
    reset_settings()
    yield Settings()
    reset_settings()


@pytest.fixture()
def make_client(settings) -> Iterator[Callable[..., TestClient]]:
    """Build a TestClient whose upstream fetcher is backed by a mock transport."""
    from app.main import create_app

    created: list[TestClient] = []

    def _factory(upstream_handler: Callable[[httpx.Request], httpx.Response] | None = None):
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        created.append(client)
        if upstream_handler is not None:
            app.state.fetcher._client = httpx.AsyncClient(
                transport=httpx.MockTransport(upstream_handler),
                timeout=5,
            )
        return client

    yield _factory

    for client in created:
        client.__exit__(None, None, None)


@pytest.fixture()
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture()
def admin(client) -> TestClient:
    response = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


def login(client: TestClient) -> TestClient:
    response = client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("UPSTREAM_PROXY", "COOKIE_SECURE", "TRUST_FORWARDED_FOR"):
        monkeypatch.delenv(name, raising=False)
    os.environ.setdefault("PYTHONHASHSEED", "0")
