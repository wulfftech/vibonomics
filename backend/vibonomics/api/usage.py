"""Spend history of the OpenRouter accounts: cost, tokens and what a token costs, by day, model, provider and key.

Read-only, from the days Vibonomics has stored (``vibonomics/usage.py``). The figures come from OpenRouter's activity
API through a management key, so an account without one shows no history.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..usage import Bucket, summarize

router = APIRouter(prefix="/api/usage", tags=["usage"])


class SpendBucketOut(BaseModel):
    cost: float = Field(description="USD of OpenRouter credits")
    requests: int
    prompt_tokens: int
    completion_tokens: int = Field(description="Includes the reasoning tokens")
    reasoning_tokens: int
    cached_tokens: int = Field(description="Prompt tokens served from a provider's cache")
    tokens: int = Field(description="Prompt plus completion tokens")
    byok_cost: float = Field(description="USD spent through the operator's own provider keys, not credits")
    cost_per_mtok: float | None = Field(
        description="USD per million tokens: how efficiently the spend turned into work"
    )
    cost_per_request: float | None
    cache_hit: float | None = Field(description="Share of prompt tokens that came from a cache, 0 to 1")


class SpendDayOut(SpendBucketOut):
    date: str


class SpendNamedOut(SpendBucketOut):
    name: str


class SpendKeyOut(SpendBucketOut):
    hash: str
    name: str
    disabled: bool
    last_used_at: datetime | None
    usage_total: float = Field(description="USD this key has spent over its whole life")


class SpendAccountOut(BaseModel):
    id: int
    name: str
    has_management_key: bool = Field(description="Without one, OpenRouter will not report activity")
    last_synced: datetime | None
    first_date: str | None
    last_date: str | None


class SpendOut(BaseModel):
    accounts: list[SpendAccountOut]
    since: str | None
    until: str | None
    days: int
    provider_id: int | None
    key: str | None
    totals: SpendBucketOut
    today_usd: float | None = Field(description="Spent so far in the current UTC day, which the history leaves out")
    today_as_of: datetime | None
    daily: list[SpendDayOut]
    models: list[SpendNamedOut]
    upstreams: list[SpendNamedOut]
    keys: list[SpendKeyOut]
    other_keys: SpendBucketOut | None = Field(
        description="Spend no listed key accounts for: a key in another workspace, or one since deleted"
    )


def _bucket(b: Bucket) -> dict[str, object]:
    return {
        "cost": round(b.cost, 6),
        "requests": b.requests,
        "prompt_tokens": b.prompt_tokens,
        "completion_tokens": b.completion_tokens,
        "reasoning_tokens": b.reasoning_tokens,
        "cached_tokens": b.cached_tokens,
        "tokens": b.tokens,
        "byok_cost": round(b.byok, 6),
        "cost_per_mtok": b.cost_per_mtok,
        "cost_per_request": b.cost_per_request,
        "cache_hit": b.cache_hit,
    }


@router.get("", response_model=SpendOut)
def spend(
    days: int = Query(30, ge=1, le=3650, description="How many days of history, ending with the latest stored day"),
    provider_id: int | None = Query(None, description="One OpenRouter account; all of them when omitted"),
    key: str | None = Query(None, max_length=100, description="One API key's hash; the whole account when omitted"),
    session: Session = Depends(get_session),
) -> SpendOut:
    """Spend, tokens and cost per million tokens over the last ``days`` days of stored history."""
    s = summarize(session, days, provider_id, key)
    return SpendOut(
        accounts=[SpendAccountOut(**a.__dict__) for a in s.accounts],
        since=s.since,
        until=s.until,
        days=days,
        provider_id=provider_id,
        key=key,
        totals=SpendBucketOut(**_bucket(s.totals)),
        today_usd=s.today_usd,
        today_as_of=s.today_as_of,
        daily=[SpendDayOut(date=day, **_bucket(b)) for day, b in s.daily],
        models=[SpendNamedOut(name=name, **_bucket(b)) for name, b in s.models],
        upstreams=[SpendNamedOut(name=name or "Unknown", **_bucket(b)) for name, b in s.upstreams],
        keys=[
            SpendKeyOut(
                hash=k.hash,
                name=k.name,
                disabled=k.disabled,
                last_used_at=k.last_used_at,
                usage_total=k.usage_total,
                **_bucket(k.bucket),
            )
            for k in s.keys
        ],
        other_keys=SpendBucketOut(**_bucket(s.other_keys)) if s.other_keys else None,
    )
