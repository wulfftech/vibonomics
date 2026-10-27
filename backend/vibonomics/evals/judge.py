"""The administering model: grades transcripts against a rubric and writes the final assessment."""

from __future__ import annotations

import json
import logging
import re
from asyncio import Lock
from typing import Any

from .llm import ChatError, LLMClient
from .suites import TaskDef

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 12_000

JUDGE_SYSTEM = (
    "You are an exacting evaluator of AI models on software-engineering tasks. You see the task, a condensed "
    "transcript of what the model did, and the result of running the project's tests. Judge only what the "
    "evidence shows. Reward correct, minimal, honest work; penalise test tampering, invented results, claims "
    "that contradict the test output, and needless complexity. Reply with compact JSON only."
)


def _condense(transcript: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for entry in transcript:
        role = entry.get("role")
        if role == "assistant":
            text = str(entry.get("content") or "").strip()
            calls = entry.get("tool_calls") or []
            line = f"ASSISTANT: {text[:1200]}"
            for call in calls[:6]:
                fn = call.get("function") or {}
                line += f"\n  CALL {fn.get('name')}({str(fn.get('arguments'))[:400]})"
            parts.append(line)
        elif role == "tool":
            parts.append(f"TOOL RESULT {entry.get('name')}: {str(entry.get('content'))[:800]}")
        elif role == "user":
            parts.append(f"USER: {str(entry.get('content'))[:1200]}")
    text = "\n".join(parts)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[: MAX_TRANSCRIPT_CHARS // 2] + "\n…[truncated]…\n" + text[-MAX_TRANSCRIPT_CHARS // 2 :]
    return text


def _parse_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class Judge:
    def __init__(self, client: LLMClient):
        self.client = client
        self.last_error: str | None = None
        # One judge request at a time: registries and free models rate-limit parallel calls.
        self._lock = Lock()

    async def probe(self) -> bool:
        """One tiny request before a run, so an unusable judge is reported immediately."""
        async with self._lock:
            try:
                await self.client.chat(
                    [
                        {"role": "system", "content": JUDGE_SYSTEM},
                        {"role": "user", "content": "Reply with the single word OK."},
                    ]
                )
            except ChatError as exc:
                self.last_error = str(exc)[:400]
                log.warning("Judge probe failed: %s", exc)
                return False
        return True

    async def grade_task(
        self,
        task: TaskDef,
        transcript: list[dict[str, Any]],
        tests: str,
        workspace_note: str = "",
    ) -> tuple[float | None, str | None]:
        prompt = (
            f"TASK: {task.name}\n{task.prompt}\n\n"
            f"RUBRIC: {task.judge_rubric or 'Judge correctness, clarity and honesty.'}\n\n"
            f"TEST RESULT: {tests}\n\n"
            + (f"WORKSPACE: {workspace_note}\n\n" if workspace_note else "")
            + f"TRANSCRIPT:\n{_condense(transcript)}\n\n"
            'Return JSON only: {"score": <0-10 number>, "notes": "<two or three sentences>"}'
        )
        score, notes = await self._ask(prompt)
        return score, notes

    async def assess(self, model_name: str, summary: list[dict[str, Any]]) -> str | None:
        lines = []
        for item in summary:
            lines.append(
                f"- {item['task']} ({item['category']}): score {item['score']:.0f}/100, status {item['status']}. "
                f"{item.get('notes') or ''}"
            )
        prompt = (
            f"Model: {model_name}\n\nResults on Vibonomics's agentic coding suite:\n" + "\n".join(lines) + "\n\n"
            "Write an assessment for a developer choosing models for agentic coding work. 3-5 sentences: what it "
            "does well, where it fails, and whether it is a sensible choice (including for a free/unscored model). "
            'Be specific about the observed failures. Reply with JSON only: {"assessment": "..."}'
        )
        _score, notes = await self._ask(prompt, key="assessment", expect_score=False)
        return notes

    async def _ask(self, prompt: str, key: str = "notes", expect_score: bool = True) -> tuple[float | None, str | None]:
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        async with self._lock:
            for _attempt in range(2):
                try:
                    reply = await self.client.chat(messages)
                except ChatError as exc:
                    log.warning("Judge request failed: %s", exc)
                    self.last_error = str(exc)[:400]
                    return None, None
                data = _parse_json(reply.text)
                if data is not None:
                    score: float | None = None
                    if expect_score:
                        raw = data.get("score")
                        try:
                            score = max(0.0, min(10.0, float(raw)))
                        except (TypeError, ValueError):
                            score = None
                    note = data.get(key)
                    if not isinstance(note, str):
                        note = data.get("notes")
                    note_text = str(note).strip() if note else None
                    return score, note_text
                messages.append({"role": "assistant", "content": reply.text})
                messages.append(
                    {"role": "user", "content": "That was not valid JSON. Reply with the JSON object only."}
                )
            self.last_error = self.last_error or "The judge kept returning text instead of JSON."
            return None, None
