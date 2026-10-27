"""The eval API must schedule runs on the event loop (sync endpoints run in a worker thread)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibonomics import crypto
from vibonomics.api import evals as evals_api
from vibonomics.db import init_db, session_scope
from vibonomics.evals import runner
from vibonomics.orm import Model, ProviderConfig


@pytest.fixture(scope="module", autouse=True)
def _database():
    init_db()


def test_start_run_endpoint_uses_the_event_loop(monkeypatch):
    with session_scope() as session:
        provider = ProviderConfig(
            type="openrouter",
            name="Eval API test provider",
            enabled=True,
            api_key_enc=crypto.encrypt("sk-or-test"),
            options={},
        )
        session.add(provider)
        session.flush()
        session.add(
            Model(
                provider_config_id=provider.id,
                model_id="fake-model",
                name="Eval API model",
                canonical_id=None,
                price_input=1.0,
                price_output=2.0,
                active=True,
            )
        )
        session.flush()
        provider_id = provider.id

    captured: dict[str, int] = {}

    def fake_schedule(run_id: int) -> None:
        # This failed when the endpoint was synchronous: no running loop in the worker thread.
        asyncio.get_running_loop()
        captured["run_id"] = run_id

    monkeypatch.setattr(runner, "schedule", fake_schedule)
    app = FastAPI()
    app.include_router(evals_api.router)
    client = TestClient(app)

    response = client.post(
        "/api/evals/runs",
        json={
            "suite": "quick",
            "targets": [{"provider_id": provider_id, "model_id": "fake-model"}],
            "judge": {"provider_id": provider_id, "model_id": "fake-model"},
            "task_ids": ["instr-json"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["id"] == captured["run_id"]
    assert response.json()["status"] == "queued"


def test_judge_support_excludes_client_only_and_unsupported_routes():
    opencode = SimpleNamespace(type="opencode")
    assert not evals_api._judge_supported(opencode, SimpleNamespace(is_free=True, extra={}))
    assert not evals_api._judge_supported(
        opencode, SimpleNamespace(is_free=False, extra={"endpoint": {"npm": "@ai-sdk/google"}})
    )
    assert evals_api._judge_supported(
        opencode, SimpleNamespace(is_free=False, extra={"endpoint": {"npm": "@ai-sdk/anthropic"}})
    )
    openrouter = SimpleNamespace(type="openrouter")
    assert evals_api._judge_supported(openrouter, SimpleNamespace(is_free=True, extra={}))
