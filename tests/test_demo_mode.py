"""DEMO_MODE: no login at all, and guardrails so a public stand stays harmless."""

from __future__ import annotations

import httpx
import pytest

from . import fixtures
from .conftest import ADMIN_PASSWORD


@pytest.fixture()
def demo_settings(tmp_path, monkeypatch):
    from app.config import Settings, reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example.org")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    reset_settings()
    yield Settings()
    reset_settings()


@pytest.fixture()
def demo_client(demo_settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(demo_settings)) as client:
        yield client


def test_admin_endpoints_are_open(demo_client):
    for path in ("/api/profiles", "/api/meta", "/api/logs", "/api/settings", "/api/templates"):
        assert demo_client.get(path).status_code == 200, path


def test_the_client_is_told_it_is_a_demo(demo_client):
    assert demo_client.get("/api/auth/me").json() == {"authenticated": True, "demo": True}


def test_a_normal_instance_still_reports_no_demo(client):
    assert client.get("/api/auth/me").json() == {"authenticated": False, "demo": False}


def test_a_normal_instance_is_still_closed(client):
    assert client.get("/api/profiles").status_code == 401


def test_no_password_is_generated_for_a_demo(demo_settings):
    assert demo_settings.generated_password is False


def test_profiles_can_be_managed_without_logging_in(demo_client):
    created = demo_client.post(
        "/api/profiles",
        json={"name": "Демо", "upstream_url": "https://panel.example.org/sub/abc"},
    )
    assert created.status_code == 201
    assert demo_client.delete(f"/api/profiles/{created.json()['id']}").status_code == 200


def test_the_shell_carries_the_demo_banner(demo_client):
    html = demo_client.get("/").text
    assert 'id="demo-banner"' in html
    assert "Вход отключён" in html


# ------------------------------------------------------------- guardrails


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/sub",
        "http://localhost/sub",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/sub",
    ],
)
async def test_internal_targets_are_refused_in_demo_mode(demo_settings, url):
    """Without a password anyone could point a profile at the host's own network,
    so a demo must not be usable as a proxy into it."""
    from app.upstream import UpstreamError, UpstreamFetcher

    fetcher = UpstreamFetcher(demo_settings)
    try:
        with pytest.raises(UpstreamError) as excinfo:
            await fetcher._refuse_internal_targets(url)
        assert "демо-режиме" in str(excinfo.value)
    finally:
        await fetcher.aclose()


async def test_public_targets_are_allowed(demo_settings):
    from app.upstream import UpstreamFetcher

    fetcher = UpstreamFetcher(demo_settings)
    try:
        # 1.1.1.1 is a literal, so this resolves without touching DNS.
        await fetcher._refuse_internal_targets("https://1.1.1.1/sub")
    finally:
        await fetcher.aclose()


def test_the_guard_only_applies_to_demo_instances(make_client):
    """A private panel address is the normal case for a self-hosted install."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixtures.URI_LIST)

    client = make_client(handler)
    client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    profile = client.post(
        "/api/profiles",
        json={"name": "Внутренняя панель", "upstream_url": "http://192.168.0.10:21002/sub/abc"},
    ).json()
    assert client.get(f"/s/{profile['token']}").status_code == 200
