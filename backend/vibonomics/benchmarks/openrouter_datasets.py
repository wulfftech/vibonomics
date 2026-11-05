"""OpenRouter's own evaluations and public usage datasets (any valid OpenRouter API key).

- ``GET /api/v1/benchmarks?source=openrouter``: OpenRouter-run GPQA Diamond and tau-bench (airline)
  with average cost per task. The endpoint also aggregates Artificial Analysis and Design Arena
  data; items from those sources are ignored here (see ``openrouter_aa.py``).
- ``GET /api/v1/datasets/session-cost``: median USD spent per session, by agent harness and model,
  CC BY 4.0. Stored as a cost-only metric (weight 0) so it shows up as a reported cost per task
  without moving any score.

Usage-share rankings are deliberately not used: popularity tracks price and defaults as much as
quality. Model ``permaslug``s are OpenRouter canonical slugs (``anthropic/claude-4.8-opus``), so we
also read the public /models list to translate them into the ids models.dev knows.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from statistics import median
from typing import Any

from ..http import FetchError, get_json
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec

log = logging.getLogger(__name__)

API = "https://openrouter.ai/api/v1"
BENCHMARKS_URL = f"{API}/benchmarks"
SESSION_COST_URL = f"{API}/datasets/session-cost"
MODELS_URL = f"{API}/models"
RANKINGS_URL = "https://openrouter.ai/rankings"
SESSION_TURNS = "10-49-turns"
PAGE = 500
MAX_OFFSET = 5000
_BENCHMARKS = {
    # benchmark_type → metric id
    "gpqa_diamond": "gpqa_diamond",
    "tau_bench_verified_airline": "tau_bench_airline",
}
# Metrics whose "task" is a multi-step agent task. A GPQA question costs a fraction of a cent, so
# its cost stays in ``extra`` rather than dragging down the model's reported cost per task.
_TASK_COST_METRICS = {"tau_bench_airline"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def citation(as_of: str | None) -> str:
    return f"Source: OpenRouter (openrouter.ai/rankings), as of {as_of or 'unknown date'}"


_SLUG_DATE = re.compile(r"-20\d{6}$")


def slug_map(models: Any) -> dict[str, str]:
    """OpenRouter canonical slug → model id ('anthropic/claude-4.8-opus-20260528' → 'anthropic/claude-opus-4.8').

    The date-less slug is mapped too when only one model has it.
    """
    exact: dict[str, str] = {}
    undated: dict[str, set[str]] = {}
    for m in models.get("data", []) if isinstance(models, dict) else []:
        if not isinstance(m, dict) or not m.get("canonical_slug") or not m.get("id"):
            continue
        slug, mid = str(m["canonical_slug"]).lower(), str(m["id"]).split(":")[0]
        exact[slug] = mid
        undated.setdefault(_SLUG_DATE.sub("", slug), set()).add(mid)
    for slug, ids in undated.items():
        if len(ids) == 1:
            exact.setdefault(slug, next(iter(ids)))
    return exact


async def _slug_map() -> dict[str, str]:
    try:
        return slug_map(await get_json(MODELS_URL, timeout=60))
    except FetchError as exc:
        log.warning("OpenRouter model list unavailable for slug mapping: %s", exc)
        return {}


def _hints(slug: str, slugs: dict[str, str], *extra: str | None) -> list[str]:
    out: list[str] = []
    for h in (slugs.get(slug.lower(), ""), slug, *extra):
        if h and h.lower() not in {o.lower() for o in out}:
            out.append(h)
    return out


def parse_benchmarks(data: Any, slugs: dict[str, str]) -> list[ScoreRecord]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise FetchError(f"{BENCHMARKS_URL}: unexpected response")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    as_of = str(meta.get("as_of") or "")[:10] or None
    records: list[ScoreRecord] = []
    for item in data["data"]:
        if not isinstance(item, dict) or item.get("source") != "openrouter":
            continue
        metric = _BENCHMARKS.get(str(item.get("benchmark_type") or ""))
        slug = item.get("model_permaslug")
        accuracy = _num(item.get("accuracy"))
        if not metric or not isinstance(slug, str) or not slug or accuracy is None:
            continue
        cost = _num(item.get("avg_cost_per_task"))
        cost = cost if cost is not None and cost >= 0 else None
        records.append(
            ScoreRecord(
                metric=metric,
                source_model=slug,
                value=round(accuracy * 100 if accuracy <= 1 else accuracy, 2),
                model_ids=_hints(slug, slugs, item.get("display_name")),
                cost_per_task=cost if metric in _TASK_COST_METRICS else None,
                as_of=str(item.get("last_run_timestamp") or "")[:10] or as_of,
                url="https://openrouter.ai/benchmarks",
                extra={
                    "display_name": item.get("display_name"),
                    "stddev": _num(item.get("accuracy_stddev")),
                    "tasks": item.get("total_tasks"),
                    "avg_cost_per_question": cost if metric not in _TASK_COST_METRICS else None,
                    "citation": meta.get("citation") or citation(as_of),
                },
            )
        )
    if not records and data["data"]:
        # The endpoint also lists other sources' items, so rows with none of ours means the layout changed.
        raise FetchError(
            f"{BENCHMARKS_URL}: no OpenRouter-run benchmarks in {len(data['data'])} items (schema change?)"
        )
    return records


def parse_session_costs(pages: list[Any], slugs: dict[str, str]) -> list[ScoreRecord]:
    """Median session cost per model: the median across harnesses of each harness's median."""
    cells: dict[str, list[tuple[str, float]]] = {}
    as_of = None
    rows = 0
    for data in pages:
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise FetchError(f"{SESSION_COST_URL}: unexpected response")
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        as_of = as_of or meta.get("window_end_date") or str(meta.get("as_of") or "")[:10] or None
        rows += len(data["data"])
        for cell in data["data"]:
            if not isinstance(cell, dict) or cell.get("turn_range") not in (None, SESSION_TURNS):
                continue
            slug = cell.get("model_permaslug")
            cost = _num(cell.get("median_session_cost_usd"))
            if isinstance(slug, str) and slug and cost is not None and cost >= 0:
                cells.setdefault(slug, []).append((str(cell.get("app_name") or cell.get("app_slug") or "?"), cost))
    records = []
    for slug, values in cells.items():
        value = round(median(c for _, c in values), 4)
        records.append(
            ScoreRecord(
                metric="session_cost",
                source_model=slug,
                value=value,
                model_ids=_hints(slug, slugs),
                cost_per_task=value,
                as_of=as_of,
                url=RANKINGS_URL,
                extra={
                    "turn_range": SESSION_TURNS,
                    "harnesses": {name: cost for name, cost in sorted(values)},
                    "citation": citation(as_of),
                },
            )
        )
    if not records and rows:
        raise FetchError(f"{SESSION_COST_URL}: no usable session costs in {rows} rows (schema change?)")
    return records


class OpenRouterDatasetsSource(BenchmarkSource):
    spec = SourceSpec(
        id="openrouter-datasets",
        name="OpenRouter evals & datasets",
        homepage=RANKINGS_URL,
        description="OpenRouter's own GPQA Diamond and tau-bench (airline) runs with cost per task, plus the "
        "median cost of a 10-49 turn agent session per model. Needs any OpenRouter API key.",
        license="CC BY 4.0 (datasets); OpenRouter evals: attribution required",
        attribution="Source: OpenRouter (openrouter.ai/rankings)",
        default_enabled=True,
        requires_key=True,
        key_label="OpenRouter API key",
        key_url="https://openrouter.ai/settings/keys",
        metrics=(
            MetricSpec(
                "tau_bench_airline",
                "tau-bench airline (OpenRouter)",
                "agentic",
                weight=0.75,
                unit="%",
                description="Tool-calling customer-service agent tasks, airline domain only.",
            ),
            MetricSpec(
                "gpqa_diamond",
                "GPQA Diamond (OpenRouter)",
                "intelligence",
                weight=0.5,
                unit="%",
                description="Graduate-level science multiple choice; close to saturation.",
            ),
            MetricSpec(
                "session_cost",
                "Median agent session cost (OpenRouter)",
                "agentic",
                higher_is_better=False,
                weight=0.0,
                unit="USD",
                description="Median spend per 10-49 turn session across agent harnesses. Cost only; not scored.",
            ),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        if not self.ctx.api_key:
            raise FetchError("OpenRouter evals & datasets: an OpenRouter API key is required")
        headers = {"Authorization": f"Bearer {self.ctx.api_key}"}
        slugs = await _slug_map()
        records: list[ScoreRecord] = []
        errors: list[str] = []
        try:
            data = await get_json(BENCHMARKS_URL, headers=headers, params={"source": "openrouter"}, timeout=60)
            records.extend(parse_benchmarks(data, slugs))
        except FetchError as exc:
            errors.append(str(exc))
        try:
            pages: list[Any] = []
            offset = 0
            while offset <= MAX_OFFSET:
                params = {"turn_range": SESSION_TURNS, "limit": PAGE, "offset": offset}
                page = await get_json(SESSION_COST_URL, headers=headers, params=params, timeout=60)
                pages.append(page)
                n = len(page.get("data") or []) if isinstance(page, dict) else 0
                if n < PAGE:
                    break
                offset += n
            records.extend(parse_session_costs(pages, slugs))
        except FetchError as exc:
            errors.append(str(exc))
        if errors and not records:
            raise FetchError("; ".join(errors)[:600])
        for err in errors:
            log.warning("OpenRouter evals & datasets partially failed: %s", err)
        self.partial_errors = errors
        today = datetime.now(UTC).date().isoformat()
        for rec in records:
            rec.as_of = rec.as_of or today
        return records
