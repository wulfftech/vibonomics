"""Refresh jobs: models, balances, price history and interrupted runs."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from vibonomics import events, jobs
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.db import init_db, session_scope
from vibonomics.orm import Balance, Model, PriceHistory, ProviderConfig, RefreshRun
from vibonomics.providers.base import BalanceInfo, ModelOffer, Price


@pytest.fixture(autouse=True)
def database(monkeypatch):
    init_db()
    monkeypatch.setattr(events, "_loop", None)

    async def empty_catalog(force: bool = False) -> ModelsDevCatalog:
        return ModelsDevCatalog({})

    monkeypatch.setattr(jobs, "load_catalog", empty_catalog)


def _provider(name: str) -> int:
    with session_scope() as s:
        cfg = ProviderConfig(type="custom-openai", name=name, enabled=True, options={})
        s.add(cfg)
        s.flush()
        return cfg.id


def _last_run(kind: str) -> RefreshRun:
    with session_scope() as s:
        run = s.scalars(select(RefreshRun).where(RefreshRun.kind == kind).order_by(RefreshRun.id.desc())).first()
        assert run is not None
        return run


def test_an_unpriced_model_records_its_price_once_not_on_every_refresh():
    provider_id = _provider("history")
    offers = [
        ModelOffer(model_id="unpriced", name="Unpriced"),
        ModelOffer(model_id="priced", name="Priced", price=Price(input=1.0, output=2.0)),
        ModelOffer(model_id="half", name="Half", price=Price(input=1.0, output=None)),
    ]
    for _ in range(4):
        jobs.persist_offers(provider_id, offers)
    with session_scope() as s:
        counts = dict(
            s.execute(
                select(Model.model_id, func.count(PriceHistory.id))
                .join(PriceHistory, PriceHistory.model_pk == Model.id)
                .where(Model.provider_config_id == provider_id)
                .group_by(Model.model_id)
            ).all()
        )
    assert counts == {"unpriced": 1, "priced": 1, "half": 1}


def test_a_price_change_adds_a_history_row_even_when_the_total_is_the_same():
    provider_id = _provider("history-swap")
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", price=Price(input=1.0, output=3.0))])
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", price=Price(input=3.0, output=1.0))])
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", price=Price(input=3.0, output=None))])
    with session_scope() as s:
        history = s.execute(
            select(PriceHistory.price_input, PriceHistory.price_output)
            .join(Model, PriceHistory.model_pk == Model.id)
            .where(Model.provider_config_id == provider_id)
            .order_by(PriceHistory.id)
        ).all()
    assert history == [(1.0, 3.0), (3.0, 1.0), (3.0, None)]


async def test_a_provider_that_fails_while_saving_does_not_fail_the_others_or_free_the_lock_early(monkeypatch):
    deleted_id = _provider("deleted mid-run")
    slow_id = _provider("slow")
    timeline: list[str] = []

    class Adapter:
        def __init__(self, cfg: ProviderConfig):
            self.cfg = cfg

        async def fetch_models(self) -> list[ModelOffer]:
            if self.cfg.id == deleted_id:
                with session_scope() as s:  # the operator deletes the provider while its refresh runs
                    s.delete(s.get(ProviderConfig, deleted_id))
                return [ModelOffer(model_id="gone", name="Gone")]
            await asyncio.sleep(0.2)
            timeline.append(f"slow persisting, lock held={jobs.is_running('models')}")
            return [ModelOffer(model_id="kept", name="Kept")]

    monkeypatch.setattr(jobs, "build_adapter", lambda cfg, ctx: Adapter(cfg))
    monkeypatch.setattr(jobs, "_load_providers_and_settings", lambda: _enabled([deleted_id, slow_id]), raising=True)

    await jobs.run_job("models", "test", {deleted_id, slow_id})

    assert timeline == ["slow persisting, lock held=True"]
    run = _last_run("models")
    assert run.status == "partial"
    assert run.stats["ok"] == 1 and run.stats["failed"] == 1
    # The driver's message, not the statement and its parameters.
    assert "FOREIGN KEY constraint failed" in run.message
    assert "INSERT" not in run.message and "parameters" not in run.message
    with session_scope() as s:
        assert [m.model_id for m in s.scalars(select(Model).where(Model.provider_config_id == slow_id))] == ["kept"]


def _enabled(ids: list[int]):
    from vibonomics.settings_store import AppSettings

    with session_scope() as s:
        return [s.get(ProviderConfig, i) for i in ids], AppSettings()


async def test_a_timed_out_fetch_says_so(monkeypatch):
    slow_id = _provider("times out")

    class Adapter:
        async def fetch_models(self) -> list[ModelOffer]:
            await asyncio.sleep(5)
            return []

        async def fetch_balance(self) -> BalanceInfo:
            await asyncio.sleep(5)
            return BalanceInfo(status="ok")

    monkeypatch.setattr(jobs, "build_adapter", lambda cfg, ctx: Adapter())
    monkeypatch.setattr(jobs, "_load_providers_and_settings", lambda: _enabled([slow_id]))
    monkeypatch.setattr(jobs, "PROVIDER_TIMEOUT", 0.05)
    monkeypatch.setattr(jobs, "BALANCE_TIMEOUT", 0.05)

    await jobs.run_job("models", "test", {slow_id})
    await jobs.run_job("balances", "test", {slow_id})

    assert _last_run("models").message == "times out: TimeoutError: timed out after 0.05s"
    assert _last_run("balances").message == "times out: timed out after 0.05s"
    with session_scope() as s:
        balance = s.get(Balance, slow_id)
        assert balance is not None and balance.status == "error"
        assert balance.message == "timed out after 0.05s"


def test_failure_text_is_never_empty_and_never_carries_sql():
    from sqlalchemy.exc import IntegrityError

    assert jobs.failure_text(TimeoutError(), 300) == "timed out after 300s"
    assert jobs.failure_text(TimeoutError()) == "timed out"
    assert jobs.failure_text(RuntimeError()) == "RuntimeError"
    assert jobs.failure_text(RuntimeError("boom")) == "boom"
    error = IntegrityError("INSERT INTO models VALUES (?)", ("secret",), Exception("FOREIGN KEY constraint failed"))
    assert jobs.failure_text(error) == "FOREIGN KEY constraint failed"


def test_runs_left_running_by_a_restart_are_marked_failed():
    with session_scope() as s:
        stale = RefreshRun(kind="benchmarks", trigger="schedule", status="running")
        done = RefreshRun(kind="benchmarks", trigger="schedule", status="ok")
        s.add_all([stale, done])
        s.flush()
        stale_id, done_id = stale.id, done.id

    jobs.recover_interrupted_runs()

    with session_scope() as s:
        stale_run, done_run = s.get(RefreshRun, stale_id), s.get(RefreshRun, done_id)
        assert stale_run is not None and done_run is not None
        assert (stale_run.status, stale_run.message) == ("failed", "Interrupted by a Vibonomics restart.")
        assert stale_run.finished_at is not None
        assert done_run.status == "ok"
