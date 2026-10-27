"""Artificial Analysis free data API (https://artificialanalysis.ai/documentation).

``GET /api/v2/data/llms/models`` with an ``x-api-key`` header returns every model with its indices,
prices and speed. The free tier allows 1,000 requests a day; we make one per refresh. Off by
default: the data comes with terms of use that restrict product use (see ``AA_TERMS``).

AA's stable identifier is the model UUID ``id``; ``slug`` and ``name`` can change. Names carry the
reasoning mode ("GPT-6 (high)", "Claude Opus 5.5 (Max)"), which becomes ``effort``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..http import FetchError, get_json
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec
from .matching import effort_word, split_qualifiers
from .openrouter_aa import AA_TERMS

URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
# metric id → keys that have carried it in ``evaluations`` (first present wins)
_METRICS: dict[str, tuple[str, ...]] = {
    "intelligence_index": ("artificial_analysis_intelligence_index", "intelligence_index"),
    "coding_index": ("artificial_analysis_coding_index", "coding_index"),
    "agentic_index": ("artificial_analysis_agentic_index", "agentic_index"),
}
_SLUG_EFFORTS = ("non-reasoning", "xhigh", "max", "high", "medium", "low", "minimal", "reasoning", "thinking")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slug_base(slug: str) -> tuple[str, str]:
    """'gpt-6-high' → ('gpt-6', 'high'); slugs without an effort suffix come back unchanged."""
    for word in _SLUG_EFFORTS:
        if slug.endswith("-" + word):
            return slug[: -len(word) - 1], effort_word(word) or word
    return slug, ""


def parse_models(data: Any) -> list[ScoreRecord]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise FetchError(f"{URL}: unexpected response")
    today = datetime.now(UTC).date().isoformat()
    records: list[ScoreRecord] = []
    for m in data["data"]:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("slug")
        evals = m.get("evaluations") if isinstance(m.get("evaluations"), dict) else {}
        if not isinstance(name, str) or not name or not evals:
            continue
        base, effort, _ = split_qualifiers(name)
        slug = str(m.get("slug") or "")
        slug_base, slug_effort = _slug_base(slug) if slug else ("", "")
        creator = m.get("model_creator") if isinstance(m.get("model_creator"), dict) else {}
        hints = [base, slug_base]
        if creator.get("slug") and slug_base:
            hints.insert(0, f"{creator['slug']}/{slug_base}")
        pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
        for metric, keys in _METRICS.items():
            value = next((v for v in (_num(evals.get(k)) for k in keys) if v is not None), None)
            if value is None:
                continue
            records.append(
                ScoreRecord(
                    metric=metric,
                    source_model=name,
                    value=round(value, 2),
                    effort=effort or slug_effort,
                    model_ids=[h for h in hints if h],
                    as_of=today,
                    url=f"https://artificialanalysis.ai/models/{slug}" if slug else "https://artificialanalysis.ai",
                    extra={
                        "aa_id": m.get("id"),
                        "slug": slug or None,
                        "creator": creator.get("name"),
                        "price_blended_3_to_1": _num(pricing.get("price_1m_blended_3_to_1")),
                        "output_tokens_per_second": _num(m.get("median_output_tokens_per_second")),
                    },
                )
            )
    return records


class ArtificialAnalysisSource(BenchmarkSource):
    spec = SourceSpec(
        id="artificial-analysis",
        name="Artificial Analysis",
        homepage="https://artificialanalysis.ai",
        description="Artificial Analysis Intelligence, Coding and Agentic indices straight from the AA data API "
        "(free key, 1,000 requests/day). Overlaps with 'Artificial Analysis (via OpenRouter)'; enable one.",
        license="Artificial Analysis terms of use",
        attribution="Data: Artificial Analysis (artificialanalysis.ai)",
        default_enabled=False,
        requires_key=True,
        key_label="Artificial Analysis API key",
        key_url="https://artificialanalysis.ai/documentation",
        terms_warning=AA_TERMS,
        metrics=(
            MetricSpec("intelligence_index", "AA Intelligence Index", "intelligence", weight=1.5),
            MetricSpec("coding_index", "AA Coding Index", "coding", weight=1.5),
            MetricSpec("agentic_index", "AA Agentic Index", "agentic", weight=1.5),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        if not self.ctx.api_key:
            raise FetchError("Artificial Analysis: an API key is required")
        data = await get_json(URL, headers={"x-api-key": self.ctx.api_key}, timeout=60)
        records = parse_models(data)
        if not records and data.get("data"):
            raise FetchError(f"{URL}: no index scores found in {len(data['data'])} models (schema change?)")
        return records
