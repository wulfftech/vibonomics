"""Refresh jobs: models/prices, balances, benchmarks. Shared by the scheduler and the API."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, bindparam, delete, false, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import events, usage
from .benchmarks.base import BenchContext, ScoreRecord
from .benchmarks.matching import Matcher
from .benchmarks.registry import SOURCES
from .catalog.modelsdev import ModelsDevCatalog, load_catalog
from .db import session_scope
from .http import FetchError
from .orm import Balance, BenchmarkScore, Model, ModelAlias, PriceHistory, ProviderConfig, RefreshRun
from .provider_service import build_adapter, resolve
from .providers.base import BalanceInfo, FetchContext, ModelOffer
from .scoring import source_in_use
from .settings_store import AppSettings, load_settings

log = logging.getLogger(__name__)

PROVIDER_TIMEOUT = 300
BALANCE_TIMEOUT = 60
# About 55 rows a day, so this keeps the table near 5,000 rows.
RUN_RETENTION_DAYS = 90
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
KINDS = ("models", "balances", "benchmarks", "usage")
_PROVIDER_KINDS = ("models", "balances")
_dropped: dict[str, set[int]] = defaultdict(set)


def now() -> datetime:
    return datetime.now(UTC)


def failure_text(exc: BaseException, timeout: float | None = None) -> str:
    """What to show for a failed fetch or write.

    ``str(exc)`` is empty for a timeout, which used to leave a run message ending in "Slow: ". A database
    error carries the statement and its parameters, so only the driver's own message is kept.
    """
    if isinstance(exc, SQLAlchemyError):
        return str(getattr(exc, "orig", None) or type(exc).__name__)
    text = str(exc)
    if text:
        return text
    if isinstance(exc, TimeoutError):
        return f"timed out after {timeout:g}s" if timeout else "timed out"
    return type(exc).__name__


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
    """Run one refresh kind. ``provider_ids`` narrows models/balances to those providers.

    A call that finds its kind already running is dropped, not queued, except a provider-scoped one: the run in
    flight read the provider list before the save that asked for it, so those ids are kept and run once, together,
    when the lock frees.
    """
    if _locks[kind].locked():
        if provider_ids and kind in _PROVIDER_KINDS:
            _dropped[kind] |= provider_ids
        return
    await _run_locked(kind, trigger, provider_ids)
    while pending := _dropped.pop(kind, None):
        await _run_locked(kind, "provider", pending)


async def _run_locked(kind: str, trigger: str, provider_ids: set[int] | None) -> None:
    jobs: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
        "models": _refresh_models,
        "balances": _refresh_balances,
        "benchmarks": _refresh_benchmarks,
        "usage": _refresh_usage,
    }
    job = jobs[kind]
    async with _locks[kind]:
        run_id: int | None = None
        status, message, stats = "ok", None, {}
        try:
            try:
                # Inside the try: a database error here used to escape and abort the caller's remaining kinds.
                run_id = await asyncio.to_thread(_start_run, kind, trigger)
                events.publish("refresh.started", {"kind": kind, "run_id": run_id})
                if provider_ids is not None and kind in ("models", "balances"):
                    stats = await job(run_id, provider_ids)
                else:
                    stats = await job(run_id)
                failed = stats.get("failed", 0)
                if failed:
                    status = "partial" if stats.get("ok", 0) else "failed"
                    message = "; ".join(stats.get("errors", []))[:2000]
                elif stats.get("partial", 0):
                    # Every source answered, but at least one left part of its data out.
                    status = "partial"
                    message = "; ".join(stats.get("errors", []))[:2000]
            except Exception as exc:  # the scheduler must survive any single failure
                log.exception("Refresh %s failed", kind)
                status, message = "failed", f"{type(exc).__name__}: {failure_text(exc)}"
            if run_id is not None:
                try:
                    await asyncio.to_thread(_finish_run, run_id, status, message, stats)
                except Exception:
                    # The row stays "running" until the next start marks it failed.
                    log.exception("Could not record the end of refresh run %s (%s)", run_id, kind)
        finally:
            # The UI's spinner for this kind stops on this event, so it goes out whatever happened above.
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


def _prune_runs(s: Session) -> None:
    """Drop run rows past the retention window, apart from the ones other screens still read.

    Those are each kind's latest run (the status page) and its latest good one (the "scores as of" time on the
    value route), which can both be older than the window for a kind that no longer runs.
    """
    latest = select(func.max(RefreshRun.id)).group_by(RefreshRun.kind)
    latest_good = (
        select(func.max(RefreshRun.id)).where(RefreshRun.status.in_(("ok", "partial"))).group_by(RefreshRun.kind)
    )
    s.execute(
        delete(RefreshRun).where(
            RefreshRun.started_at < now() - timedelta(days=RUN_RETENTION_DAYS),
            RefreshRun.status != "running",
            RefreshRun.id.not_in(latest),
            RefreshRun.id.not_in(latest_good),
        )
    )


def _start_run(kind: str, trigger: str) -> int:
    with session_scope() as s:
        _prune_runs(s)
        run = RefreshRun(kind=kind, trigger=trigger, status="running")
        s.add(run)
        s.flush()
        return run.id


def recover_interrupted_runs() -> None:
    """A run still marked running when the app starts was cut off by a restart and can never finish."""
    with session_scope() as s:
        for run in s.scalars(select(RefreshRun).where(RefreshRun.status == "running")):
            run.status, run.message, run.finished_at = "failed", "Interrupted by a Vibonomics restart.", now()


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
    degraded = bool(catalog.degraded)
    if degraded:
        # Providers that need no catalogue still refresh, but their offers carry none of its data.
        stats["partial"] = 1
        stats["errors"].append(
            f"models.dev is unavailable ({str(catalog.degraded)[:200]}): the canonical ids, families and release "
            "dates already stored were kept"
        )

    async def one(cfg: ProviderConfig) -> None:
        adapter = build_adapter(cfg, ctx)
        if adapter is None:
            return
        events.publish("refresh.progress", {"kind": "models", "provider": cfg.name})
        try:
            offers = await asyncio.wait_for(adapter.fetch_models(), PROVIDER_TIMEOUT)
            # Inside the try: a provider deleted mid-refresh fails here, and must not take the others down.
            count = await asyncio.to_thread(persist_offers, cfg.id, offers, keep_catalogue_fields=degraded)
        except Exception as exc:
            text = failure_text(exc, PROVIDER_TIMEOUT)
            msg = text if text.startswith(("GET", "POST")) else f"{type(exc).__name__}: {text}"
            log.warning("Models refresh failed for %s: %s", cfg.name, msg)
            stats["failed"] += 1
            stats["errors"].append(f"{cfg.name}: {msg[:300]}")
            await asyncio.to_thread(_mark_provider, cfg.id, "error", msg[:1000])
            return
        stats["ok"] += 1
        stats["models"] += count
        await asyncio.to_thread(_mark_provider, cfg.id, "ok", f"{count} models")

    # Wait for every provider even if one task dies, so none is still writing once the job lock is released.
    results = await asyncio.gather(*(one(p) for p in providers), return_exceptions=True)
    _record_crashes(results, [p.name for p in providers], stats)
    return stats


def _record_crashes(results: list[Any], names: list[str], stats: dict[str, Any]) -> None:
    """Count the tasks that raised out of their own error handling, so a run reports them as failures."""
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            log.error("Refresh task for %s crashed", name, exc_info=result)
            stats["failed"] += 1
            stats["errors"].append(f"{name}: {failure_text(result)[:300]}")


def _mark_provider(provider_id: int, status: str, message: str) -> None:
    with session_scope() as s:
        cfg = s.get(ProviderConfig, provider_id)
        if cfg:
            cfg.last_refresh_at, cfg.last_refresh_status, cfg.last_refresh_message = now(), status, message


def _total(i: float | None, o: float | None) -> float | None:
    if i is None or o is None:
        return None
    return i + o


_CATALOGUE_COLUMNS = ("canonical_id", "creator", "family", "release_date", "open_weights")


def _with_stored_catalogue_fields(offer: ModelOffer, row: Model) -> ModelOffer:
    """``offer`` with the columns models.dev supplies taken from ``row`` wherever it has a value.

    Built while models.dev is down, an offer holds no canonical id, family or open-weights flag and only fallbacks
    for the creator and the release date. Storing it would blank what the last good refresh found.
    """
    kept = {name: getattr(row, name) for name in _CATALOGUE_COLUMNS if getattr(row, name) is not None}
    return replace(offer, **kept)


def persist_offers(provider_id: int, offers: list[ModelOffer], *, keep_catalogue_fields: bool = False) -> int:
    """Upsert offers for one provider and record price/discount changes.

    ``keep_catalogue_fields`` is for a refresh made without the models.dev catalogue: the stored canonical id,
    creator, family, release date and open-weights flag are kept instead of being replaced by the offer's empty ones.
    """
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
            if row is not None and keep_catalogue_fields:
                offer = _with_stored_catalogue_fields(offer, row)
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
                priced_as_before = False
            else:
                old_total, old_discount = _total(row.price_input, row.price_output), row.discount or 0
                priced_as_before = (row.price_input, row.price_output) == (offer.price.input, offer.price.output)

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

            # A model with no price has no total to compare, which used to count as a change on every refresh.
            changed = not priced_as_before or new_discount != (old_discount or 0)
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
            info = await asyncio.wait_for(adapter.fetch_balance(), BALANCE_TIMEOUT)
        except Exception as exc:
            info = BalanceInfo(status="error", message=failure_text(exc, BALANCE_TIMEOUT)[:500])
        try:
            await asyncio.to_thread(_persist_balance, cfg.id, info)
        except Exception as exc:  # a provider deleted mid-refresh, or a locked database
            log.warning("Balance for %s could not be saved: %s", cfg.name, failure_text(exc))
            info = BalanceInfo(status="error", message=f"could not save: {failure_text(exc)}"[:500])
        if info.status == "error":
            stats["failed"] += 1
            stats["errors"].append(f"{cfg.name}: {info.message}")
        else:
            stats["ok"] += 1

    results = await asyncio.gather(*(one(p) for p in providers), return_exceptions=True)
    _record_crashes(results, [p.name for p in providers], stats)
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


def purge_unused_benchmark_rows(settings: AppSettings) -> int:
    """Delete the stored scores of every source that is off, or that needs terms the operator has not acknowledged.

    Nothing refreshes such a source again, so its rows would otherwise stay for good. A failure is logged, not raised:
    the routes already hide those rows, and the next refresh tries again.
    """
    unused = [sid for sid, cls in SOURCES.items() if not source_in_use(settings, sid, cls.spec)]
    if not unused:
        return 0
    try:
        with session_scope() as s:
            return s.execute(delete(BenchmarkScore).where(BenchmarkScore.source.in_(unused))).rowcount or 0
    except SQLAlchemyError as exc:
        log.warning("Could not delete the scores of sources that are off: %s", failure_text(exc))
        return 0


async def _refresh_benchmarks(run_id: int) -> dict[str, Any]:
    _providers, settings = await asyncio.to_thread(_load_providers_and_settings)
    # A source turned off while a refresh was storing it leaves rows behind that nothing else would remove.
    await asyncio.to_thread(purge_unused_benchmark_rows, settings)
    catalog = await load_catalog()
    if catalog.degraded:
        # Against an empty catalogue every score would match nothing, and storing them would replace the
        # matched rows with unmatched ones.
        message = f"{_no_catalogue(catalog)}, so the benchmark scores cannot be matched; the stored ones were kept"
        return {"ok": 0, "failed": 1, "partial": 0, "errors": [message], "records": 0, "matched": 0}
    # The aliases are read again when each source is stored: a refresh can run for minutes, and an alias set
    # meanwhile must not be overwritten by a matcher built before it existed.
    matcher = Matcher(catalog, await asyncio.to_thread(_load_operator_aliases))
    stats: dict[str, Any] = {"ok": 0, "failed": 0, "partial": 0, "errors": [], "records": 0, "matched": 0}

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
            # A source that skipped part of its data keeps the stored rows of what it skipped.
            matched = await asyncio.to_thread(
                persist_scores, source_id, records, matcher, keep_missing_metrics=bool(source.partial_errors)
            )
        except Exception as exc:
            text = failure_text(exc, PROVIDER_TIMEOUT)
            log.warning("Benchmark source %s failed: %s", source_id, text)
            stats["failed"] += 1
            stats["errors"].append(f"{cls.spec.name}: {text[:300]}")
            return
        stats["ok"] += 1
        stats["records"] += len(records)
        stats["matched"] += matched
        if source.partial_errors:
            stats["partial"] += 1
            missing = "; ".join(source.partial_errors)[:250]
            stats["errors"].append(f"{cls.spec.name}: partial, kept the stored scores for what was missing ({missing})")

    # Wait for every source, even after one fails, so none is still writing once the job lock is released.
    results = await asyncio.gather(*(one(sid) for sid in SOURCES), return_exceptions=True)
    for sid, result in zip(SOURCES, results, strict=True):
        if isinstance(result, BaseException):
            log.error("Benchmark source %s failed", sid, exc_info=result)
            stats["failed"] += 1
            stats["errors"].append(f"{SOURCES[sid].spec.name}: {failure_text(result, PROVIDER_TIMEOUT)[:300]}")
    return stats


# -- spend history ---------------------------------------------------------------------------


async def _refresh_usage(run_id: int) -> dict[str, Any]:
    """Each OpenRouter account's spend history, for the accounts that have a management key."""
    providers, _settings = await asyncio.to_thread(_load_providers_and_settings)
    stats: dict[str, Any] = {"ok": 0, "failed": 0, "partial": 0, "errors": [], "rows": 0, "skipped": 0}

    async def one(cfg: ProviderConfig) -> None:
        resolved = resolve(cfg)
        if not resolved.mgmt_key:
            stats["skipped"] += 1  # a normal key cannot read the activity API
            return
        events.publish("refresh.progress", {"kind": "usage", "provider": cfg.name})
        try:
            base = (resolved.base_url or "https://openrouter.ai/api/v1").rstrip("/")
            account = await asyncio.wait_for(usage.fetch_account(base, resolved.mgmt_key), PROVIDER_TIMEOUT)
            stats["rows"] += await asyncio.to_thread(usage.persist, cfg.id, account)
        except Exception as exc:
            text = failure_text(exc, PROVIDER_TIMEOUT)
            msg = text if text.startswith(("GET", "POST")) else f"{type(exc).__name__}: {text}"
            log.warning("Usage refresh failed for %s: %s", cfg.name, msg)
            stats["failed"] += 1
            stats["errors"].append(f"{cfg.name}: {msg[:300]}")
            return
        stats["ok"] += 1
        if account.errors:
            stats["partial"] += 1
            stats["errors"].append(f"{cfg.name}: {'; '.join(account.errors)[:300]}")

    targets = [p for p in providers if p.type == "openrouter"]
    results = await asyncio.gather(*(one(p) for p in targets), return_exceptions=True)
    _record_crashes(results, [p.name for p in targets], stats)
    return stats


def _no_catalogue(catalog: ModelsDevCatalog) -> str:
    return f"models.dev is unavailable and there is no earlier copy ({str(catalog.degraded)[:200]})"


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


def persist_scores(
    source_id: str, records: list[ScoreRecord], matcher: Matcher, *, keep_missing_metrics: bool = False
) -> int:
    """Replace a source's stored scores with ``records`` and return how many matched a model.

    With ``keep_missing_metrics`` only the metrics present in ``records`` are replaced: a source that could
    not fetch one of its leaderboards must not wipe the stored scores for it.
    """
    cls = SOURCES[source_id]
    ts = now()
    rows: dict[tuple[str, str, str], BenchmarkScore] = {}
    metrics = {m.id: m for m in cls.spec.metrics}
    present = {rec.metric for rec in records if rec.metric in metrics and math.isfinite(rec.value)}
    skipped = 0
    with session_scope() as s:
        # The delete comes first so this transaction holds the write lock from the start. An alias saved while
        # the source is stored is then either committed before the aliases are read below, or waits and is
        # applied by its own rematch to the rows stored here; it cannot fall between the two.
        stmt = delete(BenchmarkScore).where(BenchmarkScore.source == source_id)
        if keep_missing_metrics:
            stmt = stmt.where(BenchmarkScore.metric.in_(present))
        s.execute(stmt)
        matcher.operator_aliases = {(a.source, a.source_model): a.canonical_id for a in s.scalars(select(ModelAlias))}
        for rec in records:
            metric = metrics.get(rec.metric)
            if metric is None:
                continue
            if not math.isfinite(rec.value):  # SQLite stores NaN as NULL, which the NOT NULL column rejects
                skipped += 1
                continue
            effort = rec.effort or matcher.effort(rec.source_model)
            canonical, method = matcher.match(source_id, rec.source_model, rec.model_ids, rec.fallback_ids)
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
                extra={
                    **rec.extra,
                    "model_ids": [i for i in rec.model_ids if i],
                    "fallback_ids": [i for i in rec.fallback_ids if i],
                },
                fetched_at=ts,
            )
        if skipped:
            log.warning("Benchmark source %s: skipped %d non-finite values", source_id, skipped)
        by_metric: dict[str, list[BenchmarkScore]] = defaultdict(list)
        for row in rows.values():
            by_metric[row.metric].append(row)
        for metric_rows in by_metric.values():
            pcts = _percentiles([r.value for r in metric_rows], metric_rows[0].higher_is_better)
            for row, pct in zip(metric_rows, pcts, strict=True):
                row.percentile = pct
        s.add_all(rows.values())
    return sum(1 for r in rows.values() if r.canonical_id)


async def rematch_scores() -> int:
    """Re-run matching for stored scores (after alias edits) without refetching.

    Each update is addressed by the row's natural key, not its id: a refresh can replace a source's rows
    between the read and the write, and SQLite reuses ids, so an update by id could land on another row.
    """
    catalog = await load_catalog()
    if catalog.degraded:
        raise FetchError(f"{_no_catalogue(catalog)}, so the stored scores cannot be rematched")
    matcher = Matcher(catalog)
    table = BenchmarkScore.__table__
    statement = (
        update(table)
        .where(
            and_(
                table.c.source == bindparam("key_source"),
                table.c.metric == bindparam("key_metric"),
                table.c.source_model == bindparam("key_model"),
                table.c.effort == bindparam("key_effort"),
            )
        )
        .values(canonical_id=bindparam("new_canonical"), match_method=bindparam("new_method"))
    )

    def _apply() -> int:
        matched = 0
        with session_scope() as s:
            # Takes the write lock before the aliases are read, as persist_scores does with its delete: an alias
            # saved after this waits and is applied by its own rematch. Read earlier, two quick saves could commit
            # out of order and the older rematch would revert the newer mapping.
            s.execute(update(table).where(false()).values(canonical_id=table.c.canonical_id))
            matcher.operator_aliases = {
                (a.source, a.source_model): a.canonical_id for a in s.scalars(select(ModelAlias))
            }
            stored = s.execute(
                select(table.c.source, table.c.metric, table.c.source_model, table.c.effort, table.c.extra)
            ).all()
            changes = []
            for source, metric, source_model, effort, extra in stored:
                extra = extra or {}
                cid, method = matcher.match(
                    source, source_model, extra.get("model_ids", []), extra.get("fallback_ids", [])
                )
                matched += 1 if cid else 0
                changes.append(
                    {
                        "key_source": source,
                        "key_metric": metric,
                        "key_model": source_model,
                        "key_effort": effort,
                        "new_canonical": cid,
                        "new_method": method,
                    }
                )
            if changes:
                s.execute(statement, changes)
        return matched

    return await asyncio.to_thread(_apply)
