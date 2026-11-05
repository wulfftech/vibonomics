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


def _stored_extra(provider_id: int, model_id: str) -> dict:
    with session_scope() as s:
        row = s.scalars(select(Model).where(Model.provider_config_id == provider_id, Model.model_id == model_id)).one()
        return dict(row.extra)


ENDPOINTS = [{"provider": "DeepInfra", "tag": "deepinfra/fp8", "input": 0.1, "output": 0.4, "cache_read": 0.02}]


def test_a_refresh_with_no_endpoint_data_keeps_the_endpoints_the_model_already_had():
    # A failed /endpoints lookup, or the discounts setting turned off, leaves the offer without an "endpoints" key.
    # Replacing the stored extra wholesale used to drop them, and the preset lost its pins until the next good run.
    provider_id = _provider("endpoints-kept")
    jobs.persist_offers(
        provider_id, [ModelOffer(model_id="m", name="M", extra={"endpoints": ENDPOINTS, "description": "old"})]
    )
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", extra={"description": "new"})])
    extra = _stored_extra(provider_id, "m")
    assert extra["endpoints"] == ENDPOINTS
    assert extra["description"] == "new"  # the rest of the offer still replaces what was stored


def test_a_lookup_that_succeeded_with_no_endpoints_does_not_keep_stale_ones():
    provider_id = _provider("endpoints-emptied")
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", extra={"endpoints": ENDPOINTS})])
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", extra={"endpoints": []})])
    assert _stored_extra(provider_id, "m")["endpoints"] == []


def test_a_model_that_became_free_does_not_keep_endpoints_that_were_never_looked_up_for_it():
    # Free models skip the endpoint lookup, so their offer has none by design, not because a lookup failed.
    provider_id = _provider("endpoints-free")
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", extra={"endpoints": ENDPOINTS})])
    jobs.persist_offers(provider_id, [ModelOffer(model_id="m", name="M", is_free=True, extra={})])
    assert "endpoints" not in _stored_extra(provider_id, "m")


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


# ---- the run's own bookkeeping must not be able to strand the job ---------------------------------------------


def _locked(*_args, **_kwargs):
    from sqlalchemy.exc import OperationalError

    raise OperationalError("UPDATE refresh_runs", {}, Exception("database is locked"))


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: seen.append((name, data or {})))
    return seen


async def test_a_run_that_cannot_be_recorded_still_ends_with_a_finished_event_and_frees_the_lock(
    monkeypatch, published
):
    ran: list[str] = []

    async def usage(_run_id: int) -> dict:
        ran.append("usage")
        return {}

    monkeypatch.setattr(jobs, "_refresh_usage", usage)
    monkeypatch.setattr(jobs, "_start_run", _locked)

    await jobs.run_job("usage", "test")  # must not raise

    assert ran == []
    assert [name for name, _ in published] == ["refresh.finished"]
    data = published[0][1]
    assert (data["kind"], data["status"]) == ("usage", "failed")
    assert "database is locked" in data["message"]
    assert not jobs.is_running("usage")


async def test_a_run_that_cannot_be_closed_still_ends_with_a_finished_event_and_frees_the_lock(monkeypatch, published):
    async def usage(_run_id: int) -> dict:
        return {}

    monkeypatch.setattr(jobs, "_refresh_usage", usage)
    monkeypatch.setattr(jobs, "_finish_run", _locked)

    await jobs.run_job("usage", "test")  # must not raise

    assert [name for name, _ in published] == ["refresh.started", "refresh.finished"]
    assert published[1][1]["status"] == "ok"
    assert not jobs.is_running("usage")
    # The row could not be closed, so it is still "running"; other tests read the first usage run.
    with session_scope() as s:
        s.delete(s.get(RefreshRun, published[0][1]["run_id"]))


async def test_the_sequence_of_a_manual_refresh_goes_on_after_a_kind_raises(monkeypatch):
    from vibonomics.api import refresh as refresh_api

    ran: list[str] = []

    async def run_job(kind: str, trigger: str = "manual", provider_ids=None) -> None:
        ran.append(kind)
        if kind == "models":
            raise RuntimeError("database is locked")

    monkeypatch.setattr(refresh_api, "run_job", run_job)

    await refresh_api._run_sequence(["models", "balances", "benchmarks", "usage"])

    assert ran == ["models", "balances", "benchmarks", "usage"]


async def test_the_startup_refresh_goes_on_after_a_kind_raises(monkeypatch):
    from vibonomics import app as app_module
    from vibonomics.settings_store import AppSettings

    ran: list[str] = []

    async def run_job(kind: str, trigger: str = "manual", provider_ids=None) -> None:
        ran.append(kind)
        if kind == "models":
            raise RuntimeError("database is locked")

    async def catch_up(_settings) -> None:
        ran.append("preset")

    monkeypatch.setattr(app_module, "run_job", run_job)
    monkeypatch.setattr(app_module.scheduler, "catch_up_preset", catch_up)

    await app_module._startup_work(AppSettings(), delay=0)

    assert ran == ["models", "balances", "benchmarks", "usage", "preset"]


# ---- models.dev down, nothing cached --------------------------------------------------------------------------

DOWN = "GET https://models.dev/api.json: ConnectError: no route"


def _stored_enrichment(provider_id: int) -> dict[str, tuple]:
    with session_scope() as s:
        rows = s.scalars(select(Model).where(Model.provider_config_id == provider_id))
        return {m.model_id: (m.canonical_id, m.creator, m.family, m.release_date, m.open_weights) for m in rows}


class _CatalogueFreeAdapter:
    """What an adapter that needs no catalogue (OpenRouter's) offers when the catalogue is empty: no enrichment."""

    async def fetch_models(self) -> list[ModelOffer]:
        return [ModelOffer(model_id="acme/m1", name="M1 renamed", price=Price(input=1.0, output=2.0))]


def _seed_enriched_model(provider_id: int) -> None:
    with session_scope() as s:
        s.add(
            Model(
                provider_config_id=provider_id,
                model_id="acme/m1",
                name="M1",
                canonical_id="acme/m1",
                creator="acme",
                family="m",
                release_date="2026-01-01",
                open_weights=True,
                active=True,
            )
        )


async def test_a_refresh_with_models_dev_down_keeps_the_enrichment_already_stored_and_says_so(monkeypatch):
    provider_id = _provider("enriched, catalogue down")
    _seed_enriched_model(provider_id)

    async def down(force: bool = False) -> ModelsDevCatalog:
        return ModelsDevCatalog({}, degraded=DOWN)

    monkeypatch.setattr(jobs, "load_catalog", down)
    monkeypatch.setattr(jobs, "build_adapter", lambda cfg, ctx: _CatalogueFreeAdapter())
    monkeypatch.setattr(jobs, "_load_providers_and_settings", lambda: _enabled([provider_id]))

    await jobs.run_job("models", "test", {provider_id})

    assert _stored_enrichment(provider_id) == {"acme/m1": ("acme/m1", "acme", "m", "2026-01-01", True)}
    with session_scope() as s:
        # What the provider itself reports is still taken: only the catalogue's part is held.
        assert s.scalars(select(Model).where(Model.provider_config_id == provider_id)).one().name == "M1 renamed"
    run = _last_run("models")
    assert run.status == "partial"
    assert "models.dev is unavailable" in run.message and "no route" in run.message
    assert run.stats["ok"] == 1 and run.stats["partial"] == 1


async def test_a_refresh_with_a_working_catalogue_still_replaces_the_enrichment(monkeypatch):
    provider_id = _provider("enriched, catalogue up")
    _seed_enriched_model(provider_id)
    monkeypatch.setattr(jobs, "build_adapter", lambda cfg, ctx: _CatalogueFreeAdapter())
    monkeypatch.setattr(jobs, "_load_providers_and_settings", lambda: _enabled([provider_id]))

    await jobs.run_job("models", "test", {provider_id})

    assert _stored_enrichment(provider_id) == {"acme/m1": (None, None, None, None, None)}
    assert _last_run("models").status == "ok"


# ---- a provider saved while its kind is running --------------------------------------------------------------


async def _start_held_run(monkeypatch, kind: str = "models") -> tuple[list, asyncio.Event, asyncio.Task]:
    """A run of ``kind`` that holds its lock until the returned event is set; the list records each call."""
    calls: list = []
    release = asyncio.Event()

    async def job(_run_id: int, provider_ids: set[int] | None = None) -> dict:
        calls.append(provider_ids)
        if len(calls) == 1:
            await release.wait()
        return {"ok": 1}

    monkeypatch.setattr(jobs, f"_refresh_{kind}", job)
    task = asyncio.create_task(jobs.run_job(kind, "schedule"))
    await asyncio.sleep(0.05)
    assert jobs.is_running(kind)
    return calls, release, task


async def test_a_provider_refresh_dropped_by_a_run_in_progress_runs_when_the_lock_frees(monkeypatch):
    calls, release, held = await _start_held_run(monkeypatch)

    await jobs.run_job("models", "provider", {1})
    await jobs.run_job("models", "provider", {2, 3})
    assert calls == [None]  # nothing runs beside the one in flight

    release.set()
    await held

    # One follow-up for the union, not one per call, and it is recorded as a run of its own.
    assert calls == [None, {1, 2, 3}]
    assert not jobs.is_running("models")
    assert _last_run("models").trigger == "provider"


async def test_balances_keep_their_dropped_providers_too(monkeypatch):
    calls, release, held = await _start_held_run(monkeypatch, "balances")

    await jobs.run_job("balances", "provider", {7})
    release.set()
    await held

    assert calls == [None, {7}]


async def test_a_full_refresh_dropped_by_a_run_in_progress_is_still_not_queued(monkeypatch):
    calls, release, held = await _start_held_run(monkeypatch)

    await jobs.run_job("models", "manual")
    await jobs.run_job("models", "schedule")
    release.set()
    await held

    assert calls == [None]


async def test_providers_dropped_during_the_follow_up_run_get_their_own(monkeypatch):
    calls: list = []
    release = asyncio.Event()

    async def job(_run_id: int, provider_ids: set[int] | None = None) -> dict:
        calls.append(provider_ids)
        if provider_ids == {1}:
            # A save lands while the follow-up for the first one is running.
            await jobs.run_job("models", "provider", {2})
        elif provider_ids is None:
            await release.wait()
        return {"ok": 1}

    monkeypatch.setattr(jobs, "_refresh_models", job)
    held = asyncio.create_task(jobs.run_job("models", "schedule"))
    await asyncio.sleep(0.05)
    await jobs.run_job("models", "provider", {1})
    release.set()
    await held

    assert calls == [None, {1}, {2}]


async def test_a_provider_refresh_with_nothing_running_is_not_held_back(monkeypatch):
    calls: list = []

    async def job(_run_id: int, provider_ids: set[int] | None = None) -> dict:
        calls.append(provider_ids)
        return {"ok": 1}

    monkeypatch.setattr(jobs, "_refresh_models", job)

    await jobs.run_job("models", "provider", {4})
    await jobs.run_job("models", "provider", {5})

    assert calls == [{4}, {5}]
