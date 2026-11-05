"""Prices, discounts and value for the models a client names, and the best value among them.

``POST /api/models/value`` serves a program that sends work to models and has to choose between the ones it can
use: a reviewer with a pool of batch models, for instance. It names each model as its own provider does, says how
big a task is (or picks a usage profile), and gets back each model's current price after discounts, its scores
and its estimated cost and value for that task, ranked, with a pick and the reason for it. Value is the UI's:
score points per dollar per task, from the same formulas (``vibonomics/pricing.py``).

Read-only, and behind the password guard like the rest of ``/api``: a client of an instance with
``VIBONOMICS_PASSWORD`` set logs in first and sends the session cookie.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..orm import Model, ProviderConfig, RefreshRun
from ..pricing import Task, task_cost, value_of
from ..scoring import ModelScores, compute_scores, own_test_scores, score_key
from ..settings_store import AppSettings, load_settings
from .models import PriceOut, ScoresOut

MAX_BODY_BYTES = 128 * 1024
MAX_ERRORS = 20

Dimension = Literal["composite", "intelligence", "coding", "agentic"]


async def _read_capped(request: Request) -> bytes:
    """The request body, refused with 413 as soon as it passes the cap instead of after it is all in memory."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _replay(raw: bytes) -> Callable[[], Awaitable[dict[str, Any]]]:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return receive


class _CappedRoute(APIRoute):
    """This is the one POST a visitor to an open-read instance may call, so it reads its body under a cap and does
    not hand a bad one back: FastAPI's own 422 repeats every offending ``input`` in full.

    A route class rather than a ``Request`` parameter keeps the typed body, and so the request schema in the API
    document, as it was.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def capped(request: Request) -> Response:
            raw = await _read_capped(request)
            try:
                return await handler(Request(request.scope, _replay(raw)))
            except RequestValidationError as exc:
                detail = [
                    {"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()[:MAX_ERRORS]
                ]
                raise HTTPException(status_code=422, detail=detail) from None

        return capped


router = APIRouter(prefix="/api/models", tags=["models"], route_class=_CappedRoute)


class ValueModelRef(BaseModel):
    provider: str = Field(
        min_length=1,
        max_length=100,
        description="A provider type such as `openrouter`, or a configured provider's name",
    )
    model: str = Field(
        min_length=1,
        max_length=300,
        description="The model id as that provider lists it, e.g. `openai/gpt-5.4-mini:batch`",
    )


class ValueTaskIn(BaseModel):
    input_tokens: int = Field(ge=0, le=100_000_000)
    output_tokens: int = Field(ge=0, le=100_000_000)
    cache_hit_rate: float = Field(0, ge=0, le=1)

    @model_validator(mode="after")
    def _not_empty(self) -> ValueTaskIn:
        if self.input_tokens + self.output_tokens == 0:
            raise ValueError("a task needs some input or output tokens")
        return self


class ValueRequest(BaseModel):
    models: list[ValueModelRef] = Field(min_length=1, max_length=200)
    profile: str | None = Field(
        None, description="A usage profile id; the operator's default when neither this nor task is given"
    )
    task: ValueTaskIn | None = Field(None, description="The size of one task; takes precedence over profile")
    dimension: Dimension = "composite"
    min_score: float | None = Field(None, ge=0, le=100, description="A model scoring below this is never picked")
    alternatives: int = Field(
        0,
        ge=0,
        le=20,
        description="Also suggest up to this many models the request did not name, from the same providers, that "
        "score at least as well as the pick and give better value (with no pick, the best values at or above "
        "min_score). Free offers are left out.",
    )
    include_client_restricted: bool = Field(
        False,
        description="Rank, and possibly pick, a model that only one client may call (the OpenCode Console's free "
        "tier answers 403 to every other client). Off by default: a program using this API is not that client.",
    )


class ValueTaskOut(BaseModel):
    profile: str | None
    input_tokens: int
    output_tokens: int
    cache_hit_rate: float


class ValueRow(BaseModel):
    provider: str
    model: str
    found: bool
    eligible: bool = False
    rank: int | None = None
    note: str | None = None
    id: int | None = None
    provider_name: str | None = None
    name: str | None = None
    canonical_id: str | None = None
    active: bool | None = None
    batch: bool = False
    is_free: bool = False
    price: PriceOut | None = None
    list_price: PriceOut | None = None
    discount: float | None = None
    discount_note: str | None = None
    discount_until: str | None = None
    discount_since: datetime | None = None
    new_discount: bool = False
    price_drop: bool = False
    client_restricted: str | None = Field(
        None,
        description="The only client that may call this model (`opencode` for the OpenCode Console's free tier); "
        "such a model is not eligible unless the request sets include_client_restricted",
    )
    batch_discount: float | None = Field(None, description="The provider's own batch API discount, for a direct offer")
    prices_as_of: datetime | None = None
    score: float | None = Field(None, description="The requested dimension's score, 0-100")
    scores: ScoresOut | None = None
    task_cost: float | None = Field(None, description="Estimated USD per task at the current price; 0 when free")
    value: float | None = Field(
        None, description="Score points per dollar per task; a free model is costed at free_model_cost, as in the UI"
    )


class ValueResponse(BaseModel):
    generated_at: datetime
    scores_as_of: datetime | None
    task: ValueTaskOut
    dimension: Dimension
    min_score: float | None
    free_model_cost: float
    models: list[ValueRow]
    pick: ValueRow | None
    reason: str
    alternatives: list[ValueRow]


@dataclass
class _Scored:
    row: ValueRow
    value: float | None  # unrounded, and possibly infinite, for sorting
    index: int


def _task(body: ValueRequest, settings: AppSettings) -> tuple[Task, str | None]:
    if body.task is not None:
        return Task(body.task.input_tokens, body.task.output_tokens, body.task.cache_hit_rate), None
    profiles = {p.id: p for p in settings.scoring.profiles}
    wanted = body.profile or settings.scoring.default_profile
    profile = profiles.get(wanted)
    if profile is None and body.profile is None and settings.scoring.profiles:
        profile = settings.scoring.profiles[0]
    if profile is None:
        raise HTTPException(422, f"No usage profile called {wanted!r}; there are: {', '.join(profiles) or 'none'}")
    return Task(profile.task_input_tokens, profile.task_output_tokens, profile.cache_hit_rate), profile.id


def _matching_providers(name: str, providers: list[ProviderConfig]) -> list[ProviderConfig]:
    key = name.strip().lower()
    return [p for p in providers if p.type.lower() == key or p.name.strip().lower() == key]


def _batch_discount(m: Model) -> float | None:
    raw = (m.extra or {}).get("batch_discount")
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


def _score(
    ref: ValueModelRef,
    m: Model,
    provider: ProviderConfig,
    sc: ModelScores | None,
    dimension: Dimension,
    task: Task,
    free_cost: float,
    disc_cut: datetime,
    index: int,
) -> _Scored:
    score = getattr(sc, dimension) if sc else None
    cost = task_cost(m.price_input, m.price_output, m.price_cache_read, task, m.is_free)
    value_cost = task_cost(free_cost, free_cost, free_cost, task) if m.is_free else cost
    value = value_of(score, value_cost)
    row = ValueRow(
        provider=ref.provider,
        model=ref.model,
        found=True,
        id=m.id,
        provider_name=provider.name,
        name=m.name,
        canonical_id=m.canonical_id,
        active=m.active,
        batch=m.model_id.endswith(":batch"),
        is_free=m.is_free,
        price=PriceOut(
            input=m.price_input, output=m.price_output, cache_read=m.price_cache_read, cache_write=m.price_cache_write
        ),
        list_price=PriceOut(input=m.list_input, output=m.list_output) if m.discount else None,
        discount=m.discount,
        discount_note=m.discount_note,
        discount_until=m.discount_until,
        discount_since=m.discount_since if m.discount else None,
        new_discount=bool(m.discount and m.discount_since and m.discount_since >= disc_cut),
        price_drop=bool(m.price_drop_since and m.price_drop_since >= disc_cut),
        client_restricted=(m.extra or {}).get("client_restricted") or None,
        batch_discount=_batch_discount(m),
        prices_as_of=provider.last_refresh_at,
        score=score,
        scores=ScoresOut(**sc.summary()) if sc else ScoresOut(),
        task_cost=_significant(cost) if cost is not None else None,
        value=round(value, 1) if value is not None and math.isfinite(value) else None,
    )
    return _Scored(row=row, value=value, index=index)


def _significant(x: float, digits: int = 6) -> float:
    """``x`` to ``digits`` significant figures. Decimal places would turn a tiny task's cost into 0.0 beside a finite
    value, and a client that divides the score by it would divide by zero."""
    return float(f"{x:.{digits}g}")


def _judge(entry: _Scored, dimension: Dimension, min_score: float | None, include_restricted: bool) -> None:
    row = entry.row
    if not row.active:
        row.note = f"No longer in {row.provider_name}'s model list"
    elif row.client_restricted and not include_restricted:
        row.note = f"Only the {row.client_restricted} client may call it; any other gets HTTP 403"
    elif row.task_cost is None:
        row.note = "No price"
    elif row.score is None:
        row.note = f"No {dimension} score"
    elif min_score is not None and row.score < min_score:
        row.note = f"Scores {row.score:g}, under the floor of {min_score:g}"
    else:
        row.eligible = True


def _sort_key(entry: _Scored) -> tuple[float, float, float, int]:
    return (
        -(entry.value if entry.value is not None else -math.inf),
        -(entry.row.score or 0),
        entry.row.task_cost if entry.row.task_cost is not None else math.inf,
        entry.index,
    )


def _describe(entry: _Scored) -> str:
    row = entry.row
    # A row has no value when it is infinite, which also holds for an offer with a zero price that is not flagged
    # free, such as a subscription plan.
    if row.is_free or row.value is None:
        return f"{row.score:g} for nothing"
    cost = f"{row.task_cost:.4f}" if (row.task_cost or 0) >= 1e-4 else f"{row.task_cost:.2g}"
    return f"{row.score:g} for ${cost} a task, {row.value:,.0f} points per dollar"


def _reason(ranked: list[_Scored], dimension: Dimension, min_score: float | None) -> str:
    if not ranked:
        floor = f" at or above {min_score:g}" if min_score is not None else ""
        return f"None of these models has a current price and a {dimension} score{floor}."
    best = ranked[0]
    out = f"{best.row.model} gives the best value of the {len(ranked)} that qualify: {_describe(best)}."
    if len(ranked) > 1:
        out += f" Next is {ranked[1].row.model}: {_describe(ranked[1])}."
    return out


@router.post("/value", response_model=ValueResponse)
def models_value(body: ValueRequest, session: Session = Depends(get_session)) -> ValueResponse:
    """Price, score and rank the named models for one task size, and pick the best value."""
    settings = load_settings(session)
    task, profile_id = _task(body, settings)
    providers = list(session.scalars(select(ProviderConfig).where(ProviderConfig.enabled.is_(True))))
    scores = compute_scores(session, settings, own_test_scores(session))
    now = datetime.now(UTC)
    disc_cut = now - timedelta(days=settings.highlight.new_discount_days)
    free_cost = max(0.0, settings.scoring.free_model_cost)

    matched = {i: _matching_providers(ref.provider, providers) for i, ref in enumerate(body.models)}
    pids = {p.id for found in matched.values() for p in found}
    offers: dict[tuple[int, str], Model] = {}
    if pids:
        query = select(Model).where(
            Model.provider_config_id.in_(pids), Model.model_id.in_({ref.model for ref in body.models})
        )
        offers = {(m.provider_config_id, m.model_id): m for m in session.scalars(query)}

    by_id = {p.id: p for p in providers}

    def scored(ref: ValueModelRef, m: Model, index: int) -> _Scored:
        sc = scores.get(score_key(m.canonical_id, m.provider_config_id, m.model_id))
        return _score(ref, m, by_id[m.provider_config_id], sc, body.dimension, task, free_cost, disc_cut, index)

    entries: list[_Scored] = []
    judged: set[int | None] = set()  # the models already in the ranking, by Model.id
    for i, ref in enumerate(body.models):
        if not matched[i]:
            note = f"No enabled provider called {ref.provider}"
            entries.append(_Scored(ValueRow(provider=ref.provider, model=ref.model, found=False, note=note), None, i))
            continue
        candidates = [scored(ref, offers[(p.id, ref.model)], i) for p in matched[i] if (p.id, ref.model) in offers]
        if not candidates:
            note = f"Not in {ref.provider}'s model list"
            entries.append(_Scored(ValueRow(provider=ref.provider, model=ref.model, found=False, note=note), None, i))
            continue
        # Two providers of one type can both list a model: the live, cheaper offer stands for it.
        best = min(candidates, key=lambda e: (not e.row.active, _sort_key(e)[2], e.row.id or 0))
        if best.row.id in judged:
            # The same model named again, or by another name for its provider: it would be ranked against itself.
            best.row.note = "Named more than once in the request"
        else:
            judged.add(best.row.id)
            _judge(best, body.dimension, body.min_score, body.include_client_restricted)
        entries.append(best)

    ranked = sorted((e for e in entries if e.row.eligible), key=_sort_key)
    for n, entry in enumerate(ranked, start=1):
        entry.row.rank = n
    pick = ranked[0] if ranked else None

    alternatives: list[ValueRow] = []
    if body.alternatives and pids:
        named = {e.row.id for e in entries if e.row.id is not None}
        floor = max(body.min_score or 0, pick.row.score or 0) if pick else body.min_score
        others: list[_Scored] = []
        query = select(Model).where(
            Model.provider_config_id.in_(pids), Model.active.is_(True), Model.is_free.is_(False)
        )
        for m in session.scalars(query):
            if m.id in named:
                continue
            entry = scored(ValueModelRef(provider=by_id[m.provider_config_id].type, model=m.model_id), m, len(entries))
            _judge(entry, body.dimension, floor, body.include_client_restricted)
            if not entry.row.eligible or (pick and (entry.value or 0) <= (pick.value or 0)):
                continue
            entry.row.eligible = False  # not a candidate for this request's pick, only a suggestion
            entry.row.note = "Not in the request"
            others.append(entry)
        alternatives = [e.row for e in sorted(others, key=_sort_key)[: body.alternatives]]

    benchmarks = session.scalars(
        select(RefreshRun.finished_at)
        .where(RefreshRun.kind == "benchmarks", RefreshRun.status.in_(("ok", "partial")))
        .order_by(RefreshRun.finished_at.desc())
        .limit(1)
    ).first()
    return ValueResponse(
        generated_at=now,
        scores_as_of=benchmarks,
        task=ValueTaskOut(
            profile=profile_id,
            input_tokens=task.input_tokens,
            output_tokens=task.output_tokens,
            cache_hit_rate=task.cache_hit_rate,
        ),
        dimension=body.dimension,
        min_score=body.min_score,
        free_model_cost=free_cost,
        models=[e.row for e in entries],
        pick=pick.row if pick else None,
        reason=_reason(ranked, body.dimension, body.min_score),
        alternatives=alternatives,
    )
