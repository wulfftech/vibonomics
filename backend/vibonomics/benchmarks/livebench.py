"""LiveBench 2.0 (https://livebench.ai), long-horizon agentic and reasoning suites.

The site is a static app: each release is ``data/<YYYYMMDD>/{table.csv, categories.json, cost.csv}``.
Release ids are only listed in the JavaScript bundle, so we read the index page, follow its
``main.*.js`` and look for array literals of 8-digit dates (``["20261014"]``). The preview release
is appended in code (``[...releases, "20261023"]``) rather than listed, so it is skipped naturally.
Candidates are tried newest first until a ``table.csv`` loads.

``table.csv`` has one 0-100 score per suite; ``cost.csv`` has the USD spent on each suite and the
number of tasks (``nq_<suite>``), so cost per task is ``cost[suite] / nq_<suite>``.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

from ..http import FetchError, get_text
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec

log = logging.getLogger(__name__)

SITE = "https://livebench.ai"
MAX_CANDIDATES = 4
_SCRIPT = re.compile(r"""<script[^>]+src=["']([^"']*main[^"']*\.js)["']""", re.I)
_DATE_ARRAY = re.compile(r"""\[\s*(?:["']20\d{6}["']\s*,\s*)*["']20\d{6}["']\s*\]""")
_DATE = re.compile(r"""["'](20\d{6})["']""")
# suite column → metric id
_SUITES = {"reasoning": "reasoning", "coding": "coding", "long_agentic": "long_agentic"}
_TASK_COST_SUITES = {"coding", "long_agentic"}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("n/a", "na", "-", "nan"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def release_candidates(bundle: str) -> list[str]:
    """Release ids from the JS bundle, newest first. Listed arrays win over loose date strings."""
    listed = {d for arr in _DATE_ARRAY.findall(bundle) for d in _DATE.findall(arr)}
    return sorted(listed, reverse=True)


def _csv(text: str) -> list[dict[str, str]]:
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def parse_release(release: str, table: str, cost: str | None) -> list[ScoreRecord]:
    as_of = f"{release[:4]}-{release[4:6]}-{release[6:8]}"
    costs = {r.get("model", ""): r for r in _csv(cost)} if cost else {}
    records: list[ScoreRecord] = []
    for row in _csv(table):
        model = row.get("model")
        if not model:
            continue
        c = costs.get(model, {})
        for column, metric in _SUITES.items():
            value = _num(row.get(column))
            if value is None:
                continue
            spent, tasks = _num(c.get(column)), _num(c.get(f"nq_{column}"))
            per_task = round(spent / tasks, 4) if spent is not None and tasks and spent > 0 else None
            # Reasoning questions cost cents; only the agentic suites' cost is a "task" cost.
            agentic = column in _TASK_COST_SUITES
            records.append(
                ScoreRecord(
                    metric=metric,
                    source_model=model,
                    value=value,
                    model_ids=[model],
                    cost_per_task=per_task if agentic else None,
                    as_of=as_of,
                    url=f"{SITE}/#/",
                    extra={
                        "release": release,
                        "tasks": tasks,
                        "cost_per_question": None if agentic else per_task,
                        "output_tokens_per_task": _num(c.get(f"out_{column}")),
                    },
                )
            )
    return records


class LiveBenchSource(BenchmarkSource):
    spec = SourceSpec(
        id="livebench",
        name="LiveBench",
        homepage=SITE,
        description="LiveBench 2.0: contamination-limited suites where models do real long-horizon work. Uses the "
        "Reasoning, Coding (bug fixes from real PRs) and Long Agentic suites, with cost per task.",
        license="Unspecified (code Apache-2.0)",
        attribution="Data: LiveBench (livebench.ai), sponsored by Abacus.AI",
        default_enabled=False,
        metrics=(
            MetricSpec(
                "reasoning",
                "LiveBench Reasoning",
                "intelligence",
                weight=0.75,
                unit="%",
                description="Short logic, spatial and theory-of-mind questions.",
            ),
            MetricSpec(
                "coding",
                "LiveBench Coding",
                "coding",
                weight=0.75,
                unit="%",
                description="Bug fixes from real merged pull requests; single run, ~30 tasks.",
            ),
            MetricSpec(
                "long_agentic",
                "LiveBench Long Agentic",
                "agentic",
                weight=0.75,
                unit="%",
                description="Hours-long multi-turn application work; single run, ~20-30 tasks.",
            ),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        index = await get_text(f"{SITE}/", timeout=60)
        m = _SCRIPT.search(index)
        if not m:
            raise FetchError("LiveBench: could not find the app bundle on livebench.ai")
        src = m.group(1)
        bundle_url = src if src.startswith("http") else f"{SITE}/{src.lstrip('./')}"
        candidates = release_candidates(await get_text(bundle_url, timeout=60))
        if not candidates:
            raise FetchError("LiveBench: no release ids found in the site bundle (site layout changed?)")
        errors = []
        for release in candidates[:MAX_CANDIDATES]:
            base = f"{SITE}/data/{release}"
            try:
                table = await get_text(f"{base}/table.csv", timeout=60, retries=1)
            except FetchError as exc:
                errors.append(str(exc))
                continue
            try:
                cost = await get_text(f"{base}/cost.csv", timeout=60, retries=1)
            except FetchError as exc:
                log.info("LiveBench %s has no cost.csv: %s", release, exc)
                cost = None
            records = parse_release(release, table, cost)
            if records:
                return records
            errors.append(f"{base}/table.csv: no usable rows")
        raise FetchError("LiveBench: no release could be loaded: " + "; ".join(errors)[:400])
