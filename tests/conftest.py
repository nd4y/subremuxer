from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from .idp import ADMIN_GROUP, CLIENT_ID, ISSUER, VIEWER_GROUP, FakeIdP

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
    for name in (
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
        "OIDC_ADMIN_GROUP",
        "OIDC_VIEWER_GROUP",
        "OIDC_AUTO_LOGIN",
        "OIDC_GROUPS_CLAIM",
        "OIDC_REDIRECT_URL",
        "OIDC_DISPLAY_NAME",
        "AUTH_DISABLE_LOGIN_FORM",
    ):
        monkeypatch.delenv(name, raising=False)
    os.environ.setdefault("PYTHONHASHSEED", "0")


# ------------------------------------------------------------- OIDC fixtures


@pytest.fixture()
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture()
def oidc_settings(tmp_path, monkeypatch):
    """Settings with an identity provider configured, tunable per test."""

    def _build(**env: str):
        from app.config import Settings, reset_settings

        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://sub.example.org")
        monkeypatch.setenv("OIDC_ISSUER", ISSUER)
        monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "s3cret")
        monkeypatch.setenv("OIDC_ADMIN_GROUP", ADMIN_GROUP)
        monkeypatch.setenv("OIDC_VIEWER_GROUP", VIEWER_GROUP)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        reset_settings()
        return Settings()

    yield _build

    from app.config import reset_settings

    reset_settings()


@pytest.fixture()
def oidc_client(oidc_settings, idp, monkeypatch):
    """A running app whose provider talks to the fake IdP instead of the network.

    The transport is swapped in the constructor rather than after startup, so
    even the discovery check the app runs on boot reaches the fake provider.
    """
    from app import main as main_module
    from app.main import create_app
    from app.oidc import OIDCProvider

    class MockedProvider(OIDCProvider):
        def __init__(self, settings) -> None:
            super().__init__(settings)
            self._client = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))

    monkeypatch.setattr(main_module, "OIDCProvider", MockedProvider)

    opened: list[TestClient] = []

    def _factory(**env: str) -> TestClient:
        client = TestClient(create_app(oidc_settings(**env)))
        client.__enter__()
        opened.append(client)
        return client

    yield _factory

    for client in opened:
        client.__exit__(None, None, None)
