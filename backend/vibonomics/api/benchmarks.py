"""Benchmark match review: list unmatched rows and map them to canonical models."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events
from ..catalog.modelsdev import load_catalog
from ..db import get_session, session_scope
from ..jobs import rematch_scores
from ..orm import BenchmarkScore, ModelAlias

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
    q = select(BenchmarkScore)
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
    with session_scope() as s:
        existing = s.scalars(
            select(ModelAlias).where(ModelAlias.source == body.source, ModelAlias.source_model == body.source_model)
        ).first()
        if existing:
            existing.canonical_id = body.canonical_id
        else:
            s.add(ModelAlias(source=body.source, source_model=body.source_model, canonical_id=body.canonical_id))
    matched = await rematch_scores()
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
