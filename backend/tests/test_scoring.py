"""Which benchmark rows count towards a model's scores."""

from __future__ import annotations

from sqlalchemy import delete

from vibonomics.benchmarks.base import SourceSpec
from vibonomics.db import init_db, session_scope
from vibonomics.orm import BenchmarkScore, EvalResult, EvalRun, Model, ProviderConfig
from vibonomics.scoring import compute_scores, own_test_scores, score_key, source_in_use
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


def test_a_source_outside_this_module_registry_is_decided_from_the_callers_spec():
    """A caller that swapped SOURCES for its own mapping (the refresh job, in tests) must not get a
    KeyError from scoring's copy of the registry."""
    spec = SourceSpec(
        id="not-in-registry",
        name="Not In Registry",
        homepage="https://example.com",
        description="",
        license="test",
        attribution="test",
        metrics=(),
    )
    settings = AppSettings()
    assert source_in_use(settings, "not-in-registry", spec) is True
    settings.benchmarks.sources["not-in-registry"] = BenchmarkSourceSettings(enabled=False)
    assert source_in_use(settings, "not-in-registry", spec) is False


# -- a model's own tests follow the model, not the id it had when it was tested (V-4) ------------------------

OWN_PROVIDER = "Own test scoring provider"


def _tested_model(canonical_at_test_time: str | None, canonical_now: str | None) -> tuple[int, str]:
    """A provider with one model, tested in two results; returns (provider id, model id)."""
    init_db()
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name == OWN_PROVIDER))
        session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id == "acme/matched-later"))
        provider = ProviderConfig(type="openrouter", name=OWN_PROVIDER, enabled=True)
        session.add(provider)
        session.flush()
        session.add(Model(provider_config_id=provider.id, model_id="acme/m", name="m", canonical_id=canonical_now))
        run = EvalRun(status="done", suite="quick")
        session.add(run)
        session.flush()
        for task, score, canonical in (("t1", 80.0, canonical_at_test_time), ("t2", 60.0, None)):
            session.add(
                EvalResult(
                    run_id=run.id,
                    provider_config_id=provider.id,
                    model_id="acme/m",
                    canonical_id=canonical,
                    task_id=task,
                    category="chat",
                    status="passed",
                    score=score,
                )
            )
        session.add(
            BenchmarkScore(
                source="epoch",
                metric="eci",
                dimension="intelligence",
                source_model="own-test-scoring-model",
                canonical_id="acme/matched-later",
                value=70,
                percentile=70,
            )
        )
        return provider.id, "acme/m"


def test_a_model_tested_while_unmatched_keeps_its_own_test_once_it_is_matched():
    # Tested while models.dev did not know it (no canonical id saved with the result), matched since.
    pid, mid = _tested_model(canonical_at_test_time=None, canonical_now="acme/matched-later")
    with session_scope() as session:
        own = own_test_scores(session)
        assert own[(pid, mid)]["canonical_id"] == "acme/matched-later"
        scores = compute_scores(session, AppSettings(), own)
    assert scores[score_key("acme/matched-later", pid, mid)].own_test == 70.0
    assert f"@{pid}/{mid}" not in scores, "the own test was left under the key of the unmatched model"


def test_the_canonical_id_saved_with_an_old_result_does_not_outvote_the_models_current_one():
    # One result carries an id from a time the model was matched to something else; the model row is the truth.
    pid, mid = _tested_model(canonical_at_test_time="acme/stale", canonical_now="acme/matched-later")
    with session_scope() as session:
        assert own_test_scores(session)[(pid, mid)]["canonical_id"] == "acme/matched-later"


def test_results_of_a_model_that_is_gone_still_count_under_its_own_key():
    pid, mid = _tested_model(canonical_at_test_time=None, canonical_now=None)
    with session_scope() as session:
        session.execute(delete(Model).where(Model.provider_config_id == pid))
    with session_scope() as session:
        own = own_test_scores(session)
        assert own[(pid, mid)]["score"] == 70.0
        assert own[(pid, mid)]["canonical_id"] is None
        assert f"@{pid}/{mid}" in compute_scores(session, AppSettings(), own)
