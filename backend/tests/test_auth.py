"""The password guard on /api, with and without a URL base."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vibonomics.app import create_app
from vibonomics.config import get_config
from vibonomics.db import init_db


@pytest.fixture(params=["", "/vibonomics"], ids=["root", "url-base"])
def client(request, monkeypatch) -> TestClient:
    monkeypatch.setenv("VIBONOMICS_PASSWORD", "hunter2")
    monkeypatch.setenv("VIBONOMICS_URL_BASE", request.param)
    get_config.cache_clear()
    init_db()
    yield TestClient(create_app(), base_url=f"http://testserver{request.param}")
    get_config.cache_clear()


def test_api_needs_a_session(client):
    for path in ("/api/providers", "/api/settings", "/api/system/status", "/api/evals/runs"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json() == {"detail": "Not authenticated"}


def test_login_opens_the_api(client):
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/api/auth/status").json() == {"enabled": True, "authenticated": False}
    assert client.post("/api/auth/login", json={"password": "hunter2"}).status_code == 200
    assert client.get("/api/auth/status").json() == {"enabled": True, "authenticated": True}
    assert client.get("/api/providers").status_code == 200


def test_ingest_is_exempt_but_checks_its_token(client):
    response = client.post("/api/subscription/ingest/claude?token=wrong", json={})
    assert response.status_code == 401
    assert response.json() == {"detail": "Bad subscription ingest token"}


def test_the_health_check_path_answers_with_and_without_a_url_base(client):
    # The image's healthcheck asks for /ping at the root, whatever the URL base is.
    root = TestClient(client.app, base_url="http://testserver")
    assert root.get("/ping").status_code == 200
    assert root.get("/ping").text == "pong"


def test_changing_the_password_ends_every_session(client, monkeypatch):
    assert client.post("/api/auth/login", json={"password": "hunter2"}).status_code == 200
    assert client.get("/api/providers").status_code == 200

    monkeypatch.setenv("VIBONOMICS_PASSWORD", "a-new-password")
    get_config.cache_clear()
    assert client.get("/api/auth/status").json() == {"enabled": True, "authenticated": False}
    assert client.get("/api/providers").status_code == 401

    assert client.post("/api/auth/login", json={"password": "a-new-password"}).status_code == 200
    assert client.get("/api/providers").status_code == 200


def test_a_cookie_from_before_sessions_carried_the_password_digest_is_refused(client):
    from vibonomics.api import auth

    client.cookies.set(auth.COOKIE, auth._serializer().dumps("ok"))
    assert client.get("/api/providers").status_code == 401


@pytest.fixture
def host_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    monkeypatch.delenv("VIBONOMICS_URL_BASE", raising=False)
    monkeypatch.setenv("VIBONOMICS_ALLOWED_HOSTS", "vibonomics.example.com, 192.168.1.20")
    get_config.cache_clear()
    init_db()
    yield TestClient(create_app())
    get_config.cache_clear()


def test_only_listed_hosts_reach_the_api(host_client):
    assert host_client.get("/api/providers", headers={"Host": "vibonomics.example.com"}).status_code == 200
    assert host_client.get("/api/providers", headers={"Host": "192.168.1.20:2289"}).status_code == 200
    # A rebinding page arrives under its own name.
    rebound = host_client.get("/api/subscription/claude", headers={"Host": "evil.example.net:2289"})
    assert rebound.status_code == 400
    assert "ingest_token" not in rebound.text


def test_the_healthcheck_host_is_always_allowed(host_client):
    for host in ("127.0.0.1:2289", "localhost:2289"):
        assert host_client.get("/ping", headers={"Host": host}).status_code == 200


def test_no_host_list_means_any_host(monkeypatch):
    monkeypatch.delenv("VIBONOMICS_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    monkeypatch.delenv("VIBONOMICS_URL_BASE", raising=False)
    get_config.cache_clear()
    init_db()
    try:
        client = TestClient(create_app())
        assert client.get("/api/providers", headers={"Host": "anything.example"}).status_code == 200
    finally:
        get_config.cache_clear()
