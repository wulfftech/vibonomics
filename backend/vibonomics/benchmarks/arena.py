"""Arena (formerly LMArena) leaderboards from the Hugging Face dataset ``lmarena-ai/leaderboard-dataset``.

CC BY 4.0. Read through the Hugging Face datasets-server JSON API so we never download parquet.
Subsets used (``latest`` split): ``text_style_control`` (the arena's default text view since May 2025)
for overall and coding ratings, ``webdev`` (Code Arena) and ``agent`` (Agent Arena IPS scores).

The text subset holds ~29 categories x ~400 models, sorted by category. ``/rows`` is paged and we
stop once both wanted categories have been seen and a page contains neither (the ``/filter``
endpoint would be ideal but times out on cold indexes).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..http import FetchError, get_json
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec
from .matching import split_qualifiers

log = logging.getLogger(__name__)

DATASET = "lmarena-ai/leaderboard-dataset"
FILTER_URL = "https://datasets-server.huggingface.co/filter"
PAGE = 100
MAX_ROWS = 1_000
LEADERBOARD_URL = "https://arena.ai/leaderboard"

# (config, category, metric id, value column)
_WANTED: dict[str, list[tuple[str, str, str]]] = {
    "text_style_control": [("overall", "text", "rating"), ("coding", "text_coding", "rating")],
    "webdev": [("overall", "webdev", "rating")],
    "agent": [("overall", "agent", "score")],
}
_PAGES = {"text": "text", "text_coding": "text", "webdev": "webdev", "agent": "agent"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _filter_page(config: str, category: str, offset: int) -> tuple[list[dict[str, Any]], int | None]:
    params = {
        "dataset": DATASET,
        "config": config,
        "split": "latest",
        "where": f"\"category\"='{category}'",
        "offset": offset,
        "length": PAGE,
    }
    data = await get_json(FILTER_URL, params=params, timeout=90)
    if not isinstance(data, dict):
        raise FetchError(f"{FILTER_URL} ({config}): unexpected response")
    rows = [r.get("row") for r in data.get("rows") or [] if isinstance(r, dict)]
    total = data.get("num_rows_total")
    return [r for r in rows if isinstance(r, dict)], total if isinstance(total, int) else None


async def fetch_rows(config: str, categories: set[str], max_rows: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Rows of one subset's ``latest`` split whose ``category`` is in ``categories``.

    Uses the datasets-server ``/filter`` endpoint (DuckDB over the parquet files), which returns only
    matching rows: a handful of requests per subset instead of paging through thousands of rows.
    """
    out: list[dict[str, Any]] = []
    for category in sorted(categories):
        rows, total = await _filter_page(config, category, 0)
        out.extend(rows)
        offset = len(rows)
        limit = min(max_rows, total if total is not None else max_rows)
        while len(rows) == PAGE and offset < limit:
            rows, _ = await _filter_page(config, category, offset)
            out.extend(rows)
            offset += len(rows)
    return out


async def _fetch_with_warmup(config: str, categories: set[str]) -> list[dict[str, Any]]:
    """The filter index is built lazily; a cold index answers 500 for a minute or so."""
    last: FetchError | None = None
    for attempt in range(3):
        try:
            return await fetch_rows(config, categories)
        except FetchError as exc:
            last = exc
            message = str(exc)
            if "index is loading" in message or "Authentication check" in message or " timed out" in message:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            raise
    if last is not None:
        raise last
    return []


_SNAPSHOT = re.compile(r"^(\d{4}|\d{8}|\d{4}-\d{2}-\d{2})$")


def name_hints(name: str) -> tuple[list[str], str, list[str]]:
    """Matching hints, effort and leftover qualifiers for an Arena display name.

    'Qwen3.8-Max (0902)' → (['Qwen3.8-Max-0902', 'Qwen3.8-Max'], '', ['0902']);
    'DeepSeek-V4-Pro (High, 20260813)' → (['DeepSeek-V4-Pro-0813', 'DeepSeek-V4-Pro-20260813', 'DeepSeek-V4-Pro'],
    'high', ['20260813']) so a snapshot the catalogue lists by month-day wins over the undated model.
    """
    base, effort, qualifiers = split_qualifiers(name)
    dated: list[str] = []
    for q in qualifiers:
        if _SNAPSHOT.match(q):
            digits = q.replace("-", "")
            if len(digits) == 8:
                dated.append(f"{base}-{digits[4:]}")
            dated.append(f"{base}-{q}")
    return [*dated, base], effort, qualifiers


def rows_to_records(config: str, rows: list[dict[str, Any]]) -> list[ScoreRecord]:
    records: list[ScoreRecord] = []
    # ``latest`` should hold one publish date, but keep only the newest if several slip in.
    dates = sorted({str(r.get("leaderboard_publish_date") or "") for r in rows})
    newest = dates[-1] if dates else ""
    for category, metric, column in _WANTED[config]:
        for row in rows:
            if row.get("category") != category or str(row.get("leaderboard_publish_date") or "") != newest:
                continue
            name = row.get("model_name")
            value = _num(row.get(column))
            if not isinstance(name, str) or not name.strip() or value is None:
                continue
            hints, effort, qualifiers = name_hints(name)
            lower = _num(row.get("rating_lower") if column == "rating" else row.get("score_ci_lower"))
            upper = _num(row.get("rating_upper") if column == "rating" else row.get("score_ci_upper"))
            records.append(
                ScoreRecord(
                    metric=metric,
                    source_model=name.strip(),
                    value=round(value, 4) if column == "score" else round(value, 1),
                    effort=effort,
                    model_ids=hints,
                    as_of=newest or None,
                    url=f"{LEADERBOARD_URL}/{_PAGES[metric]}",
                    extra={
                        "organization": row.get("organization"),
                        "rank": _num(row.get("rank")),
                        "ci": [lower, upper] if lower is not None and upper is not None else None,
                        "votes": _num(row.get("vote_count") if column == "rating" else row.get("session_count")),
                        "qualifiers": qualifiers or None,
                    },
                )
            )
    return records


class ArenaSource(BenchmarkSource):
    spec = SourceSpec(
        id="arena",
        name="Arena (LMArena)",
        homepage=LEADERBOARD_URL,
        description="Crowd-sourced head-to-head human preference ratings: Text (style controlled, overall and "
        "coding), Code Arena (WebDev) and Agent Arena.",
        license="CC BY 4.0",
        attribution="Data: Arena (arena.ai), lmarena-ai/leaderboard-dataset on Hugging Face, CC BY 4.0",
        default_enabled=True,
        metrics=(
            MetricSpec(
                "text",
                "Arena Text rating",
                "intelligence",
                unit="Elo",
                description="Overall text arena rating with style control (Bradley-Terry, Elo-like scale).",
            ),
            MetricSpec(
                "text_coding",
                "Arena Text coding rating",
                "coding",
                weight=0.75,
                unit="Elo",
                description="Text arena rating on coding prompts (chat answers, not agentic).",
            ),
            MetricSpec(
                "webdev",
                "Code Arena (WebDev) rating",
                "coding",
                unit="Elo",
                description="Human votes on web apps built by the model.",
            ),
            MetricSpec(
                "agent",
                "Agent Arena score",
                "agentic",
                description="Inverse-propensity-weighted success signal from real agent sessions.",
            ),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        records: list[ScoreRecord] = []
        errors: list[str] = []
        for config in _WANTED:
            categories = {category for category, _, _ in _WANTED[config]}
            try:
                rows = await _fetch_with_warmup(config, categories)
            except FetchError as exc:
                log.warning("Arena subset %s skipped: %s", config, exc)
                errors.append(str(exc))
                continue
            records.extend(rows_to_records(config, rows))
        if not records:
            raise FetchError("Arena: no leaderboard rows found" + (f" ({'; '.join(errors)[:300]})" if errors else ""))
        self.partial_errors = errors
        return records
