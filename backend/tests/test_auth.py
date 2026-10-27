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
