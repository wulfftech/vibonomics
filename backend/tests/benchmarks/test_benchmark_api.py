"""The benchmark routes: which rows they serve, what an alias may name, and that events follow the commit."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from vibonomics import events, jobs
from vibonomics.api import benchmarks as benchmarks_api
from vibonomics.api import settings as settings_api
from vibonomics.catalog.modelsdev import ModelsDevCatalog
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


def test_a_save_still_succeeds_when_the_stored_rows_cannot_be_cleared(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    _put_benchmarks(client, aa_enabled=True, terms=True)
    _seed_both_sources()

    def locked():
        raise OperationalError("DELETE FROM benchmark_scores", {}, Exception("database is locked"))

    monkeypatch.setattr(jobs, "session_scope", locked)
    _put_benchmarks(client, aa_enabled=False, terms=True)  # asserts the 200

    monkeypatch.setattr(jobs, "session_scope", session_scope)
    assert _served(client) == [(OPEN, "open model")]  # hidden by the route, and removed by a later save or refresh


def test_saving_settings_that_leave_a_source_on_keeps_its_rows(client):
    _put_benchmarks(client, aa_enabled=True, terms=True)
    _seed_both_sources()

    _put_benchmarks(client, aa_enabled=True, terms=True)

    assert (_stored(AA), _stored(OPEN)) == (1, 1)


def _use_catalogue(monkeypatch, catalog: ModelsDevCatalog) -> None:
    async def load_catalog(force: bool = False) -> ModelsDevCatalog:
        return catalog

    monkeypatch.setattr(benchmarks_api, "load_catalog", load_catalog)
    monkeypatch.setattr(jobs, "load_catalog", load_catalog)


M1 = ModelsDevCatalog({"vendor": {"id": "vendor", "models": {"m1": {"id": "m1", "canonical_model_id": "acme/m1"}}}})


def _aliases() -> list[tuple[str, str, str | None]]:
    with session_scope() as s:
        return [(a.source, a.source_model, a.canonical_id) for a in s.scalars(select(ModelAlias))]


def _canonical_of(source: str, name: str) -> tuple[str | None, str | None]:
    with session_scope() as s:
        row = s.scalars(
            select(BenchmarkScore).where(BenchmarkScore.source == source, BenchmarkScore.source_model == name)
        ).one()
        return row.canonical_id, row.match_method


def _alias_body(canonical: str | None) -> dict:
    return {"source": OPEN, "source_model": "open model", "canonical_id": canonical}


def test_an_alias_names_a_model_the_catalogue_lists(client, monkeypatch):
    _use_catalogue(monkeypatch, M1)
    with session_scope() as s:
        s.add(_row(OPEN, "open model"))

    response = client.put("/api/benchmarks/aliases", json=_alias_body("acme/m1"))

    assert response.status_code == 200 and response.json() == {"matched": 1}
    assert _canonical_of(OPEN, "open model") == ("acme/m1", "operator")


def test_an_alias_to_a_model_the_catalogue_does_not_list_is_refused(client, monkeypatch):
    _use_catalogue(monkeypatch, M1)
    with session_scope() as s:
        s.add(_row(OPEN, "open model"))

    response = client.put("/api/benchmarks/aliases", json=_alias_body("acme/typo"))

    assert response.status_code == 422 and "acme/typo" in response.text
    assert _aliases() == []
    assert _canonical_of(OPEN, "open model") == (None, None)


def test_a_name_can_still_be_ignored_with_a_null_alias(client, monkeypatch):
    _use_catalogue(monkeypatch, M1)
    with session_scope() as s:
        s.add(_row(OPEN, "open model"))

    assert client.put("/api/benchmarks/aliases", json=_alias_body(None)).status_code == 200

    assert _aliases() == [(OPEN, "open model", None)]


def test_an_alias_cannot_be_checked_while_the_catalogue_is_down_and_is_not_saved(client, monkeypatch):
    _use_catalogue(monkeypatch, ModelsDevCatalog({}, degraded="models.dev: no route"))
    with session_scope() as s:
        s.add(_row(OPEN, "open model", "acme/m1"))

    response = client.put("/api/benchmarks/aliases", json=_alias_body(None))

    assert response.status_code == 503
    assert _aliases() == []
    assert _canonical_of(OPEN, "open model") == ("acme/m1", None)


def test_the_alias_and_its_rematch_are_stored_before_the_event(client, monkeypatch):
    _use_catalogue(monkeypatch, M1)
    with session_scope() as s:
        s.add(_row(OPEN, "open model"))
    seen: dict[str, object] = {}

    def listener(name: str, data=None) -> None:
        # A client that refetches on the event reads through its own connection: only committed rows are visible.
        seen[name] = (_aliases(), _canonical_of(OPEN, "open model"))

    monkeypatch.setattr(events, "publish", listener)

    assert client.put("/api/benchmarks/aliases", json=_alias_body("acme/m1")).status_code == 200

    assert seen == {"benchmarks.changed": ([(OPEN, "open model", "acme/m1")], ("acme/m1", "operator"))}


async def test_a_rematch_reads_the_aliases_while_it_holds_the_write_lock(monkeypatch):
    """So an alias saved meanwhile waits for it, and cannot be reverted by a rematch that read the aliases earlier."""
    import sqlite3

    from vibonomics.benchmarks.matching import Matcher
    from vibonomics.config import get_config

    _use_catalogue(monkeypatch, M1)
    with session_scope() as s:
        s.add(_row(OPEN, "open model"))
    held: list[bool] = []
    real_match = Matcher.match

    def match(self, *args, **kwargs):
        other = sqlite3.connect(get_config().db_path, timeout=0.2)
        try:
            other.execute("BEGIN IMMEDIATE")
            held.append(False)
        except sqlite3.OperationalError:
            held.append(True)
        finally:
            other.close()
        return real_match(self, *args, **kwargs)

    monkeypatch.setattr(Matcher, "match", match)

    await jobs.rematch_scores()

    assert held and all(held)


async def test_a_refresh_clears_the_rows_a_switched_off_source_left_behind(monkeypatch):
    from vibonomics.benchmarks.registry import SOURCES

    _use_catalogue(monkeypatch, ModelsDevCatalog({}))
    monkeypatch.setattr(jobs, "SOURCES", {AA: SOURCES[AA]})  # never fetched: it is off
    _seed_both_sources()

    await jobs._refresh_benchmarks(0)

    assert (_stored(AA), _stored(OPEN)) == (0, 1)
