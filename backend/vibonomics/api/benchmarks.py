"""Benchmark match review: list unmatched rows and map them to canonical models."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events
from ..benchmarks.registry import SOURCES
from ..catalog.modelsdev import load_catalog
from ..db import get_session, session_scope
from ..http import FetchError
from ..jobs import rematch_scores
from ..orm import BenchmarkScore, ModelAlias
from ..scoring import source_in_use
from ..settings_store import load_settings

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


class ScoreRowOut(BaseModel):
    source: str
    source_model: str
    metrics: dict[str, float]
    canonical_id: str | None
    match_method: str | None


class AliasIn(BaseModel):
    source: str
    source_model: str
    canonical_id: str | None


class CanonicalOut(BaseModel):
    id: str
    name: str


@router.get("/rows", response_model=list[ScoreRowOut])
def score_rows(
    source: str | None = None, unmatched_only: bool = False, session: Session = Depends(get_session)
) -> list[ScoreRowOut]:
    # Open to visitors, and the terms of some sources (Artificial Analysis) forbid serving their numbers to
    # anyone who has not opted in, so a source that is off serves nothing.
    settings = load_settings(session)
    in_use = [sid for sid, cls in SOURCES.items() if source_in_use(settings, sid, cls.spec)]
    q = select(BenchmarkScore).where(BenchmarkScore.source.in_(in_use))
    if source:
        q = q.where(BenchmarkScore.source == source)
    if unmatched_only:
        q = q.where(BenchmarkScore.canonical_id.is_(None))
    grouped: dict[tuple[str, str], ScoreRowOut] = {}
    for row in session.scalars(q):
        key = (row.source, row.source_model)
        out = grouped.setdefault(
            key,
            ScoreRowOut(
                source=row.source,
                source_model=row.source_model,
                metrics={},
                canonical_id=row.canonical_id,
                match_method=row.match_method,
            ),
        )
        label = row.metric + (f" ({row.effort})" if row.effort else "")
        out.metrics[label] = row.value
    return sorted(grouped.values(), key=lambda r: (r.source, r.source_model.lower()))


@router.put("/aliases", response_model=dict)
async def set_alias(body: AliasIn) -> dict[str, int]:
    catalog = await load_catalog()
    if catalog.degraded:
        raise HTTPException(
            503, "The model catalogue is not available, so the alias cannot be checked. Try again later."
        )
    if body.canonical_id is not None and body.canonical_id not in catalog.canonical_index():
        # Otherwise the scores would attach to nothing and the name would leave the unmatched list.
        raise HTTPException(422, f"'{body.canonical_id}' is not a model in the catalogue")
    with session_scope() as s:
        existing = s.scalars(
            select(ModelAlias).where(ModelAlias.source == body.source, ModelAlias.source_model == body.source_model)
        ).first()
        if existing:
            existing.canonical_id = body.canonical_id
        else:
            s.add(ModelAlias(source=body.source, source_model=body.source_model, canonical_id=body.canonical_id))
    try:
        matched = await rematch_scores()
    except FetchError as exc:  # the catalogue went away after the check above
        raise HTTPException(503, str(exc)) from exc
    events.publish("benchmarks.changed", {})
    return {"matched": matched}


@router.get("/canonical", response_model=list[CanonicalOut])
async def canonical_search(q: str = Query("", max_length=100), limit: int = 30) -> list[CanonicalOut]:
    catalog = await load_catalog()
    needle = q.lower().strip()
    results = []
    for cid, info in catalog.canonical_index().items():
        if not needle or needle in cid.lower() or needle in info.name.lower():
            results.append(CanonicalOut(id=cid, name=info.name))
            if len(results) >= limit:
                break
    return results
