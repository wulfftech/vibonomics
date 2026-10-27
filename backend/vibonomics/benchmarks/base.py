"""Benchmark source interface.

A source fetches third-party scores and returns ``ScoreRecord``s keyed by the source's own model
naming. The refresh job matches each record to a models.dev canonical model id (see
``matching.py``), computes per-metric percentiles across the source's whole leaderboard, and stores
the rows. ``scoring.py`` folds those percentiles into intelligence / coding / agentic scores.

Rules for sources:
- Only fetch at runtime from the official location; never commit fetched data to the repo.
- Fill ``spec.license`` and ``spec.attribution`` honestly; the UI shows them next to the numbers.
- Put any IDs that help matching into ``ScoreRecord.model_ids`` (OpenRouter ids, provider API ids,
  canonical ``creator/model`` ids). Put reasoning effort ("max", "high"...) in ``effort``, not in
  ``source_model``-derived ids.
- Raise ``vibonomics.http.FetchError`` on failure; return [] only when the source is genuinely empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from ..catalog.modelsdev import ModelsDevCatalog
from ..settings_store import AppSettings

Dimension = Literal["intelligence", "coding", "agentic"]


@dataclass(frozen=True)
class MetricSpec:
    id: str
    label: str
    dimension: Dimension
    higher_is_better: bool = True
    weight: float = 1.0
    unit: str = ""
    description: str = ""


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    homepage: str
    description: str
    license: str
    attribution: str
    metrics: tuple[MetricSpec, ...]
    default_enabled: bool = True
    requires_key: bool = False
    key_label: str | None = None
    key_url: str | None = None
    terms_warning: str | None = None
    """Shown in Settings; the operator must acknowledge it before enabling the source."""


@dataclass
class ScoreRecord:
    metric: str
    source_model: str
    value: float
    effort: str = ""
    model_ids: list[str] = field(default_factory=list)
    cost_per_task: float | None = None
    tokens_per_task: float | None = None
    as_of: str | None = None
    url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchContext:
    catalog: ModelsDevCatalog
    settings: AppSettings
    api_key: str | None = None


class BenchmarkSource:
    spec: ClassVar[SourceSpec]

    def __init__(self, ctx: BenchContext):
        self.ctx = ctx

    async def fetch(self) -> list[ScoreRecord]:
        raise NotImplementedError

    def metric(self, metric_id: str) -> MetricSpec | None:
        return next((m for m in self.spec.metrics if m.id == metric_id), None)
