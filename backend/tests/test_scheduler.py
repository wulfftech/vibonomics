"""The refresh timers: saving settings must not restart them, and the preset catches up after a restart."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibonomics import app as app_module
from vibonomics import presets, scheduler
from vibonomics.api import settings as settings_api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import Setting
from vibonomics.settings_store import (
    AppSettings,
    GeneralSettings,
    PresetSettings,
    PresetsSettings,
    RefreshSettings,
    save_settings,
)

SLUG = "vibonomics-best-value"
JOB = f"preset-sync:{SLUG}"
ITEM = PresetSettings(enabled=True, auto_apply=True, interval_hours=12)
AUTO = PresetsSettings(items=[ITEM])


def _with(**patch: object) -> PresetsSettings:
    """The one automatic preset with fields changed."""
    return PresetsSettings(items=[ITEM.model_copy(update=patch)])


@pytest.fixture(autouse=True)
def database():
    init_db()
    with session_scope() as s:
        save_settings(s, AppSettings())
        row = s.get(Setting, presets.STATE_KEY)
        if row is not None:
            s.delete(row)


@pytest.fixture
async def running():
    scheduler.start(AppSettings())
    yield
    scheduler.shutdown()


def _timers() -> dict[str, datetime]:
    assert scheduler._scheduler is not None
    return {job.id: job.next_run_time for job in scheduler._scheduler.get_jobs()}


async def _later() -> None:
    await asyncio.sleep(0.05)  # a restarted timer would start from a later "now"


# ---- J-2 ------------------------------------------------------------------------------------------------------


async def test_saving_settings_that_leave_the_intervals_alone_does_not_restart_any_timer(running):
    scheduler.reschedule(AppSettings(presets=AUTO))
    before = _timers()
    assert set(before) == {
        "refresh-models",
        "refresh-balances",
        "refresh-benchmarks",
        "refresh-usage",
        JOB,
        "preset-catalog-sync",
    }

    for _ in range(3):
        await _later()
        scheduler.reschedule(AppSettings(presets=AUTO, general=GeneralSettings(instance_name="Renamed")))

    assert _timers() == before


async def test_a_fractional_interval_is_not_mistaken_for_a_change(running):
    # 0.1 h is 360.00000000000006 s as a float, and 360 s as a timedelta.
    settings = AppSettings(refresh=RefreshSettings(models_interval_hours=0.1))
    scheduler.reschedule(settings)
    before = _timers()

    await _later()
    scheduler.reschedule(settings)

    assert _timers() == before


async def test_changing_one_interval_moves_only_that_timer(running):
    scheduler.reschedule(AppSettings(presets=AUTO))
    before = _timers()
    await _later()

    scheduler.reschedule(AppSettings(presets=AUTO, refresh=RefreshSettings(benchmarks_interval_hours=48)))

    after = _timers()
    assert after["refresh-benchmarks"] > before["refresh-benchmarks"]
    job = scheduler._scheduler.get_job("refresh-benchmarks")  # type: ignore[union-attr]
    assert job.trigger.interval == timedelta(hours=48)
    assert {k: v for k, v in after.items() if k != "refresh-benchmarks"} == {
        k: v for k, v in before.items() if k != "refresh-benchmarks"
    }


async def test_changing_the_preset_interval_moves_only_the_preset_timer(running):
    scheduler.reschedule(AppSettings(presets=AUTO))
    before = _timers()
    await _later()

    scheduler.reschedule(AppSettings(presets=_with(interval_hours=6)))

    after = _timers()
    assert after[JOB] != before[JOB]
    assert {k: v for k, v in after.items() if k != JOB} == {k: v for k, v in before.items() if k != JOB}


# ---- several presets ------------------------------------------------------------------------------------------

SECOND = "vibonomics-second"
SECOND_JOB = f"preset-sync:{SECOND}"


def _two(**second: object) -> PresetsSettings:
    """One preset on a schedule, and a second one with ``second`` changed from automatic every six hours."""
    other = PresetSettings(slug=SECOND, enabled=True, auto_apply=True, interval_hours=6).model_copy(update=second)
    return PresetsSettings(items=[ITEM, other])


def _preset_jobs() -> dict[str, timedelta]:
    assert scheduler._scheduler is not None
    return {j.id: j.trigger.interval for j in scheduler._scheduler.get_jobs() if j.id.startswith("preset-sync:")}


async def test_only_a_preset_that_is_on_and_automatic_gets_a_timer_of_its_own(running):
    scheduler.reschedule(AppSettings(presets=_two(auto_apply=False)))
    assert _preset_jobs() == {JOB: timedelta(hours=12)}

    scheduler.reschedule(AppSettings(presets=_two()))
    assert _preset_jobs() == {JOB: timedelta(hours=12), SECOND_JOB: timedelta(hours=6)}

    scheduler.reschedule(AppSettings(presets=_two(enabled=False)))
    assert _preset_jobs() == {JOB: timedelta(hours=12)}


async def test_removing_a_preset_removes_its_timer_and_leaves_the_other(running):
    scheduler.reschedule(AppSettings(presets=_two()))
    kept = _timers()[JOB]

    scheduler.reschedule(AppSettings(presets=AUTO))

    assert _preset_jobs() == {JOB: timedelta(hours=12)}
    assert _timers()[JOB] == kept
    scheduler.reschedule(AppSettings())
    assert _preset_jobs() == {}


async def test_a_save_that_changes_no_preset_restarts_no_timer(running):
    scheduler.reschedule(AppSettings(presets=_two()))
    before = _timers()

    for _ in range(2):
        await _later()
        renamed = AppSettings(presets=_two(), general=GeneralSettings(instance_name="Renamed"))
        scheduler.reschedule(renamed)

    assert _timers() == before


async def test_changing_one_presets_interval_moves_only_its_timer(running):
    scheduler.reschedule(AppSettings(presets=_two()))
    before = _timers()
    await _later()

    scheduler.reschedule(AppSettings(presets=_two(interval_hours=3)))

    after = _timers()
    assert after[SECOND_JOB] != before[SECOND_JOB]
    assert {k: v for k, v in after.items() if k != SECOND_JOB} == {k: v for k, v in before.items() if k != SECOND_JOB}


async def test_a_timer_runs_the_sync_of_its_own_preset(running, synced):
    scheduler.reschedule(AppSettings(presets=_two()))
    assert scheduler._scheduler is not None

    await scheduler._scheduler.get_job(SECOND_JOB).func(*scheduler._scheduler.get_job(SECOND_JOB).args)

    assert synced == [f"{SECOND} schedule"]


# ---- J-10 -----------------------------------------------------------------------------------------------------


async def test_a_rejected_save_leaves_the_running_schedule_as_it_was(running):
    app = FastAPI()
    app.include_router(settings_api.router)
    client = TestClient(app)
    current = client.get("/api/settings").json()["settings"]
    before = _timers()
    intervals = {j.id: j.trigger.interval for j in scheduler._scheduler.get_jobs()}  # type: ignore[union-attr]

    # A valid new interval for one job and an impossible one for another, in one save.
    bad = {**current, "refresh": {**current["refresh"], "models_interval_hours": 3, "benchmarks_interval_hours": 1e12}}
    response = client.put("/api/settings", json={"settings": bad})
    assert response.status_code == 422

    assert _timers() == before
    assert {j.id: j.trigger.interval for j in scheduler._scheduler.get_jobs()} == intervals  # type: ignore[union-attr]
    stored = client.get("/api/settings").json()["settings"]["refresh"]
    assert stored["models_interval_hours"] == current["refresh"]["models_interval_hours"]


# ---- P-15 -----------------------------------------------------------------------------------------------------


def _state(age: timedelta | None, slug: str = SLUG, **extra: object) -> None:
    value: dict[str, object] = dict(extra)
    if age is not None:
        last = {"at": (datetime.now(UTC) - age).isoformat(), "status": "applied", "trigger": "schedule", "slug": slug}
        value["last"] = {slug: last}
    with session_scope() as s:
        s.add(Setting(key=presets.STATE_KEY, value=value))


@pytest.fixture
def synced(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_sync(slug: str, trigger: str = "schedule") -> None:
        calls.append(f"{slug} {trigger}")

    monkeypatch.setattr(presets, "sync", fake_sync)
    return calls


async def test_a_preset_that_is_overdue_at_startup_runs_once(synced):
    _state(timedelta(hours=13))

    await scheduler.catch_up_preset(AppSettings(presets=AUTO))

    assert synced == [f"{SLUG} startup"]


async def test_a_preset_that_has_never_run_runs_at_startup(synced):
    await scheduler.catch_up_preset(AppSettings(presets=AUTO))

    assert synced == [f"{SLUG} startup"]


async def test_a_preset_that_ran_within_its_interval_is_left_alone(synced):
    _state(timedelta(hours=11))

    await scheduler.catch_up_preset(AppSettings(presets=AUTO))

    assert synced == []


async def test_each_preset_catches_up_on_its_own_last_run(synced):
    # the first ran an hour ago, the second a day ago and its interval is six hours
    _state(timedelta(hours=1))
    with session_scope() as s:
        row = s.get(Setting, presets.STATE_KEY)
        assert row is not None
        stale = {"at": (datetime.now(UTC) - timedelta(days=1)).isoformat(), "status": "applied", "slug": SECOND}
        row.value = {**row.value, "last": {**row.value["last"], SECOND: stale}}

    await scheduler.catch_up_preset(AppSettings(presets=_two()))

    assert synced == [f"{SECOND} startup"]


async def test_a_preset_that_fails_to_catch_up_does_not_stop_the_next(monkeypatch):
    seen: list[str] = []

    async def sync(slug: str, trigger: str = "schedule") -> None:
        seen.append(slug)
        if slug == SLUG:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(presets, "sync", sync)

    await scheduler.catch_up_preset(AppSettings(presets=_two()))

    assert seen == [SLUG, SECOND]


async def test_the_single_record_an_older_release_kept_counts_for_the_preset_it_names(synced):
    with session_scope() as s:
        old = {"at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(), "status": "applied", "slug": SLUG}
        s.add(Setting(key=presets.STATE_KEY, value={"owned": [SLUG], "last": old}))

    await scheduler.catch_up_preset(AppSettings(presets=AUTO))

    assert synced == []  # it ran an hour ago, and its interval is twelve


async def test_a_state_with_an_unreadable_time_counts_as_overdue(synced):
    with session_scope() as s:
        s.add(Setting(key=presets.STATE_KEY, value={"last": {SLUG: {"at": "not a time"}}}))

    await scheduler.catch_up_preset(AppSettings(presets=AUTO))

    assert synced == [f"{SLUG} startup"]


async def test_nothing_runs_at_startup_unless_auto_apply_and_the_feature_are_both_on(synced):
    _state(timedelta(days=30))

    await scheduler.catch_up_preset(AppSettings(presets=_with(auto_apply=False)))
    await scheduler.catch_up_preset(AppSettings(presets=_with(enabled=False)))
    await scheduler.catch_up_preset(AppSettings())

    assert synced == []


async def test_the_startup_catch_up_waits_for_the_lock_the_scheduled_sync_takes(monkeypatch):
    _state(timedelta(days=2))
    entered: list[bool] = []

    def load_inputs(slug, draft=None):
        entered.append(True)
        raise presets.PresetDisabled("stop here")

    monkeypatch.setattr(presets, "load_inputs", load_inputs)

    async with presets._lock:
        task = asyncio.create_task(scheduler.catch_up_preset(AppSettings(presets=AUTO)))
        await asyncio.sleep(0.2)
        assert entered == []
    await asyncio.wait_for(task, 5)

    assert entered == [True]


async def test_a_failure_while_catching_up_does_not_escape(monkeypatch):
    def broken() -> dict:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(presets, "read_state", broken)

    await scheduler.catch_up_preset(AppSettings(presets=AUTO))


async def test_startup_refreshes_every_kind_and_then_catches_up_the_preset(monkeypatch):
    order: list[object] = []

    async def fake_run_job(kind: str, trigger: str = "manual", provider_ids=None) -> None:
        order.append((kind, trigger))

    async def fake_catch_up(settings: AppSettings) -> None:
        order.append("preset")

    async def fake_catalogue() -> None:
        order.append("catalogue")  # the real one calls OpenRouter when an earlier test left a key in the database

    monkeypatch.setattr(app_module, "run_job", fake_run_job)
    monkeypatch.setattr(scheduler, "catch_up_preset", fake_catch_up)
    monkeypatch.setattr(scheduler, "sync_catalogue", fake_catalogue)

    await app_module._startup_work(AppSettings(), delay=0)

    assert order == [(kind, "startup") for kind in app_module.KINDS] + ["preset", "catalogue"]


async def test_the_preset_catches_up_at_startup_even_when_the_refresh_is_off(monkeypatch):
    order: list[object] = []

    async def fake_run_job(kind: str, trigger: str = "manual", provider_ids=None) -> None:
        order.append(kind)

    async def fake_catch_up(settings: AppSettings) -> None:
        order.append("preset")

    async def fake_catalogue() -> None:
        order.append("catalogue")

    monkeypatch.setattr(app_module, "run_job", fake_run_job)
    monkeypatch.setattr(scheduler, "catch_up_preset", fake_catch_up)
    monkeypatch.setattr(scheduler, "sync_catalogue", fake_catalogue)

    await app_module._startup_work(AppSettings(refresh=RefreshSettings(refresh_on_startup=False)), delay=0)

    assert order == ["preset", "catalogue"]
