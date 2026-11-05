"""A route's event must follow its commit: a client that refetches when it arrives reads through its own connection."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from vibonomics import events
from vibonomics.api import evals as evals_api
from vibonomics.api import providers as providers_api
from vibonomics.db import init_db, session_scope
from vibonomics.evals import runner
from vibonomics.orm import EvalRun, ProviderConfig


@pytest.fixture(autouse=True)
def database():
    init_db()
    yield
    with session_scope() as s:
        for provider in s.scalars(select(ProviderConfig).where(ProviderConfig.name.like("Order %"))):
            s.delete(provider)
        for run in s.scalars(select(EvalRun).where(EvalRun.targets == [])):
            s.delete(run)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(providers_api.router)
    app.include_router(evals_api.router)
    return TestClient(app)


def _provider_names() -> dict[int, str]:
    with session_scope() as s:  # another connection: it sees only what has been committed
        return {p.id: p.name for p in s.scalars(select(ProviderConfig))}


def _run_status(run_id: int) -> str | None:
    with session_scope() as s:
        run = s.get(EvalRun, run_id)
        return run.status if run else None


def _announced(monkeypatch, read) -> list[tuple[str, object]]:
    """Record what ``read()`` finds in the database at the moment each event goes out."""
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: seen.append((name, read())))
    return seen


def test_a_created_provider_is_stored_before_the_event(client, monkeypatch):
    seen = _announced(monkeypatch, lambda: sorted(_provider_names().values()))

    response = client.post(
        "/api/providers", json={"type": "custom-openai", "name": "Order created", "base_url": "http://x"}
    )

    assert response.status_code == 201
    assert len(seen) == 1 and seen[0][0] == "providers.changed"
    assert "Order created" in seen[0][1]


def test_an_updated_provider_is_stored_before_the_event(client, monkeypatch):
    created = client.post(
        "/api/providers", json={"type": "custom-openai", "name": "Order before", "base_url": "http://x"}
    )
    provider_id = created.json()["id"]
    seen = _announced(monkeypatch, lambda: _provider_names().get(provider_id))

    assert client.put(f"/api/providers/{provider_id}", json={"name": "Order after"}).status_code == 200

    assert seen == [("providers.changed", "Order after")]


def test_a_deleted_provider_is_gone_before_the_event(client, monkeypatch):
    created = client.post(
        "/api/providers", json={"type": "custom-openai", "name": "Order deleted", "base_url": "http://x"}
    )
    provider_id = created.json()["id"]
    seen = _announced(monkeypatch, lambda: provider_id in _provider_names())

    assert client.delete(f"/api/providers/{provider_id}").status_code == 204

    assert seen == [("providers.changed", False)]


@pytest.mark.parametrize("route", ["post_cancel", "delete"])
def test_an_eval_run_that_is_cancelled_or_deleted_is_stored_before_the_event(client, monkeypatch, route):
    with session_scope() as s:
        run = EvalRun(suite="quick", targets=[], progress={}, status="queued" if route == "post_cancel" else "done")
        s.add(run)
        s.flush()
        run_id = run.id
    monkeypatch.setattr(runner, "cancel", lambda _run_id: None)
    seen = _announced(monkeypatch, lambda: _run_status(run_id))

    if route == "post_cancel":
        assert client.post(f"/api/evals/runs/{run_id}/cancel").status_code == 200
        expected = "cancelled"
    else:
        assert client.delete(f"/api/evals/runs/{run_id}").status_code == 204
        expected = None

    assert seen == [("evals.finished", expected)]
