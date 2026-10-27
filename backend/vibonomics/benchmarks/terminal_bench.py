"""Terminal-Bench 4.0 (harbor-framework/terminal-bench, Apache-2.0).

The leaderboard is a directory of JSON files in the GitHub repo, one per submission (schema in
``leaderboard/leaderboard.yaml``). One GitHub API call lists the directory; the files themselves
come from raw.githubusercontent.com, which doesn't count against the 60 requests/hour limit for
unauthenticated API calls.

Each submission is a model *and* an agent harness (Claude Code, Codex, mini-swe-agent...), so the
same model can appear more than once. We keep the best accuracy per model and effort and record
the harness in ``extra``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..http import FetchError, get_json
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec

log = logging.getLogger(__name__)

REPO = "harbor-framework/terminal-bench"
BRANCH = "main"
DIRECTORY = "leaderboard/submissions"
LIST_URL = f"https://api.github.com/repos/{REPO}/contents/{DIRECTORY}"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{DIRECTORY}/{{name}}"
HTML_URL = f"https://github.com/{REPO}/blob/{BRANCH}/{DIRECTORY}/{{name}}"
MAX_FILES = 400
CONCURRENCY = 8
_FILE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("label") or None
    return value if isinstance(value, str) and value else None


def parse_submission(name: str, data: Any) -> ScoreRecord | None:
    """One submission file → one accuracy record, or None if it lacks the essentials."""
    if not isinstance(data, dict):
        return None
    sf = data.get("source_filter") or {}
    md = data.get("metadata") or {}
    metrics = data.get("metrics") or {}
    if not isinstance(sf, dict) or not isinstance(md, dict) or not isinstance(metrics, dict):
        return None
    model = sf.get("model_name")
    accuracy = _num(metrics.get("accuracy"))
    if not isinstance(model, str) or not model or accuracy is None:
        return None
    effort = sf.get("reasoning_effort") if "reasoning_effort" in sf else md.get("reasoning_effort")
    effort = str(effort).strip().lower() if effort else ""

    trials = _num(metrics.get("n_trials"))
    cost = _num(metrics.get("total_cost_usd"))
    tokens = _num(metrics.get("total_tokens"))
    per_task = trials if trials and trials > 0 else None
    # A zero total cost means the submitter didn't report it (free endpoints are rare at this tier).
    cost_per_task = round(cost / per_task, 4) if per_task and cost and cost > 0 else None
    tokens_per_task = round(tokens / per_task) if per_task and tokens and tokens > 0 else None

    m = _FILE_DATE.match(name)
    hints = [model]
    if "/" in model:
        hints.append(model.split("/", 1)[1])
    label = _label(md.get("model_display"))
    if label:
        hints.append(label)
    return ScoreRecord(
        metric="accuracy",
        source_model=model,
        value=round(accuracy, 2),
        effort=effort,
        model_ids=hints,
        cost_per_task=cost_per_task,
        tokens_per_task=tokens_per_task,
        as_of=m.group(1) if m else (md.get("date") if isinstance(md.get("date"), str) else None),
        url=HTML_URL.format(name=name),
        extra={
            "agent": _label(md.get("agent_display")) or sf.get("agent"),
            "agent_version": sf.get("agent_version"),
            "model_label": label,
            "model_org": _label(md.get("model_org")),
            "ci95_half_width": _num(metrics.get("accuracy_ci95_half_width")),
            "n_trials": trials,
            "avg_trial_duration_sec": _num(metrics.get("avg_trial_duration_sec")),
            "submission": name,
        },
    )


class TerminalBenchSource(BenchmarkSource):
    spec = SourceSpec(
        id="terminal-bench",
        name="Terminal-Bench 4.0",
        homepage="https://www.tbench.ai",
        description="Hard, realistic terminal tasks solved end-to-end by an agent harness (Claude Code, Codex, "
        "mini-swe-agent...). Best submission per model and effort; reports cost and tokens per task.",
        license="Apache-2.0",
        attribution="Data: Terminal-Bench (tbench.ai), harbor-framework/terminal-bench on GitHub, Apache-2.0",
        default_enabled=True,
        metrics=(
            MetricSpec(
                "accuracy",
                "Terminal-Bench 4.0 accuracy",
                "agentic",
                weight=1.5,
                unit="%",
                description="Share of task trials solved, best agent harness per model and effort.",
            ),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        listing = await get_json(LIST_URL, headers={"Accept": "application/vnd.github+json"}, timeout=60)
        if not isinstance(listing, list):
            raise FetchError(f"{LIST_URL}: expected a directory listing")
        files = [
            (str(f["name"]), f.get("download_url") or RAW_URL.format(name=f["name"]))
            for f in listing
            if isinstance(f, dict) and f.get("type") == "file" and str(f.get("name", "")).endswith(".json")
        ]
        if not files:
            raise FetchError(f"{LIST_URL}: no submission files found")
        files.sort(reverse=True)  # newest first, so the cap drops the oldest
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(name: str, url: str) -> ScoreRecord | None:
            async with sem:
                try:
                    return parse_submission(name, await get_json(url, timeout=60))
                except FetchError as exc:
                    log.warning("Terminal-Bench submission %s skipped: %s", name, exc)
                    return None

        parsed = await asyncio.gather(*(one(n, u) for n, u in files[:MAX_FILES]))
        records = [r for r in parsed if r is not None]
        if not records:
            raise FetchError("Terminal-Bench: none of the submission files could be parsed")

        best: dict[tuple[str, str], ScoreRecord] = {}
        for rec in records:
            key = (rec.source_model.lower(), rec.effort)
            cur = best.get(key)
            if cur is None or rec.value > cur.value:
                best[key] = rec
        return list(best.values())
