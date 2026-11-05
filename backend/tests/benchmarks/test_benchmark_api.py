"""The benchmark routes: which rows they serve, what an alias may name, and that events follow the commit."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from vibonomics import events
from vibonomics.api import benchmarks as benchmarks_api
from vibonomics.api import settings as settings_api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import BenchmarkScore, ModelAlias
from vibonomics.settings_store import AppSettings, load_settings, save_settings

AA = "artificial-analysis"
OPEN = "epoch"  # on by default, no terms


@pytest.fixture(autouse=True)
def database(monkeypatch):
    init_db()
    monkeypatch.setattr(events, "_loop", None)
    with session_scope() as s:
        save_settings(s, AppSettings())
        s.execute(delete(BenchmarkScore).where(BenchmarkScore.source.in_([AA, OPEN])))
        s.execute(delete(ModelAlias))
    yield
    with session_scope() as s:
        s.execute(delete(BenchmarkScore).where(BenchmarkScore.source.in_([AA, OPEN])))
        s.execute(delete(ModelAlias))


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(benchmarks_api.router)
    app.include_router(settings_api.router)
    return TestClient(app)


def _row(source: str, name: str, canonical: str | None = None) -> BenchmarkScore:
    return BenchmarkScore(
        source=source,
        metric="m",
        dimension="intelligence",
        source_model=name,
        canonical_id=canonical,
        value=50.0,
        percentile=50.0,
    )


def _seed_both_sources() -> None:
    with session_scope() as s:
        s.add_all([_row(AA, "aa model"), _row(OPEN, "open model")])


def _served(client: TestClient, **params: object) -> list[tuple[str, str]]:
    response = client.get("/api/benchmarks/rows", params=params)
    assert response.status_code == 200, response.text
    return [(r["source"], r["source_model"]) for r in response.json()]


def _stored(source: str) -> int:
    with session_scope() as s:
        return len(s.scalars(select(BenchmarkScore).where(BenchmarkScore.source == source)).all())


def _put_benchmarks(client: TestClient, *, aa_enabled: bool, terms: bool, epoch_enabled: bool = True) -> None:
    current = client.get("/api/settings").json()["settings"]
    sources = {**current["benchmarks"]["sources"], AA: {"enabled": aa_enabled}, OPEN: {"enabled": epoch_enabled}}
    benchmarks = {**current["benchmarks"], "sources": sources, "aa_terms_acknowledged": terms}
    response = client.put("/api/settings", json={"settings": {**current, "benchmarks": benchmarks}})
    assert response.status_code == 200, response.text


def test_rows_of_a_source_that_is_not_in_use_are_not_served(client):
    _seed_both_sources()

    # Artificial Analysis is opt-in, and this route is open to visitors with no login.
    assert _served(client) == [(OPEN, "open model")]
    assert _served(client, source=AA) == []

    with session_scope() as s:
        settings = load_settings(s)
        settings.benchmarks.sources[AA] = settings.benchmark_source(AA, False).model_copy(update={"enabled": True})
        save_settings(s, settings)
    assert _served(client, source=AA) == [], "enabled, but its terms are not acknowledged"

    with session_scope() as s:
        settings = load_settings(s)
        settings.benchmarks.aa_terms_acknowledged = True
        save_settings(s, settings)
    assert _served(client, source=AA) == [(AA, "aa model")]


def test_turning_a_source_off_deletes_its_rows(client):
    _put_benchmarks(client, aa_enabled=True, terms=True)
    _seed_both_sources()
    assert _served(client) == [(AA, "aa model"), (OPEN, "open model")]

    _put_benchmarks(client, aa_enabled=False, terms=True)

    assert (_stored(AA), _stored(OPEN)) == (0, 1)


def test_withdrawing_the_terms_deletes_the_rows_of_a_source_that_needs_them(client):
    _put_benchmarks(client, aa_enabled=True, terms=True)
    _seed_both_sources()

    _put_benchmarks(client, aa_enabled=True, terms=False)

    assert (_stored(AA), _stored(OPEN)) == (0, 1)


def test_saving_settings_that_leave_a_source_on_keeps_its_rows(client):
    _put_benchmarks(client, aa_enabled=True, terms=True)
    _seed_both_sources()

    _put_benchmarks(client, aa_enabled=True, terms=True)

    assert (_stored(AA), _stored(OPEN)) == (1, 1)


async def test_a_refresh_clears_the_rows_a_switched_off_source_left_behind(monkeypatch):
    from vibonomics import jobs
    from vibonomics.benchmarks.registry import SOURCES
    from vibonomics.catalog.modelsdev import ModelsDevCatalog

    async def load_catalog(force: bool = False) -> ModelsDevCatalog:
        return ModelsDevCatalog({})

    monkeypatch.setattr(jobs, "load_catalog", load_catalog)
    monkeypatch.setattr(jobs, "SOURCES", {AA: SOURCES[AA]})  # never fetched: it is off
    _seed_both_sources()

    await jobs._refresh_benchmarks(0)

    assert (_stored(AA), _stored(OPEN)) == (0, 1)
