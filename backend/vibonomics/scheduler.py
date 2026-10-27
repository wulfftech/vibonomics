"""Periodic refresh jobs (APScheduler on the app's event loop)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .jobs import run_job
from .settings_store import AppSettings

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _scheduled(kind: str) -> None:
    await run_job(kind, "schedule")


def start(settings: AppSettings) -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
        _scheduler.start()
    reschedule(settings)


def reschedule(settings: AppSettings) -> None:
    if _scheduler is None:
        return
    intervals = {
        "models": settings.refresh.models_interval_hours * 3600,
        "balances": settings.refresh.balances_interval_minutes * 60,
        "benchmarks": settings.refresh.benchmarks_interval_hours * 3600,
    }
    for kind, seconds in intervals.items():
        job_id = f"refresh-{kind}"
        existing = _scheduler.get_job(job_id)
        if seconds <= 0:
            if existing:
                existing.remove()
            continue
        seconds = max(seconds, 300 if kind != "balances" else 60)
        _scheduler.add_job(_scheduled, "interval", seconds=seconds, args=[kind], id=job_id, replace_existing=True)
        log.info("Scheduled %s refresh every %ss", kind, int(seconds))


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
