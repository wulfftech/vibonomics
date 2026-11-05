"""Fold benchmark percentiles and Vibonomics's own test results into per-model scores.

Every third-party metric is converted to a percentile within its own leaderboard (0 = worst,
100 = best), which makes Elo ratings, pass rates and indices comparable. A dimension score
(intelligence / coding / agentic) is the weighted mean of the percentiles available for that
model; the composite is the weighted mean of the available dimensions. Vibonomics test scores
(already 0-100) feed the coding and agentic dimensions with ``own_tests_weight``.

Blended prices and value scores depend on the usage profile the operator picks in the UI, so they
are computed client-side from the raw prices and the composite returned here. A program that can't run the
UI's copy asks ``POST /api/models/value``, which uses the same formulas from ``pricing.py``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .benchmarks.base import SourceSpec
from .benchmarks.registry import SOURCES
from .orm import BenchmarkScore, EvalResult, EvalRun, Model
from .settings_store import AppSettings

DIMENSIONS = ("intelligence", "coding", "agentic")

# Result statuses that carry a real grade. The others ("error": the model call failed, "ungraded": no test
# result and no judge score, "skipped") store a placeholder score of 0 and stay out of every average.
GRADED_STATUSES = ("passed", "partial", "failed")


@dataclass
class MetricValue:
    source: str
    source_name: str
    metric: str
    label: str
    dimension: str
    value: float
    percentile: float | None
    effort: str
    as_of: str | None
    url: str | None
    cost_per_task: float | None
    weight: float


@dataclass
class ModelScores:
    canonical_id: str
    intelligence: float | None = None
    coding: float | None = None
    agentic: float | None = None
    composite: float | None = None
    own_test: float | None = None
    source_count: int = 0
    metrics: list[MetricValue] = field(default_factory=list)
    reported_cost_per_task: float | None = None
    reported_cost_sources: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "intelligence": self.intelligence,
            "coding": self.coding,
            "agentic": self.agentic,
            "composite": self.composite,
            "own_test": self.own_test,
            "source_count": self.source_count,
        }


def score_key(canonical_id: str | None, provider_config_id: int, model_id: str) -> str:
    """Scores are shared across providers via the canonical id; unmatched models score alone."""
    return canonical_id or f"@{provider_config_id}/{model_id}"


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return None
    return round(sum(v * w for v, w in pairs) / total_w, 1)


def own_test_scores(session: Session) -> dict[tuple[int, str], dict[str, Any]]:
    """Average Vibonomics test score per (provider_config_id, model_id), from graded results of finished runs.

    The canonical id is the model's current one, not the one saved with a result: that is whatever the model matched
    when it was tested, and a model tested while unmatched and matched since would lose its own test from every score.
    A model that is gone has none, and its results score under its own key.
    """
    rows = session.execute(
        select(
            EvalResult.provider_config_id,
            EvalResult.model_id,
            Model.canonical_id,
            func.avg(EvalResult.score),
            func.count(EvalResult.id),
            func.max(EvalResult.created_at),
            func.avg(EvalResult.input_tokens + EvalResult.output_tokens),
            func.avg(EvalResult.cost_usd),
            func.count(func.distinct(EvalResult.run_id)),
        )
        .join(EvalRun, EvalRun.id == EvalResult.run_id)
        .outerjoin(
            Model, (Model.provider_config_id == EvalResult.provider_config_id) & (Model.model_id == EvalResult.model_id)
        )
        .where(EvalRun.status == "done", EvalResult.status.in_(GRADED_STATUSES))
        .group_by(EvalResult.provider_config_id, EvalResult.model_id)
    ).all()
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for pid, mid, cid, avg, n, last, tokens, cost, runs in rows:
        result[(pid, mid)] = {
            "score": round(float(avg), 1) if avg is not None else None,
            "tasks": n,
            "runs": runs,
            "last_run_at": last,
            "tokens_per_task": round(float(tokens)) if tokens is not None else None,
            "cost_per_task": round(float(cost), 5) if cost is not None else None,
            "canonical_id": cid,
        }
    return result


def source_in_use(settings: AppSettings, source_id: str, spec: SourceSpec) -> bool:
    """Whether a benchmark source's data may be fetched and used: enabled, with its terms acknowledged.

    The spec comes from the caller's registry rather than being looked up here: a caller that swapped
    ``SOURCES`` for its own mapping would otherwise get a ``KeyError`` from this module's copy.
    """
    if not settings.benchmark_source(source_id, spec.default_enabled).enabled:
        return False
    return not spec.terms_warning or settings.benchmarks.aa_terms_acknowledged


def compute_scores(
    session: Session, settings: AppSettings, own_tests: dict[tuple[int, str], dict[str, Any]] | None = None
) -> dict[str, ModelScores]:
    weights = {
        "intelligence": settings.scoring.intelligence_weight,
        "coding": settings.scoring.coding_weight,
        "agentic": settings.scoring.agentic_weight,
    }
    # Rows of a source switched off (or whose terms were withdrawn) stay in the table until its next
    # refresh; they must stop counting now.
    metric_specs = {
        (sid, m.id): (cls.spec, m)
        for sid, cls in SOURCES.items()
        if source_in_use(settings, sid, cls.spec)
        for m in cls.spec.metrics
    }

    # Best value per (canonical, source, metric) across efforts.
    best: dict[tuple[str, str, str], BenchmarkScore] = {}
    for row in session.scalars(select(BenchmarkScore).where(BenchmarkScore.canonical_id.is_not(None))):
        if (row.source, row.metric) not in metric_specs:
            continue
        key = (row.canonical_id or "", row.source, row.metric)
        cur = best.get(key)
        if cur is None or (row.percentile or 0) > (cur.percentile or 0):
            best[key] = row

    scores: dict[str, ModelScores] = {}
    dims: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    costs: dict[str, list[tuple[float, str]]] = defaultdict(list)
    sources_per_model: dict[str, set[str]] = defaultdict(set)
    for (cid, source, metric), row in best.items():
        spec, mspec = metric_specs[(source, metric)]
        ms = scores.setdefault(cid, ModelScores(canonical_id=cid))
        ms.metrics.append(
            MetricValue(
                source=source,
                source_name=spec.name,
                metric=metric,
                label=mspec.label,
                dimension=mspec.dimension,
                value=row.value,
                percentile=row.percentile,
                effort=row.effort,
                as_of=row.as_of,
                url=row.url,
                cost_per_task=row.cost_per_task,
                weight=mspec.weight,
            )
        )
        if row.percentile is not None:
            dims[cid][mspec.dimension].append((row.percentile, mspec.weight))
        if row.cost_per_task is not None:
            costs[cid].append((row.cost_per_task, spec.name))
        sources_per_model[cid].add(source)

    # Own tests, aggregated per canonical model across providers.
    own_by_canonical: dict[str, list[float]] = defaultdict(list)
    for (pid, mid), info in (own_tests or {}).items():
        if info.get("score") is not None:
            own_by_canonical[score_key(info.get("canonical_id"), pid, mid)].append(info["score"])
    for cid, values in own_by_canonical.items():
        ms = scores.setdefault(cid, ModelScores(canonical_id=cid))
        ms.own_test = round(sum(values) / len(values), 1)
        w = settings.scoring.own_tests_weight
        if w > 0:
            dims[cid]["coding"].append((ms.own_test, w))
            dims[cid]["agentic"].append((ms.own_test, w))
        sources_per_model[cid].add("vibonomics")

    for cid, ms in scores.items():
        for dim in DIMENSIONS:
            setattr(ms, dim, _wmean(dims[cid][dim]))
        ms.composite = _wmean(
            [(getattr(ms, d), weights[d]) for d in DIMENSIONS if getattr(ms, d) is not None]  # type: ignore[misc]
        )
        ms.source_count = len(sources_per_model[cid])
        if costs[cid]:
            ms.reported_cost_per_task = round(median(c for c, _ in costs[cid]), 4)
            ms.reported_cost_sources = sorted({name for _, name in costs[cid]})
    return scores
