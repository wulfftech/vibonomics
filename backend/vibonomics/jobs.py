"""Refresh jobs: models/prices, balances, benchmarks. Shared by the scheduler and the API."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from . import events
from .benchmarks.base import BenchContext, ScoreRecord
from .benchmarks.matching import Matcher, split_effort
from .benchmarks.registry import SOURCES
from .catalog.modelsdev import load_catalog
from .db import session_scope
from .orm import Balance, BenchmarkScore, Model, ModelAlias, PriceHistory, ProviderConfig, RefreshRun
from .provider_service import build_adapter
from .providers.base import BalanceInfo, FetchContext, ModelOffer
from .scoring import source_in_use
from .settings_store import AppSettings, load_settings

log = logging.getLogger(__name__)

PROVIDER_TIMEOUT = 300
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
KINDS = ("models", "balances", "benchmarks")


def now() -> datetime:
    return datetime.now(UTC)


_background: set[asyncio.Task[Any]] = set()


def spawn(coro: Awaitable[Any]) -> asyncio.Task[Any]:
    """Run a coroutine in the background, keeping a reference so it isn't garbage collected."""
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def is_running(kind: str) -> bool:
    return _locks[kind].locked()


async def run_job(kind: str, trigger: str = "manual", provider_ids: set[int] | None = None) -> None:
    """Run one refresh kind. ``provider_ids`` narrows models/balances to those providers."""
    jobs: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
        "models": _refresh_models,
        "balances": _refresh_balances,
        "benchmarks": _refresh_benchmarks,
    }
    job = jobs[kind]
    lock = _locks[kind]
    if lock.locked():
        return
    async with lock:
        run_id = await asyncio.to_thread(_start_run, kind, trigger)
        events.publish("refresh.started", {"kind": kind, "run_id": run_id})
        status, message, stats = "ok", None, {}
        try:
            if provider_ids is not None and kind in ("models", "balances"):
                stats = await job(run_id, provider_ids)
            else:
                stats = await job(run_id)
            failed = stats.get("failed", 0)
            if failed:
                status = "partial" if stats.get("ok", 0) else "failed"
                message = "; ".join(stats.get("errors", []))[:2000]
        except Exception as exc:  # the scheduler must survive any single failure
            log.exception("Refresh %s failed", kind)
            status, message = "failed", f"{type(exc).__name__}: {exc}"
        await asyncio.to_thread(_finish_run, run_id, status, message, stats)
        events.publish(
            "refresh.finished",
            {
                "kind": kind,
                "run_id": run_id,
                "status": status,
                "message": message,
                # Present only for a single-provider refresh, so the UI can stay quiet about it.
                **({"provider": next(iter(provider_ids))} if provider_ids and len(provider_ids) == 1 else {}),
            },
        )


def _start_run(kind: str, trigger: str) -> int:
    with session_scope() as s:
        run = RefreshRun(kind=kind, trigger=trigger, status="running")
        s.add(run)
        s.flush()
        return run.id


def _finish_run(run_id: int, status: str, message: str | None, stats: dict[str, Any]) -> None:
    with session_scope() as s:
        run = s.get(RefreshRun, run_id)
        if run:
            run.status, run.message, run.finished_at = status, message, now()
            run.stats = {k: v for k, v in stats.items() if k != "errors"}


def _load_providers_and_settings() -> tuple[list[ProviderConfig], AppSettings]:
    with session_scope() as s:
        providers = list(s.scalars(select(ProviderConfig).where(ProviderConfig.enabled.is_(True))))
        return providers, load_settings(s)


# -- models & prices -------------------------------------------------------------------------


async def _refresh_models(run_id: int, provider_ids: set[int] | None = None) -> dict[str, Any]:
    providers, settings = await asyncio.to_thread(_load_providers_and_settings)
    if provider_ids is not None:
        providers = [p for p in providers if p.id in provider_ids]
    catalog = await load_catalog(force=True)
    ctx = FetchContext(catalog=catalog, settings=settings)
    stats: dict[str, Any] = {"ok": 0, "failed": 0, "errors": [], "models": 0}

    async def one(cfg: ProviderConfig) -> None:
        adapter = build_adapter(cfg, ctx)
        if adapter is None:
            return
        events.publish("refresh.progress", {"kind": "models", "provider": cfg.name})
        try:
            offers = await asyncio.wait_for(adapter.fetch_models(), PROVIDER_TIMEOUT)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}" if not str(exc).startswith(("GET", "POST")) else str(exc)
            log.warning("Models refresh failed for %s: %s", cfg.name, msg)
            stats["failed"] += 1
            stats["errors"].append(f"{cfg.name}: {msg[:300]}")
            await asyncio.to_thread(_mark_provider, cfg.id, "error", msg[:1000])
            return
        count = await asyncio.to_thread(persist_offers, cfg.id, offers)
        stats["ok"] += 1
        stats["models"] += count
        await asyncio.to_thread(_mark_provider, cfg.id, "ok", f"{count} models")

    await asyncio.gather(*(one(p) for p in providers))
    return stats


def _mark_provider(provider_id: int, status: str, message: str) -> None:
    with session_scope() as s:
        cfg = s.get(ProviderConfig, provider_id)
        if cfg:
            cfg.last_refresh_at, cfg.last_refresh_status, cfg.last_refresh_message = now(), status, message


def _total(i: float | None, o: float | None) -> float | None:
    if i is None or o is None:
        return None
    return i + o


def persist_offers(provider_id: int, offers: list[ModelOffer]) -> int:
    """Upsert offers for one provider and record price/discount changes."""
    ts = now()
    with session_scope() as s:
        existing = {m.model_id: m for m in s.scalars(select(Model).where(Model.provider_config_id == provider_id))}
        first_refresh = not existing
        seen: set[str] = set()
        for offer in offers:
            if not offer.model_id or offer.model_id in seen:
                continue
            seen.add(offer.model_id)
            row = existing.get(offer.model_id)
            if row is None:
                row = Model(
                    provider_config_id=provider_id,
                    model_id=offer.model_id,
                    first_seen=ts,
                    baseline=first_refresh,
                    name=offer.name,
                )
                s.add(row)
                old_total, old_discount = None, None
            else:
                old_total, old_discount = _total(row.price_input, row.price_output), row.discount or 0

            new_total = _total(offer.price.input, offer.price.output)
            new_discount = offer.discount or 0
            row.name = offer.name
            row.creator = offer.creator
            row.canonical_id = offer.canonical_id
            row.family = offer.family
            row.context_length = offer.context_length
            row.max_output = offer.max_output
            row.input_modalities = offer.input_modalities
            row.output_modalities = offer.output_modalities
            row.reasoning = offer.reasoning
            row.tool_call = offer.tool_call
            row.open_weights = offer.open_weights
            row.release_date = offer.release_date
            row.price_input = offer.price.input
            row.price_output = offer.price.output
            row.price_cache_read = offer.price.cache_read
            row.price_cache_write = offer.price.cache_write
            row.list_input = offer.list_price.input if offer.list_price else None
            row.list_output = offer.list_price.output if offer.list_price else None
            row.discount = offer.discount
            row.discount_note = offer.discount_note
            row.discount_until = offer.discount_until
            row.is_free = offer.is_free
            row.pricing_source = offer.pricing_source
            row.extra = offer.extra
            row.last_seen = ts
            row.active = True

            if new_discount > 0 and not (old_discount or 0) > 0:
                row.discount_since = ts if old_total is not None or not row.baseline else None
            elif new_discount <= 0:
                row.discount_since = None
            if old_total is not None and new_total is not None:
                if new_total < old_total * 0.99 and new_discount <= (old_discount or 0):
                    row.price_drop_since = ts
                elif new_total > old_total * 1.01:
                    row.price_drop_since = None

            changed = old_total is None or new_total != old_total or new_discount != (old_discount or 0)
            if changed:
                s.flush()
                s.add(
                    PriceHistory(
                        model_pk=row.id,
                        observed_at=ts,
                        price_input=offer.price.input,
                        price_output=offer.price.output,
                        price_cache_read=offer.price.cache_read,
                        discount=offer.discount,
                    )
                )
        for mid, row in existing.items():
            if mid not in seen:
                row.active = False
        return len(seen)


# -- balances --------------------------------------------------------------------------------


async def _refresh_balances(run_id: int, provider_ids: set[int] | None = None) -> dict[str, Any]:
    providers, settings = await asyncio.to_thread(_load_providers_and_settings)
    if provider_ids is not None:
        providers = [p for p in providers if p.id in provider_ids]
    catalog = await load_catalog()
    ctx = FetchContext(catalog=catalog, settings=settings)
    stats: dict[str, Any] = {"ok": 0, "failed": 0, "errors": []}

    async def one(cfg: ProviderConfig) -> None:
        adapter = build_adapter(cfg, ctx)
        if adapter is None:
            return
        events.publish("refresh.progress", {"kind": "balances", "provider": cfg.name})
        try:
            info = await asyncio.wait_for(adapter.fetch_balance(), 60)
        except Exception as exc:
            info = BalanceInfo(status="error", message=str(exc)[:500])
        if info.status == "error":
            stats["failed"] += 1
            stats["errors"].append(f"{cfg.name}: {info.message}")
        else:
            stats["ok"] += 1
        await asyncio.to_thread(_persist_balance, cfg.id, info)

    await asyncio.gather(*(one(p) for p in providers))
    return stats


def _persist_balance(provider_id: int, info: BalanceInfo) -> None:
    with session_scope() as s:
        row = s.get(Balance, provider_id)
        if row is None:
            row = Balance(provider_config_id=provider_id, status=info.status)
            s.add(row)
        row.fetched_at = now()
        row.status = info.status
        row.account_balance = info.account_balance
        row.currency = info.currency
        row.account_total = info.account_total
        row.account_used = info.account_used
        row.key_usage = info.key_usage
        row.key_limit = info.key_limit
        row.key_remaining = info.key_remaining
        row.key_label = info.key_label
        row.message = info.message
        row.details = info.details


# -- benchmarks ------------------------------------------------------------------------------


async def _refresh_benchmarks(run_id: int) -> dict[str, Any]:
    _providers, settings = await asyncio.to_thread(_load_providers_and_settings)
    catalog = await load_catalog()
    operator_aliases = await asyncio.to_thread(_load_operator_aliases)
    matcher = Matcher(catalog, operator_aliases)
    stats: dict[str, Any] = {"ok": 0, "failed": 0, "errors": [], "records": 0, "matched": 0}

    async def one(source_id: str) -> None:
        cls = SOURCES[source_id]
        if not source_in_use(settings, source_id, cls.spec):
            return
        key = settings.benchmark_key(source_id)
        if cls.spec.requires_key and not key:
            return
        source = cls(BenchContext(catalog=catalog, settings=settings, api_key=key))
        events.publish("refresh.progress", {"kind": "benchmarks", "provider": cls.spec.name})
        try:
            records = await asyncio.wait_for(source.fetch(), PROVIDER_TIMEOUT)
        except Exception as exc:
            log.warning("Benchmark source %s failed: %s", source_id, exc)
            stats["failed"] += 1
            stats["errors"].append(f"{cls.spec.name}: {str(exc)[:300]}")
            return
        matched = await asyncio.to_thread(persist_scores, source_id, records, matcher)
        stats["ok"] += 1
        stats["records"] += len(records)
        stats["matched"] += matched

    await asyncio.gather(*(one(sid) for sid in SOURCES))
    return stats


def _load_operator_aliases() -> dict[tuple[str, str], str | None]:
    with session_scope() as s:
        return {(a.source, a.source_model): a.canonical_id for a in s.scalars(select(ModelAlias))}


def _percentiles(values: list[float], higher_is_better: bool) -> list[float]:
    n = len(values)
    if n == 1:
        return [50.0]
    order = sorted(values)
    result = []
    for v in values:
        below = sum(1 for x in order if x < v)
        equal = sum(1 for x in order if x == v)
        rank = below + (equal - 1) / 2
        pct = 100.0 * rank / (n - 1)
        result.append(round(pct if higher_is_better else 100.0 - pct, 2))
    return result


def persist_scores(source_id: str, records: list[ScoreRecord], matcher: Matcher) -> int:
    cls = SOURCES[source_id]
    ts = now()
    rows: dict[tuple[str, str, str], BenchmarkScore] = {}
    metrics = {m.id: m for m in cls.spec.metrics}
    for rec in records:
        metric = metrics.get(rec.metric)
        if metric is None:
            continue
        base_name, parsed_effort = split_effort(rec.source_model)
        effort = rec.effort or parsed_effort
        canonical, method = matcher.match(source_id, rec.source_model, [*rec.model_ids, base_name])
        key = (rec.metric, rec.source_model, effort)
        rows[key] = BenchmarkScore(
            source=source_id,
            metric=rec.metric,
            dimension=metric.dimension,
            source_model=rec.source_model,
            effort=effort,
            canonical_id=canonical,
            match_method=method,
            value=rec.value,
            higher_is_better=metric.higher_is_better,
            cost_per_task=rec.cost_per_task,
            tokens_per_task=rec.tokens_per_task,
            as_of=rec.as_of,
            url=rec.url,
            extra={**rec.extra, "model_ids": [i for i in rec.model_ids if i]},
            fetched_at=ts,
        )
    by_metric: dict[str, list[BenchmarkScore]] = defaultdict(list)
    for row in rows.values():
        by_metric[row.metric].append(row)
    for metric_rows in by_metric.values():
        pcts = _percentiles([r.value for r in metric_rows], metric_rows[0].higher_is_better)
        for row, pct in zip(metric_rows, pcts, strict=True):
            row.percentile = pct
    with session_scope() as s:
        s.execute(delete(BenchmarkScore).where(BenchmarkScore.source == source_id))
        s.add_all(rows.values())
    return sum(1 for r in rows.values() if r.canonical_id)


async def rematch_scores() -> int:
    """Re-run matching for stored scores (after alias edits) without refetching."""
    catalog = await load_catalog()
    operator_aliases = await asyncio.to_thread(_load_operator_aliases)
    matcher = Matcher(catalog, operator_aliases)

    def _apply() -> int:
        matched = 0
        with session_scope() as s:
            for row in s.scalars(select(BenchmarkScore)):
                base_name, _ = split_effort(row.source_model)
                hints = [*(row.extra or {}).get("model_ids", []), base_name]
                cid, method = matcher.match(row.source, row.source_model, hints)
                row.canonical_id, row.match_method = cid, method
                matched += 1 if cid else 0
        return matched

    return await asyncio.to_thread(_apply)
