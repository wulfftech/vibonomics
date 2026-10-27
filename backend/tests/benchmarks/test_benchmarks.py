"""Benchmark source parsers against captured fixtures (no network)."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from vibonomics.benchmarks import arena, artificial_analysis, epoch, livebench, openrouter_datasets, terminal_bench
from vibonomics.benchmarks.matching import Matcher, split_effort
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.http import FetchError

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
