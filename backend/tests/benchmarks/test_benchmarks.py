"""Benchmark source parsers against captured fixtures (no network)."""

import asyncio
import io
import json
import math
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from vibonomics import events, jobs
from vibonomics.benchmarks import arena, artificial_analysis, epoch, livebench, openrouter_datasets, terminal_bench
from vibonomics.benchmarks.base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec
from vibonomics.benchmarks.matching import Matcher, curated_aliases, split_effort
from vibonomics.benchmarks.registry import SOURCES
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.db import init_db, session_scope
from vibonomics.http import FetchError
from vibonomics.orm import BenchmarkScore, ModelAlias, RefreshRun

FIX = Path(__file__).parent / "fixtures"


def load(path: str):
    return json.loads((FIX / path).read_text(encoding="utf-8"))


# -- Terminal-Bench ---------------------------------------------------------------------------


def test_terminal_bench_submission():
    data = load("terminal_bench/2026-09-30-anthropic-claude-opus-5-5-max-claude-code.json")
    record = terminal_bench.parse_submission("claude-opus.json", data)
    assert record is not None
    assert record.metric == "accuracy"
    assert record.effort == "max"
    assert "anthropic/claude-opus-5-5" in record.model_ids
    assert 0 <= record.value <= 100
    metrics = data["metrics"]
    expected_cost = metrics["total_cost_usd"] / metrics["n_trials"]
    assert record.cost_per_task == pytest.approx(expected_cost, rel=1e-3)
    assert (record.extra or {}).get("agent")


def test_terminal_bench_rejects_broken_submission():
    assert terminal_bench.parse_submission("x.json", {"metrics": {}}) is None
    assert terminal_bench.parse_submission("x.json", "not a dict") is None


# -- Arena ------------------------------------------------------------------------------------


def test_arena_text_records():
    rows = load("arena/text_style_control.json")
    records = arena.rows_to_records("text_style_control", rows)
    assert records
    first = records[0]
    assert first.metric in {"text", "text_coding"}
    assert first.effort in {"high", "max", ""}
    assert first.value > 1000


def test_arena_agent_scores_are_normalised():
    records = arena.rows_to_records("agent", load("arena/agent.json"))
    assert records and records[0].metric == "agent"
    assert 0 < records[0].value <= 1


def test_arena_name_hints_extract_snapshots_and_effort():
    hints, effort, leftovers = arena.name_hints("DeepSeek-V4-Pro (High, 20260813)")
    assert effort == "high"
    assert "DeepSeek-V4-Pro-0813" in hints
    assert "DeepSeek-V4-Pro" in hints
    assert leftovers == ["20260813"]


# -- Epoch ------------------------------------------------------------------------------------


def _epoch_zip() -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name in (
            "model_metadata.csv",
            "epoch_capabilities_index/eci_scores.csv",
            "swe_bench_verified.csv",
            "mirrorcode.csv",
        ):
            zf.writestr(name, (FIX / "epoch" / name).read_text(encoding="utf-8"))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_epoch_archive_parsing():
    with _epoch_zip() as zf:
        records = epoch.parse_archive(zf)
    assert any(r.metric == "eci" for r in records)
    assert any(r.metric == "swe_bench_verified" for r in records)
    eci = next(r for r in records if r.metric == "eci")
    assert eci.value > 0
    assert eci.model_ids


def test_epoch_split_version():
    assert epoch.split_version("claude-opus-5-5_max") == ("claude-opus-5-5", "max")
    assert epoch.split_version("glm-5.3_unknown") == ("glm-5.3", "")


# -- LiveBench --------------------------------------------------------------------------------


def test_livebench_parsing_and_task_cost():
    records = livebench.parse_release(
        "20261014",
        (FIX / "livebench" / "table.csv").read_text(encoding="utf-8"),
        (FIX / "livebench" / "cost.csv").read_text(encoding="utf-8"),
    )
    metrics = {r.metric for r in records}
    assert {"reasoning", "coding", "long_agentic"} <= metrics
    agentic = next(r for r in records if r.metric == "long_agentic")
    assert agentic.cost_per_task and agentic.cost_per_task > 0
    assert agentic.cost_per_task != agentic.extra.get("cost_per_question")


def test_livebench_release_candidates():
    bundle = (FIX / "livebench" / "main.js").read_text(encoding="utf-8")
    releases = livebench.release_candidates(bundle)
    assert releases
    assert releases[0] == "20261014"


# -- OpenRouter datasets ----------------------------------------------------------------------


def test_openrouter_benchmarks():
    data = load("openrouter/benchmarks.json")
    slugs = openrouter_datasets.slug_map(load("openrouter/models.json"))
    records = openrouter_datasets.parse_benchmarks(data, slugs)
    assert records, "fixture has no OpenRouter-run benchmarks"
    record = records[0]
    assert record.metric in {"tau_bench_airline", "gpqa_diamond"}
    assert 0 <= record.value <= 100


def test_openrouter_session_costs():
    pages = [load("openrouter/session_cost.json")]
    records = openrouter_datasets.parse_session_costs(pages, {})
    assert records
    assert records[0].metric == "session_cost"
    assert records[0].cost_per_task is not None


# -- Artificial Analysis ----------------------------------------------------------------------


def test_artificial_analysis_parsing():
    records = artificial_analysis.parse_models(load("artificial_analysis/models.json"))
    assert {r.metric for r in records} & {"intelligence_index", "coding_index", "agentic_index"}
    for record in records:
        assert record.model_ids


def test_artificial_analysis_rejects_junk():
    with pytest.raises(FetchError):
        artificial_analysis.parse_models({"nope": True})


# -- Matching ---------------------------------------------------------------------------------


def test_matcher_resolves_common_names():
    catalog = ModelsDevCatalog(
        {
            "openrouter": {
                "id": "openrouter",
                "models": {
                    "anthropic/claude-opus-5.5": {
                        "id": "anthropic/claude-opus-5.5",
                        "canonical_model_id": "anthropic/claude-opus-5-5",
                    },
                },
            },
            "anthropic": {
                "id": "anthropic",
                "models": {
                    "claude-opus-5-5": {
                        "id": "claude-opus-5-5",
                        "name": "Claude Opus 5.5",
                        "canonical_model_id": "anthropic/claude-opus-5-5",
                    },
                },
            },
        }
    )
    matcher = Matcher(catalog)
    assert matcher.match("arena", "Claude Opus 5.5 (Max)", [])[0] == "anthropic/claude-opus-5-5"
    assert matcher.match("terminal-bench", "anthropic/claude-opus-5-5", [])[0] == "anthropic/claude-opus-5-5"
    assert matcher.match("x", "totally-unknown-model", [])[0] is None


def test_split_effort_variants():
    assert split_effort("Claude Opus 5.5 (Max)") == ("Claude Opus 5.5", "max")
    assert split_effort("gpt-6_xhigh") == ("gpt-6", "xhigh")
    assert split_effort("plain-model") == ("plain-model", "")


def _catalog(*models: tuple[str, ...]) -> ModelsDevCatalog:
    """One provider listing (canonical id, name[, provider model id]) in the order given."""
    listed = {}
    for cid, name, *mid in models:
        model_id = mid[0] if mid else cid.split("/", 1)[1]
        listed[model_id] = {"id": model_id, "name": name, "canonical_model_id": cid}
    return ModelsDevCatalog({"vendor": {"id": "vendor", "models": listed}})


@pytest.mark.parametrize("order", [1, -1], ids=["listed", "reversed"])
def test_matcher_keeps_dated_snapshots_apart(order):
    june = ("anthropic/claude-3-5-sonnet-20240620", "Claude Sonnet 3.5")
    october = ("anthropic/claude-3-5-sonnet-20241022", "Claude Sonnet 3.5 v2")
    flash = ("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash")
    flash_1017 = ("deepseek/deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017")
    matcher = Matcher(_catalog(*[june, october, flash, flash_1017][::order]))

    def match(name: str) -> str | None:
        return matcher.match("arena", name, [])[0]

    assert match("claude-3-5-sonnet-20241022") == october[0]
    assert match("claude-3-5-sonnet-20240620") == june[0]
    # Undated, the name fits both snapshots equally, so neither may claim it.
    assert match("claude-3-5-sonnet") is None
    assert match("Claude 3.5 Sonnet") is None
    assert match("Claude Sonnet 3.5") == june[0]
    # A full date reaches a short-dated release for that day, otherwise the undated model.
    assert match("DeepSeek-V4.1-Flash-20261017") == flash_1017[0]
    assert match("DeepSeek-V4.1-Flash-20260101") == flash[0]


def _aa_model(name: str, slug: str, creator: str) -> dict:
    return {
        "name": name,
        "slug": slug,
        "model_creator": {"slug": creator},
        "evaluations": {"artificial_analysis_intelligence_index": 50},
    }


def test_matcher_strips_effort_words_only_when_the_full_name_fails():
    matcher = Matcher(
        _catalog(
            ("moonshotai/kimi-k2", "Kimi K2"),
            ("moonshotai/kimi-k2-thinking", "Kimi K2 Thinking"),
            ("alibaba/qwen3", "Qwen3"),
            ("alibaba/qwen3-max", "Qwen3 Max"),
            ("openai/gpt-6", "GPT-6"),
        )
    )
    kimi, qwen, gpt = artificial_analysis.parse_models(
        {
            "data": [
                _aa_model("Kimi K2 Thinking", "kimi-k2-thinking", "moonshotai"),
                _aa_model("Qwen3 Max", "qwen3-max", "alibaba"),
                _aa_model("GPT-6 (high)", "gpt-6-high", "openai"),
            ]
        }
    )
    assert "qwen3" not in qwen.model_ids and "alibaba/qwen3" not in qwen.model_ids
    for rec, expected in [(kimi, "moonshotai/kimi-k2-thinking"), (qwen, "alibaba/qwen3-max"), (gpt, "openai/gpt-6")]:
        assert matcher.match("artificial-analysis", rec.source_model, rec.model_ids, rec.fallback_ids)[0] == expected

    # With no catalogue entry for the full name, the stripped slug is still a fallback.
    only_k2 = Matcher(_catalog(("moonshotai/kimi-k2", "Kimi K2")))
    assert only_k2.match("artificial-analysis", kimi.source_model, kimi.model_ids, kimi.fallback_ids) == (
        "moonshotai/kimi-k2",
        "id",
    )


def test_a_full_dated_name_needs_no_curated_alias():
    matcher = Matcher(_catalog(("openai/gpt-4o", "GPT-4o"), ("openai/gpt-4o-2024-11-20", "GPT-4o (2024-11-20)")))
    assert matcher.match("epoch", "gpt-4o-2024-11-20") == ("openai/gpt-4o-2024-11-20", "name")


CURATED_ARENA = {
    "Gemini 2.5 Flash-Lite Preview (Non-Thinking, 09-2025)": "google/gemini-2.5-flash-lite-preview-09-2025",
    "Gemini 2.5 Flash-Lite Preview (Thinking, 06-17)": "google/gemini-2.5-flash-lite-preview-06-17",
    "Kimi K2 Preview (0711)": "moonshotai/kimi-k2",
    "Kimi K2 Preview (0905)": "moonshotai/kimi-k2-0905",
    "Mistral Large 2 (2407)": "mistral/mistral-large-2407",
    "Mixtral 8x22B Instruct v0.1": "mistral/mixtral-8x22b-instruct",
    "Llama 3.1 Nemotron Ultra 253B v1": "nvidia/llama-3.1-nemotron-ultra-253b",
}


def test_curated_arena_aliases_reach_their_catalogue_ids():
    matcher = Matcher(_catalog(*[(cid, cid.split("/", 1)[1]) for cid in CURATED_ARENA.values()]))
    for name, cid in CURATED_ARENA.items():
        assert matcher.match("arena", name) == (cid, "curated"), name
        assert matcher.match("arena", name.lower()) == (cid, "curated"), name


def test_curated_aliases_name_a_known_source_and_a_creator_slash_model():
    for source, aliases in curated_aliases().items():
        assert source in SOURCES, source
        for name, target in aliases.items():
            assert target is None or target.count("/") == 1, (source, name, target)


# -- Refresh ----------------------------------------------------------------------------------


@pytest.fixture
def database():
    init_db()


def test_persist_scores_keeps_effort_like_names_and_skips_non_finite_values(database):
    matcher = Matcher(
        _catalog(
            ("mistral/mistral", "Mistral 7B", "mistral"),
            ("mistral/mistral-medium", "Mistral Medium", "mistral-medium-latest"),
            ("openai/gpt-6", "GPT-6"),
        )
    )
    records = [
        ScoreRecord(metric="coding", source_model="mistral-medium", value=60.0, model_ids=["mistral-medium"]),
        ScoreRecord(metric="coding", source_model="gpt-6-high", value=80.0, model_ids=["gpt-6-high"]),
        ScoreRecord(metric="coding", source_model="broken-row", value=math.nan, model_ids=["broken-row"]),
    ]
    assert jobs.persist_scores("livebench", records, matcher) == 2
    with session_scope() as s:
        rows = s.scalars(select(BenchmarkScore).where(BenchmarkScore.source == "livebench"))
        stored = {r.source_model: (r.canonical_id, r.effort) for r in rows}
    assert stored == {"mistral-medium": ("mistral/mistral-medium", ""), "gpt-6-high": ("openai/gpt-6", "high")}


def _fake_source(source_id: str, delay: float) -> type[BenchmarkSource]:
    class Fake(BenchmarkSource):
        spec = SourceSpec(
            id=source_id,
            name=source_id.title(),
            homepage="https://example.com",
            description="",
            license="test",
            attribution="test",
            metrics=(MetricSpec("score", "Score", "coding"),),
        )

        async def fetch(self) -> list[ScoreRecord]:
            await asyncio.sleep(delay)
            return [ScoreRecord(metric="score", source_model=f"{source_id}-model", value=1.0)]

    return Fake


async def test_benchmark_refresh_waits_for_every_source_when_one_fails(database, monkeypatch):
    persisted: list[str] = []
    real_persist = jobs.persist_scores

    def persist(source_id: str, records: list[ScoreRecord], matcher: Matcher, **options: bool) -> int:
        if source_id == "broken":
            raise RuntimeError("database is locked")
        count = real_persist(source_id, records, matcher, **options)
        persisted.append(source_id)
        return count

    async def load_catalog(force: bool = False) -> ModelsDevCatalog:
        return _catalog()

    monkeypatch.setattr(events, "_loop", None)
    monkeypatch.setattr(jobs, "SOURCES", {"broken": _fake_source("broken", 0), "slow": _fake_source("slow", 0.05)})
    monkeypatch.setattr(jobs, "persist_scores", persist)
    monkeypatch.setattr(jobs, "load_catalog", load_catalog)

    await jobs.run_job("benchmarks")

    # The slow source finished writing before the job (and its lock) let go.
    assert persisted == ["slow"]
    with session_scope() as s:
        run = s.scalars(
            select(RefreshRun).where(RefreshRun.kind == "benchmarks").order_by(RefreshRun.id.desc())
        ).first()
        assert run is not None
        assert run.status == "partial"
        assert run.message == "Broken: database is locked"
        assert run.stats["ok"] == 1 and run.stats["failed"] == 1


# -- A refresh that only partly worked --------------------------------------------------------


def _two_metric_source(source_id: str, state: dict) -> type[BenchmarkSource]:
    class Partial(BenchmarkSource):
        spec = SourceSpec(
            id=source_id,
            name=source_id.title(),
            homepage="https://example.com",
            description="",
            license="test",
            attribution="test",
            metrics=(MetricSpec("a", "A", "coding"), MetricSpec("b", "B", "agentic")),
        )

        async def fetch(self) -> list[ScoreRecord]:
            self.partial_errors = list(state["errors"])
            return [ScoreRecord(metric=m, source_model="model", value=state["value"]) for m in state["metrics"]]

    return Partial


def _stored_metrics(source_id: str) -> dict[str, float]:
    with session_scope() as s:
        rows = s.scalars(select(BenchmarkScore).where(BenchmarkScore.source == source_id))
        return {r.metric: r.value for r in rows}


async def test_a_leaderboard_that_failed_to_load_keeps_its_stored_scores(database, monkeypatch):
    state = {"metrics": ["a", "b"], "errors": [], "value": 1.0}

    async def load_catalog(force: bool = False) -> ModelsDevCatalog:
        return _catalog()

    monkeypatch.setattr(events, "_loop", None)
    monkeypatch.setattr(jobs, "SOURCES", {"halfway": _two_metric_source("halfway", state)})
    monkeypatch.setattr(jobs, "load_catalog", load_catalog)

    await jobs.run_job("benchmarks")
    assert _stored_metrics("halfway") == {"a": 1.0, "b": 1.0}

    # "b" fails to load: "a" is replaced, "b" is kept, and the run says it was partial.
    state.update(metrics=["a"], errors=["b: HTTP 500"], value=2.0)
    await jobs.run_job("benchmarks")
    assert _stored_metrics("halfway") == {"a": 2.0, "b": 1.0}
    with session_scope() as s:
        run = s.scalars(
            select(RefreshRun).where(RefreshRun.kind == "benchmarks").order_by(RefreshRun.id.desc())
        ).first()
        assert run is not None
        assert run.status == "partial"
        assert "Halfway: partial" in run.message and "b: HTTP 500" in run.message
        assert run.stats["ok"] == 1 and run.stats["failed"] == 0 and run.stats["partial"] == 1

    # A complete fetch that no longer has "b" is the source dropping it, so its rows go.
    state.update(errors=[], value=3.0)
    await jobs.run_job("benchmarks")
    assert _stored_metrics("halfway") == {"a": 3.0}


async def test_arena_reports_the_subsets_it_could_not_load(monkeypatch):
    async def fetch_rows(config: str, categories: set[str]) -> list[dict]:
        if config != "text_style_control":
            raise FetchError("GET https://datasets-server.huggingface.co/filter: HTTP 500")
        return load("arena/text_style_control.json")

    monkeypatch.setattr(arena, "_fetch_with_warmup", fetch_rows)
    from vibonomics.benchmarks.base import BenchContext
    from vibonomics.settings_store import AppSettings

    source = arena.ArenaSource(BenchContext(catalog=_catalog(), settings=AppSettings()))
    records = await source.fetch()
    assert {r.metric for r in records} <= {"text", "text_coding"}
    assert len(source.partial_errors) == 2

    monkeypatch.setattr(
        arena, "_fetch_with_warmup", lambda config, categories: asyncio.sleep(0, load(f"arena/{config}.json"))
    )
    complete = arena.ArenaSource(BenchContext(catalog=_catalog(), settings=AppSettings()))
    await complete.fetch()
    assert complete.partial_errors == []


# -- Aliases edited while a refresh runs ------------------------------------------------------


def _alias(source: str, name: str, canonical_id: str) -> None:
    with session_scope() as s:
        s.add(ModelAlias(source=source, source_model=name, canonical_id=canonical_id))


def _drop_alias(source: str, name: str) -> None:
    with session_scope() as s:
        for alias in s.scalars(select(ModelAlias).where(ModelAlias.source == source, ModelAlias.source_model == name)):
            s.delete(alias)


def test_an_alias_saved_after_a_refresh_started_is_applied_when_its_source_is_stored(database):
    matcher_built_at_job_start = Matcher(_catalog(("acme/m1", "M1")))
    _alias("livebench", "Mystery Model", "acme/m1")
    try:
        jobs.persist_scores(
            "livebench",
            [ScoreRecord(metric="coding", source_model="Mystery Model", value=1.0)],
            matcher_built_at_job_start,
        )
        with session_scope() as s:
            row = s.scalars(select(BenchmarkScore).where(BenchmarkScore.source == "livebench")).one()
            assert (row.canonical_id, row.match_method) == ("acme/m1", "operator")
    finally:
        _drop_alias("livebench", "Mystery Model")


async def test_a_rematch_cannot_hand_one_row_the_match_computed_for_another(database, monkeypatch):
    catalog = _catalog(("acme/m1", "M1"))
    refresh_matcher = Matcher(catalog)
    mystery = ScoreRecord(metric="coding", source_model="Mystery Model", value=1.0)
    jobs.persist_scores("livebench", [mystery], refresh_matcher)
    _alias("livebench", "Mystery Model", "acme/m1")

    async def load_catalog(force: bool = False) -> ModelsDevCatalog:
        return catalog

    monkeypatch.setattr(jobs, "load_catalog", load_catalog)
    real_match = Matcher.match
    refreshed: list[int] = []

    def match_then_a_refresh_lands(self, *args, **kwargs):
        if not refreshed:
            refreshed.append(1)
            # The refresh replaces the source's rows after the rematch read them and before it writes.
            new = ScoreRecord(metric="coding", source_model="Brand New Model", value=2.0)
            jobs.persist_scores("livebench", [new, mystery], refresh_matcher)
        return real_match(self, *args, **kwargs)

    monkeypatch.setattr(Matcher, "match", match_then_a_refresh_lands)
    try:
        await jobs.rematch_scores()
        with session_scope() as s:
            stored = {
                r.source_model: (r.canonical_id, r.match_method)
                for r in s.scalars(select(BenchmarkScore).where(BenchmarkScore.source == "livebench"))
            }
        assert stored["Mystery Model"] == ("acme/m1", "operator")
        assert stored["Brand New Model"] == (None, "unmatched")
    finally:
        _drop_alias("livebench", "Mystery Model")
