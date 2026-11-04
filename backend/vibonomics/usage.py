"""Spend history for OpenRouter accounts: fetched from the activity API, kept day by day, summarised for the page.

OpenRouter's ``GET /activity`` returns the last 30 completed UTC days, one row per day, model and upstream provider,
and needs a management key. That window is all it will ever show, so every day it reports is stored here
(``UsageDay``) and the history grows past it. Each fetch replaces the days it returns, so a figure OpenRouter later
revises is corrected. The current UTC day is not in the activity API; ``GET /keys`` reports it per key
(``usage_daily``), which is where "today so far" comes from.

An account (one OpenRouter provider in Vibonomics, with its own management key) is fetched whole, and then once per
API key that was used lately, so the page can split the spend by key. A person with many keys, or with several
OpenRouter accounts configured, sees them separately or together. Spend no listed key accounts for (a key in another
workspace, or one since deleted) is shown as the remainder rather than dropped.

Token counts: completion tokens include the reasoning tokens (no row reports more reasoning than completion), and
cached tokens are part of the prompt tokens. So tokens processed are prompt plus completion.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, false, func, select
from sqlalchemy.orm import Session

from .db import session_scope
from .http import FetchError, get_json
from .orm import ProviderConfig, UsageDay, UsageKey, utcnow
from .provider_service import resolve

log = logging.getLogger(__name__)

RECENT_KEY_DAYS = 31
KEY_PAGES = 10
KEY_CONCURRENCY = 4
MODELS_SHOWN = 30
UPSTREAMS_SHOWN = 15


# ---- fetching ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    date: str
    model: str
    upstream: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    usage: float
    byok_usage: float


@dataclass
class KeyInfo:
    hash: str
    name: str
    disabled: bool
    last_used_at: datetime | None
    usage_total: float
    usage_today: float
    usage_month: float


@dataclass
class Account:
    keys: list[KeyInfo] = field(default_factory=list)
    aggregate: list[Row] = field(default_factory=list)
    per_key: dict[str, list[Row]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _num(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_rows(data: Any) -> list[Row]:
    """Activity rows, one per day, model and upstream provider; endpoints of one provider are summed."""
    merged: dict[tuple[str, str, str], list[float]] = {}
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, dict):
            continue
        day = str(raw.get("date") or "")[:10]  # "2026-11-03 00:00:00"
        model = str(raw.get("model") or raw.get("model_permaslug") or "")
        if len(day) != 10 or not model:
            continue
        slot = merged.setdefault((day, model, str(raw.get("provider_name") or "")), [0.0] * 7)
        for i, field_name in enumerate(
            (
                "requests",
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "cached_tokens",
                "usage",
                "byok_usage_inference",
            )
        ):
            slot[i] += _num(raw.get(field_name))
    return [
        Row(day, model, upstream, int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4]), v[5], v[6])
        for (day, model, upstream), v in sorted(merged.items())
    ]


async def _list_keys(base_url: str, headers: dict[str, str]) -> list[KeyInfo]:
    keys: dict[str, KeyInfo] = {}
    offset = 0
    for _ in range(KEY_PAGES):
        params = {"include_disabled": "true", "offset": offset}
        page = (await get_json(f"{base_url}/keys", headers=headers, params=params, timeout=30)).get("data") or []
        fresh = 0
        for k in page:
            digest = k.get("hash") if isinstance(k, dict) else None
            if not isinstance(digest, str) or not digest or digest in keys:
                continue
            fresh += 1
            keys[digest] = KeyInfo(
                hash=digest,
                name=str(k.get("name") or k.get("label") or "")[:200],
                disabled=bool(k.get("disabled")),
                last_used_at=_time(k.get("last_used_at")),
                usage_total=_num(k.get("usage")),
                usage_today=_num(k.get("usage_daily")),
                usage_month=_num(k.get("usage_monthly")),
            )
        if not fresh:  # the end, or a server that ignores the offset
            break
        offset += len(page)
    return list(keys.values())


async def fetch_account(base_url: str, mgmt_key: str) -> Account:
    """The account's activity, and each recently used key's. A key that fails is skipped and reported."""
    headers = {"Authorization": f"Bearer {mgmt_key}"}
    account = Account()
    # The whole account first: when this fails there is nothing worth storing.
    activity = await get_json(f"{base_url}/activity", headers=headers, timeout=60)
    account.aggregate = parse_rows(activity.get("data"))
    try:
        account.keys = await _list_keys(base_url, headers)
    except FetchError as exc:
        account.errors.append(f"API keys: {exc}")
        return account

    cutoff = utcnow() - timedelta(days=RECENT_KEY_DAYS)
    gate = asyncio.Semaphore(KEY_CONCURRENCY)

    async def one(key: KeyInfo) -> None:
        async with gate:
            try:
                data = await get_json(
                    f"{base_url}/activity", headers=headers, params={"api_key_hash": key.hash}, timeout=60
                )
            except FetchError as exc:
                account.errors.append(f"key {key.name or key.hash[:8]}: {exc}")
                return
        account.per_key[key.hash] = parse_rows(data.get("data"))

    await asyncio.gather(*(one(k) for k in account.keys if k.last_used_at and k.last_used_at >= cutoff))
    return account


# ---- storing -------------------------------------------------------------------------------------------------


def _replace_days(session: Session, provider_id: int, key_hash: str, rows: list[Row], now: datetime) -> None:
    days = {r.date for r in rows}
    if not days:
        return
    session.execute(
        delete(UsageDay).where(
            UsageDay.provider_config_id == provider_id, UsageDay.key_hash == key_hash, UsageDay.date.in_(days)
        )
    )
    session.add_all(
        UsageDay(
            provider_config_id=provider_id,
            key_hash=key_hash,
            date=r.date,
            model=r.model,
            upstream=r.upstream,
            requests=r.requests,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            reasoning_tokens=r.reasoning_tokens,
            cached_tokens=r.cached_tokens,
            usage=r.usage,
            byok_usage=r.byok_usage,
            fetched_at=now,
        )
        for r in rows
    )


def persist(provider_id: int, account: Account) -> int:
    """Store what a fetch returned. Days the fetch did not return are left as they were."""
    now = utcnow()
    count = len(account.aggregate) + sum(len(rows) for rows in account.per_key.values())
    with session_scope() as s:
        _replace_days(s, provider_id, "", account.aggregate, now)
        for digest, rows in account.per_key.items():
            _replace_days(s, provider_id, digest, rows, now)
        known = {k.key_hash: k for k in s.scalars(select(UsageKey).where(UsageKey.provider_config_id == provider_id))}
        for info in account.keys:
            row = known.get(info.hash)
            if row is None:
                row = UsageKey(provider_config_id=provider_id, key_hash=info.hash)
                s.add(row)
            row.name, row.disabled, row.last_used_at = info.name, info.disabled, info.last_used_at
            row.usage_total, row.usage_today, row.usage_month, row.fetched_at = (
                info.usage_total,
                info.usage_today,
                info.usage_month,
                now,
            )
    return count


# ---- summarising ---------------------------------------------------------------------------------------------


@dataclass
class Bucket:
    cost: float = 0.0
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    byok: float = 0.0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_per_mtok(self) -> float | None:
        """USD per million tokens processed: the account's overall efficiency."""
        return self.cost / self.tokens * 1e6 if self.tokens else None

    @property
    def cost_per_request(self) -> float | None:
        return self.cost / self.requests if self.requests else None

    @property
    def cache_hit(self) -> float | None:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else None

    def minus(self, other: Bucket) -> Bucket:
        return Bucket(
            cost=max(0.0, self.cost - other.cost),
            requests=max(0, self.requests - other.requests),
            prompt_tokens=max(0, self.prompt_tokens - other.prompt_tokens),
            completion_tokens=max(0, self.completion_tokens - other.completion_tokens),
            reasoning_tokens=max(0, self.reasoning_tokens - other.reasoning_tokens),
            cached_tokens=max(0, self.cached_tokens - other.cached_tokens),
            byok=max(0.0, self.byok - other.byok),
        )

    def plus(self, other: Bucket) -> Bucket:
        return Bucket(
            self.cost + other.cost,
            self.requests + other.requests,
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
            self.cached_tokens + other.cached_tokens,
            self.byok + other.byok,
        )


@dataclass
class KeySummary:
    hash: str
    name: str
    disabled: bool
    last_used_at: datetime | None
    usage_total: float
    bucket: Bucket


@dataclass
class AccountSummary:
    id: int
    name: str
    has_management_key: bool
    last_synced: datetime | None
    first_date: str | None
    last_date: str | None


@dataclass
class Summary:
    accounts: list[AccountSummary]
    since: str | None
    until: str | None
    totals: Bucket
    daily: list[tuple[str, Bucket]]
    models: list[tuple[str, Bucket]]
    upstreams: list[tuple[str, Bucket]]
    keys: list[KeySummary]
    other_keys: Bucket | None
    today_usd: float | None
    today_as_of: datetime | None


_SUMS = (
    func.sum(UsageDay.usage),
    func.sum(UsageDay.requests),
    func.sum(UsageDay.prompt_tokens),
    func.sum(UsageDay.completion_tokens),
    func.sum(UsageDay.reasoning_tokens),
    func.sum(UsageDay.cached_tokens),
    func.sum(UsageDay.byok_usage),
)


def _bucket(values: Any) -> Bucket:
    cost, requests, prompt, completion, reasoning, cached, byok = (v or 0 for v in values)
    return Bucket(float(cost), int(requests), int(prompt), int(completion), int(reasoning), int(cached), float(byok))


def _grouped(session: Session, column: Any, where: list[Any]) -> dict[str, Bucket]:
    rows = session.execute(select(column, *_SUMS).where(*where).group_by(column))
    return {str(key): _bucket(rest) for key, *rest in rows}


def _fold(buckets: dict[str, Bucket], shown: int, label: str) -> list[tuple[str, Bucket]]:
    ranked = sorted(buckets.items(), key=lambda item: (-item[1].cost, item[0]))
    if len(ranked) <= shown:
        return ranked
    rest = Bucket()
    for _name, bucket in ranked[shown:]:
        rest = rest.plus(bucket)
    return [*ranked[:shown], (f"{label} ({len(ranked) - shown})", rest)]


def summarize(session: Session, days: int, provider_id: int | None, key_hash: str | None) -> Summary:
    """Spend over the last ``days`` days of history for one account or all of them, and for one key or the whole."""
    providers = list(
        session.scalars(
            select(ProviderConfig)
            .where(ProviderConfig.type == "openrouter", ProviderConfig.enabled.is_(True))
            .order_by(ProviderConfig.id)
        )
    )
    accounts: list[AccountSummary] = []
    for p in providers:
        first, last, synced = session.execute(
            select(func.min(UsageDay.date), func.max(UsageDay.date), func.max(UsageDay.fetched_at)).where(
                UsageDay.provider_config_id == p.id, UsageDay.key_hash == ""
            )
        ).one()
        accounts.append(AccountSummary(p.id, p.name, resolve(p).mgmt_key is not None, synced, first, last))

    ids = [p.id for p in providers if provider_id is None or p.id == provider_id]
    scope = [UsageDay.provider_config_id.in_(ids)] if ids else [false()]
    whole = [*scope, UsageDay.key_hash == ""]
    chosen = [*scope, UsageDay.key_hash == key_hash] if key_hash else whole

    first, last = session.execute(select(func.min(UsageDay.date), func.max(UsageDay.date)).where(*chosen)).one()
    today, today_as_of = _today(session, ids, key_hash)
    if last is None:
        return Summary(accounts, None, None, Bucket(), [], [], [], [], None, today, today_as_of)
    since = max(first, (date.fromisoformat(last) - timedelta(days=days - 1)).isoformat())
    window = [*chosen, UsageDay.date >= since]

    per_day = _grouped(session, UsageDay.date, window)
    daily: list[tuple[str, Bucket]] = []
    day = date.fromisoformat(since)
    while day <= date.fromisoformat(last):
        daily.append((day.isoformat(), per_day.get(day.isoformat(), Bucket())))
        day += timedelta(days=1)
    totals = Bucket()
    for _day, bucket in daily:
        totals = totals.plus(bucket)

    # The keys are listed even when one is chosen, so a picker built from them keeps its options.
    keyed = _grouped(session, UsageDay.key_hash, [*scope, UsageDay.key_hash != "", UsageDay.date >= since])
    listed = list(session.scalars(select(UsageKey).where(UsageKey.provider_config_id.in_(ids)))) if ids else []
    names = {k.key_hash: k for k in listed}
    keys: list[KeySummary] = []
    for digest in sorted(set(names) | set(keyed)):
        info = names.get(digest)
        keys.append(
            KeySummary(
                hash=digest,
                name=(info.name if info else "") or f"Key {digest[:8]}",
                disabled=bool(info and info.disabled),
                last_used_at=info.last_used_at if info else None,
                usage_total=info.usage_total if info else 0.0,
                bucket=keyed.get(digest, Bucket()),
            )
        )
    keys.sort(key=lambda k: (-k.bucket.cost, k.name))
    other: Bucket | None = None
    if not key_hash:
        attributed = Bucket()
        for k in keys:
            attributed = attributed.plus(k.bucket)
        rest = totals.minus(attributed)
        other = rest if rest.cost >= 0.005 or rest.requests else None

    return Summary(
        accounts=accounts,
        since=since,
        until=last,
        totals=totals,
        daily=daily,
        models=_fold(_grouped(session, UsageDay.model, window), MODELS_SHOWN, "Other models"),
        upstreams=_fold(_grouped(session, UsageDay.upstream, window), UPSTREAMS_SHOWN, "Other providers"),
        keys=keys,
        other_keys=other,
        today_usd=today,
        today_as_of=today_as_of,
    )


def _today(session: Session, ids: list[int], key_hash: str | None) -> tuple[float | None, datetime | None]:
    """What the keys have spent in the current UTC day, which the activity API leaves out.

    Only figures fetched today count: one fetched yesterday describes yesterday. None when there is nothing recent.
    """
    if not ids:
        return None, None
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    where = [UsageKey.provider_config_id.in_(ids), UsageKey.fetched_at >= midnight]
    if key_hash:
        where.append(UsageKey.key_hash == key_hash)
    total, count, as_of = session.execute(
        select(func.sum(UsageKey.usage_today), func.count(UsageKey.id), func.max(UsageKey.fetched_at)).where(*where)
    ).one()
    return (float(total or 0), as_of) if count else (None, None)
