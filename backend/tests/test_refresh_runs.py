"""The refresh history route and how long the history is kept."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from vibonomics import events, jobs
from vibonomics.api import refresh as refresh_api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import RefreshRun


@pytest.fixture(autouse=True)
def database(monkeypatch):
    init_db()
    monkeypatch.setattr(events, "_loop", None)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(refresh_api.router)
    return TestClient(app)


def _run(kind: str, status: str, age_days: float) -> int:
    with session_scope() as s:
        run = RefreshRun(
            kind=kind, trigger="test", status=status, started_at=datetime.now(UTC) - timedelta(days=age_days)
        )
        s.add(run)
        s.flush()
        return run.id


def _present(ids: list[int]) -> set[int]:
    with session_scope() as s:
        return set(s.scalars(select(RefreshRun.id).where(RefreshRun.id.in_(ids))))


def test_the_runs_limit_must_be_between_one_and_five_hundred(client):
    for _ in range(3):
        _run("limit-test", "ok", 0)

    for bad in (-1, 0, 501):
        assert client.get("/api/refresh/runs", params={"limit": bad}).status_code == 422, bad
    assert len(client.get("/api/refresh/runs", params={"limit": 1}).json()) == 1
    assert client.get("/api/refresh/runs", params={"limit": 500}).status_code == 200
    assert client.get("/api/refresh/runs").status_code == 200


def test_starting_a_run_prunes_what_is_older_than_the_retention_window():
    assert jobs.RUN_RETENTION_DAYS == 90
    old_failed = _run("prune-test", "failed", 120)
    old_ok = _run("prune-test", "ok", 110)
    newest_ok = _run("prune-test", "ok", 100)  # the last good run of its kind: value.py reads its time
    older_failed = _run("prune-test", "failed", 95)
    recent = _run("prune-test", "failed", 10)
    stuck = _run("prune-test", "running", 200)  # recovered at the next start, not here
    only_run = _run("prune-test-idle", "failed", 200)  # the newest of its kind, so the status page still has it
    everything = [old_failed, old_ok, newest_ok, older_failed, recent, stuck, only_run]

    jobs._start_run("prune-test-trigger", "test")

    assert _present(everything) == {newest_ok, recent, stuck, only_run}
