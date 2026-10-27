"""Which benchmark rows count towards a model's scores."""

from __future__ import annotations

from sqlalchemy import delete

from vibonomics.db import init_db, session_scope
from vibonomics.orm import BenchmarkScore
from vibonomics.scoring import compute_scores
from vibonomics.settings_store import AppSettings, BenchmarkSourceSettings

MODEL = "acme/scoring-test-model"


def _seed() -> None:
    init_db()
    with session_scope() as session:
        session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id == MODEL))
        for source, metric in (("epoch", "eci"), ("artificial-analysis", "intelligence_index")):
            session.add(
                BenchmarkScore(
                    source=source,
                    metric=metric,
                    dimension="intelligence",
                    source_model=f"{source}-scoring-test-model",
                    canonical_id=MODEL,
                    value=70,
                    percentile=90,
                )
            )


def _sources(settings: AppSettings) -> set[str]:
    with session_scope() as session:
        scores = compute_scores(session, settings)
    return {metric.source for metric in scores[MODEL].metrics} if MODEL in scores else set()


def test_only_sources_in_use_count():
    _seed()
    settings = AppSettings()
    assert _sources(settings) == {"epoch"}, "Artificial Analysis is opt-in"

    settings.benchmarks.sources["artificial-analysis"] = BenchmarkSourceSettings(enabled=True)
    assert _sources(settings) == {"epoch"}, "enabled, but its terms are not acknowledged"
    settings.benchmarks.aa_terms_acknowledged = True
    assert _sources(settings) == {"epoch", "artificial-analysis"}

    settings.benchmarks.sources["epoch"] = BenchmarkSourceSettings(enabled=False)
    assert _sources(settings) == {"artificial-analysis"}
    settings.benchmarks.aa_terms_acknowledged = False
    assert _sources(settings) == set()
