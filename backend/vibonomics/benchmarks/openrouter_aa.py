"""Artificial Analysis indices and Design Arena ratings as embedded in OpenRouter's public /models.

OpenRouter republishes these numbers, but they remain Artificial Analysis / Design Arena data, so
both sources are off by default and the AA one carries the same terms warning as the direct
connector.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..http import get_json
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec

AA_TERMS = (
    "Artificial Analysis data is subject to its terms of use, which restrict using it in products for model "
    "selection or comparison and require attribution. Enable this only for your own private use, and you "
    "take responsibility for complying with those terms."
)

_URL = "https://openrouter.ai/api/v1/models"


async def _models() -> list[dict]:
    data = await get_json(_URL, timeout=60)
    return data.get("data", []) if isinstance(data, dict) else []


class OpenRouterAASource(BenchmarkSource):
    spec = SourceSpec(
        id="openrouter-aa",
        name="Artificial Analysis (via OpenRouter)",
        homepage="https://artificialanalysis.ai",
        description="Artificial Analysis Intelligence, Coding and Agentic indices that OpenRouter embeds in its "
        "public model list. No key needed.",
        license="Artificial Analysis terms of use",
        attribution="Data: Artificial Analysis (artificialanalysis.ai), via OpenRouter",
        default_enabled=False,
        terms_warning=AA_TERMS,
        metrics=(
            MetricSpec("intelligence_index", "AA Intelligence Index", "intelligence", weight=1.5),
            MetricSpec("coding_index", "AA Coding Index", "coding", weight=1.5),
            MetricSpec("agentic_index", "AA Agentic Index", "agentic", weight=1.5),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        today = datetime.now(UTC).date().isoformat()
        records: list[ScoreRecord] = []
        for m in await _models():
            aa = (m.get("benchmarks") or {}).get("artificial_analysis") or {}
            mid = m.get("id") or ""
            base_id = mid.split(":")[0]
            for metric in ("intelligence_index", "coding_index", "agentic_index"):
                value = aa.get(metric)
                if value is None:
                    continue
                records.append(
                    ScoreRecord(
                        metric=metric,
                        source_model=base_id,
                        value=float(value),
                        model_ids=[base_id, m.get("canonical_slug") or ""],
                        as_of=today,
                        url="https://artificialanalysis.ai/models",
                    )
                )
        # :free and paid variants carry the same numbers; keep one row per model.
        unique = {(r.metric, r.source_model): r for r in records}
        return list(unique.values())


class OpenRouterDesignArenaSource(BenchmarkSource):
    spec = SourceSpec(
        id="openrouter-designarena",
        name="Design Arena (via OpenRouter)",
        homepage="https://www.designarena.ai",
        description="Design Arena Elo ratings for website and game generation, as embedded in OpenRouter's "
        "model list. Useful for front-end coding quality.",
        license="Unspecified (republished by OpenRouter)",
        attribution="Data: Design Arena (designarena.ai), via OpenRouter",
        default_enabled=False,
        metrics=(
            MetricSpec("website_elo", "Design Arena website Elo", "coding", weight=0.75, unit="Elo"),
            MetricSpec("gamedev_elo", "Design Arena game dev Elo", "coding", weight=0.5, unit="Elo"),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        today = datetime.now(UTC).date().isoformat()
        records: dict[tuple[str, str], ScoreRecord] = {}
        for m in await _models():
            base_id = (m.get("id") or "").split(":")[0]
            for entry in (m.get("benchmarks") or {}).get("design_arena") or []:
                category = entry.get("category")
                metric = {"website": "website_elo", "gamedev": "gamedev_elo"}.get(category or "")
                if not metric or entry.get("elo") is None:
                    continue
                records[(metric, base_id)] = ScoreRecord(
                    metric=metric,
                    source_model=base_id,
                    value=float(entry["elo"]),
                    model_ids=[base_id],
                    as_of=today,
                    url="https://www.designarena.ai/leaderboard",
                    extra={"rank": entry.get("rank"), "win_rate": entry.get("win_rate")},
                )
        return list(records.values())
