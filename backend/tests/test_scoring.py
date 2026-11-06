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


# -- preferring the model's own tests over the computed score ----------------------------------------------------

PREFIX = "ownpref/"
PROVIDER_A, PROVIDER_B = 9001, 9002  # compute_scores only reads the dict it is given, so these need no provider row

# canonical id -> percentiles (intelligence, coding, agentic), one source each, at weights 1.5 / 0.75 / 1.5
THIRD_PARTY = {
    PREFIX + "full": (80, 50, 70),
    PREFIX + "few": (80, 50, 70),
    PREFIX + "two-providers": (80, 50, 70),
    PREFIX + "no-tests": (60, 40, 20),
}
BENCHMARK_ROWS = (
    ("epoch", "eci", "intelligence"),
    ("epoch", "swe_bench_verified", "coding"),
    ("terminal-bench", "accuracy", "agentic"),
)


def _seed_third_party() -> None:
    init_db()
    with session_scope() as session:
        session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id.like(PREFIX + "%")))
        for canonical, percentiles in THIRD_PARTY.items():
            for (source, metric, dimension), percentile in zip(BENCHMARK_ROWS, percentiles, strict=True):
                session.add(
                    BenchmarkScore(
                        source=source,
                        metric=metric,
                        dimension=dimension,
                        source_model=f"{canonical}-{metric}",
                        canonical_id=canonical,
                        value=percentile,
                        percentile=percentile,
                    )
                )


def _own(canonical: str, score: float, tasks: int, provider: int = PROVIDER_A) -> dict:
    return {(provider, canonical): {"score": score, "tasks": tasks, "canonical_id": canonical}}


# "full" has plenty of tasks, "few" is one short of the default minimum, "two-providers" reaches it only by adding up
# its two providers, "own-only" has no third-party data at all and "no-tests" has no tests.
OWN_TESTS = {
    **_own(PREFIX + "full", 90.0, 10),
    **_own(PREFIX + "few", 90.0, 4),
    **_own(PREFIX + "two-providers", 80.0, 3, PROVIDER_A),
    **_own(PREFIX + "two-providers", 60.0, 2, PROVIDER_B),
    **_own(PREFIX + "own-only", 60.0, 8),
}


def _compute(settings: AppSettings | None = None, **kwargs) -> dict:
    _seed_third_party()
    with session_scope() as session:
        return compute_scores(session, settings or AppSettings(), OWN_TESTS, **kwargs)


def _preferring(**scoring) -> AppSettings:
    settings = AppSettings()
    settings.scoring.prefer_own_tests = True
    for field, value in scoring.items():
        setattr(settings.scoring, field, value)
    return settings


def _numbers(scores: dict, name: str) -> tuple:
    s = scores[PREFIX + name]
    return (s.intelligence, s.coding, s.agentic, s.composite)


def test_without_the_setting_the_own_tests_are_blended_in_as_they_always_were():
    scores = _compute()
    # coding = (50 * 0.75 + 90 * 1) / 1.75, agentic = (70 * 1.5 + 90 * 1) / 2.5, weights 0.3 / 0.35 / 0.35
    assert _numbers(scores, "full") == (80.0, 72.9, 78.0, 76.8)
    assert _numbers(scores, "few") == (80.0, 72.9, 78.0, 76.8)
    assert _numbers(scores, "two-providers") == (80.0, 61.4, 70.0, 70.0)
    assert _numbers(scores, "own-only") == (None, 60.0, 60.0, 60.0)
    assert _numbers(scores, "no-tests") == (60.0, 40.0, 20.0, 39.0)
    assert scores[PREFIX + "full"].own_test == 90.0
    assert scores[PREFIX + "no-tests"].own_test is None


def test_the_preference_is_off_by_default_and_nothing_reports_preferred():
    assert AppSettings().scoring.prefer_own_tests is False
    assert AppSettings().scoring.prefer_own_tests_min_tasks == 5
    scores = _compute()
    assert not any(s.own_test_preferred or s.summary()["own_test_preferred"] for s in scores.values())


def test_a_model_with_enough_tests_scores_exactly_what_its_own_tests_scored():
    scores = _compute(_preferring())
    full = scores[PREFIX + "full"]
    # Coding and agentic are the own score whatever the benchmarks say; intelligence is unchanged (own tests do not
    # measure it); the composite is 80 * 0.3 + 90 * 0.35 + 90 * 0.35.
    assert _numbers(scores, "full") == (80.0, 90.0, 90.0, 87.0)
    assert full.own_test == 90.0
    assert full.own_test_preferred is True
    assert full.summary()["own_test_preferred"] is True
    assert full.summary()["own_test"] == 90.0


def test_the_composite_of_a_preferred_model_follows_the_configured_dimension_weights():
    scores = _compute(_preferring(intelligence_weight=1, coding_weight=2, agentic_weight=1))
    assert _numbers(scores, "full") == (80.0, 90.0, 90.0, 87.5)  # (80 + 90 * 2 + 90) / 4
    scores = _compute(_preferring(intelligence_weight=0, coding_weight=1, agentic_weight=3))
    assert scores[PREFIX + "full"].composite == 90.0


def test_a_model_with_too_few_graded_tasks_is_computed_as_before():
    scores = _compute(_preferring())
    few = scores[PREFIX + "few"]
    assert _numbers(scores, "few") == (80.0, 72.9, 78.0, 76.8)
    assert few.own_test == 90.0, "its own score is still shown"
    assert few.own_test_preferred is False


def test_a_model_without_tests_is_computed_as_before():
    scores = _compute(_preferring())
    assert _numbers(scores, "no-tests") == (60.0, 40.0, 20.0, 39.0)
    assert scores[PREFIX + "no-tests"].own_test_preferred is False


def test_a_model_with_tests_and_no_third_party_data_is_still_preferred():
    scores = _compute(_preferring())
    assert _numbers(scores, "own-only") == (None, 60.0, 60.0, 60.0)
    assert scores[PREFIX + "own-only"].own_test_preferred is True


def test_the_minimum_is_a_count_of_tasks_summed_over_the_providers_of_one_model():
    two = _compute(_preferring())[PREFIX + "two-providers"]
    assert two.own_test == 70.0, "the average of the two providers' averages, as it always was"
    assert two.own_test_tasks == 5
    assert (two.coding, two.agentic, two.composite) == (70.0, 70.0, 73.0)
    assert two.own_test_preferred is True, "3 + 2 tasks reach the minimum of 5 though neither provider does alone"
    assert _compute(_preferring(prefer_own_tests_min_tasks=6))[PREFIX + "two-providers"].own_test_preferred is False


def test_the_minimum_counts_tasks_inclusively():
    assert _compute(_preferring(prefer_own_tests_min_tasks=10))[PREFIX + "full"].own_test_preferred is True
    assert _compute(_preferring(prefer_own_tests_min_tasks=11))[PREFIX + "full"].own_test_preferred is False
    assert _compute(_preferring(prefer_own_tests_min_tasks=1))[PREFIX + "few"].own_test_preferred is True


def test_the_blend_weight_has_no_effect_on_a_preferred_model():
    for weight in (0, 1, 5):
        scores = _compute(_preferring(own_tests_weight=weight))
        assert _numbers(scores, "full") == (80.0, 90.0, 90.0, 87.0), weight
        assert scores[PREFIX + "full"].own_test_preferred is True
    # ... while it still decides how much a model that does not qualify takes from its tests
    assert _numbers(_compute(_preferring(own_tests_weight=0)), "few") == (80.0, 50.0, 70.0, 66.0)


def test_the_blend_weight_zero_keeps_the_own_score_out_when_the_preference_is_off():
    scores = _compute(AppSettings(scoring={"own_tests_weight": 0}))
    assert _numbers(scores, "full") == (80.0, 50.0, 70.0, 66.0)
    assert scores[PREFIX + "full"].own_test == 90.0
    assert scores[PREFIX + "full"].own_test_preferred is False


def test_a_caller_can_override_the_setting_for_one_call():
    on_by_setting, off_by_setting = _preferring(), AppSettings()
    assert _compute(off_by_setting, prefer_own_tests=True)[PREFIX + "full"].coding == 90.0
    assert _compute(off_by_setting, prefer_own_tests=None)[PREFIX + "full"].coding == 72.9
    assert _compute(off_by_setting, prefer_own_tests=False)[PREFIX + "full"].coding == 72.9
    assert _compute(on_by_setting, prefer_own_tests=False)[PREFIX + "full"].coding == 72.9
    assert _compute(on_by_setting, prefer_own_tests=None)[PREFIX + "full"].coding == 90.0
    # the override only flips the switch: the minimum still comes from the settings
    assert _compute(off_by_setting, prefer_own_tests=True)[PREFIX + "few"].own_test_preferred is False
    # and it leaves the settings object alone
    assert off_by_setting.scoring.prefer_own_tests is False and on_by_setting.scoring.prefer_own_tests is True
