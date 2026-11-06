"""Periodic refresh jobs (APScheduler on the app's event loop)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import preset_catalog, presets
from .jobs import run_job
from .settings_store import AppSettings, PresetSettings

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

CATALOG_JOB = "preset-catalog-sync"
CATALOG_INTERVAL_SECONDS = 30 * 60


async def _scheduled(kind: str) -> None:
    await run_job(kind, "schedule")


PRESET_JOB_PREFIX = "preset-sync:"


async def _sync_preset(slug: str) -> None:
    await presets.sync(slug, "schedule")


def start(settings: AppSettings) -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
        _scheduler.start()
    reschedule(settings)
    _schedule_catalogue()


def _set_interval(job_id: str, seconds: float, func: Callable[..., Awaitable[None]], args: list[Any]) -> bool:
    """Run ``func`` every ``seconds``, leaving a job that already has that interval alone.

    Replacing a job restarts its timer from now. Every settings save comes through here, so replacing them all
    meant a job with a long interval never came due for an operator who saves more often than that.
    """
    assert _scheduler is not None
    existing = _scheduler.get_job(job_id)
    if existing is not None and getattr(existing.trigger, "interval", None) == timedelta(seconds=seconds):
        return False
    _scheduler.add_job(func, "interval", seconds=seconds, args=args, id=job_id, replace_existing=True)
    return True


def reschedule(settings: AppSettings) -> None:
    if _scheduler is None:
        return
    intervals = {
        "models": settings.refresh.models_interval_hours * 3600,
        "balances": settings.refresh.balances_interval_minutes * 60,
        "benchmarks": settings.refresh.benchmarks_interval_hours * 3600,
        "usage": settings.refresh.usage_interval_hours * 3600,
    }
    for kind, seconds in intervals.items():
        job_id = f"refresh-{kind}"
        existing = _scheduler.get_job(job_id)
        if seconds <= 0:
            if existing:
                existing.remove()
            continue
        seconds = max(seconds, 300 if kind != "balances" else 60)
        if _set_interval(job_id, seconds, _scheduled, [kind]):
            log.info("Scheduled %s refresh every %ss", kind, int(seconds))
    _schedule_preset(settings)


def _unattended(settings: AppSettings) -> list[PresetSettings]:
    """The presets that update on a schedule: the operator allowed writes and asked for them to run unattended."""
    return [item for item in settings.presets.items if item.enabled and item.auto_apply]


def _schedule_preset(settings: AppSettings) -> None:
    """One timer per preset that updates unattended, gone with the preset or the switch."""
    assert _scheduler is not None
    wanted = {f"{PRESET_JOB_PREFIX}{item.slug}": item for item in _unattended(settings)}
    for job in _scheduler.get_jobs():
        if job.id.startswith(PRESET_JOB_PREFIX) and job.id not in wanted:
            job.remove()
    for job_id, item in wanted.items():
        if _set_interval(job_id, item.interval_hours * 3600, _sync_preset, [item.slug]):
            log.info("Scheduled the update of preset %s every %sh", item.slug, item.interval_hours)


def _preset_is_due(state: dict[str, Any], interval_hours: float, slug: str) -> bool:
    """Whether the last run of ``slug`` is further back than its interval, or there is none that can be read."""
    last = ((state.get("last") or {}).get(slug) or {}).get("at")
    try:
        when = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when >= timedelta(hours=interval_hours)


async def catch_up_preset(settings: AppSettings) -> None:
    """Run each preset update that is overdue once at startup.

    A timer starts again at every restart, so on an instance that restarts more often than the interval it would
    never fire. Each takes the same lock as the scheduled run, through ``presets.sync``.
    """
    try:
        state = await asyncio.to_thread(presets.read_state)
    except Exception:  # a failed catch-up must not stop the startup work after it
        log.exception("Could not catch up the preset updates")
        return
    for item in _unattended(settings):
        try:
            if _preset_is_due(state, item.interval_hours, item.slug):
                log.info("The update of preset %s is overdue; running it now", item.slug)
                await presets.sync(item.slug, "startup")
        except Exception:  # one preset failing must not keep the others from catching up
            log.exception("Could not catch up the update of preset %s", item.slug)


async def sync_catalogue() -> None:
    """Refresh the local copy of the account's presets. Used by the timer and at startup; never raises."""
    try:
        await preset_catalog.sync()
    except Exception:  # the scheduler and the startup work after this must survive any single failure
        log.exception("Could not sync the preset catalogue")


def _schedule_catalogue() -> None:
    """The catalogue timer does not depend on any setting, so only ``start`` adds it and a save never moves it."""
    assert _scheduler is not None
    if _set_interval(CATALOG_JOB, CATALOG_INTERVAL_SECONDS, sync_catalogue, []):
        log.info("Scheduled the preset catalogue sync every %s minutes", CATALOG_INTERVAL_SECONDS // 60)


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
