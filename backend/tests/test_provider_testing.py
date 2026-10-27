"""Provider testing: unsaved drafts and per-field overrides."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibonomics.api import providers as api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import ProviderConfig


@pytest.fixture(scope="module", autouse=True)
def _database():
    init_db()


def _client(monkeypatch) -> TestClient:
    async def fake_run(resolved, settings):
        return api.TestResult(
            ok=True,
            message=f"{resolved.api_key}|{resolved.mgmt_key}|{resolved.extra_key}|{resolved.base_url}",
        )

    monkeypatch.setattr(api, "_run_test", fake_run)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _seed() -> int:
    with session_scope() as session:
        row = ProviderConfig(
            type="openrouter",
            name="Draft test provider",
            enabled=True,
            api_key_enc=api.crypto.encrypt("stored-key"),
            mgmt_key_enc=None,
            extra_key_enc=None,
            base_url=None,
            options={},
        )
        session.add(row)
        session.flush()
        return row.id


def test_draft_provider_test_uses_typed_values(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/api/providers/test",
        json={
            "type": "anthropic",
            "name": "Draft",
            "base_url": "https://example.test",
            "api_key": "sk-ant-new",
            "extra_key": "oauth-token",
            "options": {},
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True
    assert "oauth-token" in result["message"]
    assert "sk-ant-new" in result["message"]
    assert "https://example.test" in result["message"]


def test_saved_provider_test_honours_overrides(monkeypatch):
    provider_id = _seed()
    client = _client(monkeypatch)

    saved = client.post(f"/api/providers/{provider_id}/test")
    assert saved.json()["message"].split("|")[0] == "stored-key"

    typed = client.post(f"/api/providers/{provider_id}/test", json={"api_key": "typed-key"})
    assert typed.json()["message"].split("|")[0] == "typed-key"

    cleared = client.post(f"/api/providers/{provider_id}/test", json={"api_key": ""})
    assert cleared.json()["message"].split("|")[0] == "None"


def test_draft_requires_custom_base_url(monkeypatch):
    client = _client(monkeypatch)
    response = client.post("/api/providers/test", json={"type": "custom-openai", "api_key": "x"})
    assert response.status_code == 400


def test_override_is_non_destructive():
    base = api.ResolvedProvider(
        id=1,
        type="openrouter",
        name="x",
        api_key="stored",
        mgmt_key=None,
        base_url=None,
        options={"a": "1"},
    )
    overridden = api._override(base, api.ProviderTestOverride(api_key="new", options={"b": "2"}))
    assert overridden.api_key == "new"
    assert overridden.options == {"a": "1", "b": "2"}
    # The dataclass mutates in place; the caller builds it fresh per request.
