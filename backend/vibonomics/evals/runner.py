"""Runs an evaluation: drives each model through each task, scores it, and stores the results."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..catalog.modelsdev import load_catalog
from ..db import session_scope
from ..events import publish
from ..orm import EvalResult, EvalRun, Model, ProviderConfig
from ..provider_service import build_adapter
from ..providers.base import FetchContext
from ..sandbox.executor import UnsafePath, safe_relative_path
from ..settings_store import AppSettings, load_settings
from . import sandbox_client
from .graders import grade_answer, grade_tool_calls
from .judge import Judge
from .llm import ChatError, ChatReply, LLMClient, ToolCall, assistant_message, tool_call_payload
from .suites import SUITES, TaskDef

log = logging.getLogger(__name__)

_cancelled: set[int] = set()
_tasks: dict[int, asyncio.Task[Any]] = {}

MAX_TOOL_OUTPUT = 6000
MAX_TRANSCRIPT_CHARS = 60_000
PASS_THRESHOLD = 75.0
PARTIAL_THRESHOLD = 40.0

EXEC_DISABLED_NOTE = (
    "Code execution is disabled (Settings → Testing → sandbox). Coding tasks were graded from the transcript "
    "by the judge only."
)


def schedule(run_id: int) -> None:
    # Must be called from the event loop (API endpoints are async): a sync endpoint runs in a
    # worker thread where there is no current loop.
    loop = asyncio.get_running_loop()
    task = loop.create_task(_execute(run_id))
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))


async def execute(run_id: int) -> None:
    """Run synchronously (used by tests); the API uses ``schedule``."""
    await _execute(run_id)


def cancel(run_id: int) -> None:
    _cancelled.add(run_id)
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        task.cancel()


def recover_interrupted() -> None:
    """Runs left queued/running by a restart can never finish; mark them failed."""
    with session_scope() as session:
        for run in session.scalars(select(EvalRun).where(EvalRun.status.in_(("queued", "running")))):
            run.status = "failed"
            run.error = "Interrupted by a Vibonomics restart."
            run.finished_at = datetime.now(UTC)


# -- execution -------------------------------------------------------------------------------


class Target:
    def __init__(self, provider_id: int, model_id: str, name: str, provider_name: str, client: LLMClient):
        self.provider_id = provider_id
        self.model_id = model_id
        self.name = name
        self.provider_name = provider_name
        self.client = client

    @property
    def key(self) -> str:
        return f"{self.provider_id}|{self.model_id}"


async def _execute(run_id: int) -> None:
    try:
        await _run(run_id)
    except asyncio.CancelledError:
        _finish(run_id, "cancelled", "Cancelled by the operator.")
        raise
    except Exception as exc:  # keep the worker alive no matter what
        log.exception("Eval run %s failed", run_id)
        _finish(run_id, "failed", f"{type(exc).__name__}: {exc}")
    finally:
        _cancelled.discard(run_id)


def _finish(run_id: int, status: str, error: str | None = None) -> None:
    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        if run is not None and run.status not in {"done", "failed", "cancelled"}:
            run.status = status
            run.error = error
            run.finished_at = datetime.now(UTC)
    publish("evals.finished", {"run_id": run_id, "status": status})


async def _run(run_id: int) -> None:
    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        run.status = "running"
        settings = load_settings(session)
        suite = SUITES.get(run.suite)
        if suite is None:
            raise ValueError(f"Unknown suite {run.suite!r}")
        task_ids = list((run.progress or {}).get("task_ids") or [t.id for t in suite.tasks])
        tasks = [t for tid in task_ids if (t := suite.task(tid)) is not None]
        targets_raw = list(run.targets or [])
        judge_provider_id = run.judge_provider_config_id
        judge_model = run.judge_model_id
        providers = {p.id: p for p in session.scalars(select(ProviderConfig))}
        model_rows = {
            (m.provider_config_id, m.model_id): m
            for m in session.scalars(select(Model).where(Model.provider_config_id.in_(list(providers.keys()))))
        }

    catalog = await load_catalog()
    ctx = FetchContext(catalog=catalog, settings=settings)
    sandbox = sandbox_client.mode_for(settings)

    targets: list[Target] = []
    target_errors: dict[str, str] = {}
    for raw in targets_raw:
        provider_id, model_id = int(raw["provider_id"]), str(raw["model_id"])
        cfg = providers.get(provider_id)
        try:
            adapter = build_adapter(cfg, ctx) if cfg else None
            if adapter is None:
                raise ChatError("the provider no longer exists")
            client = LLMClient(
                adapter.chat_target(model_id),
                timeout=float(settings.evals.request_timeout_seconds),
                max_tokens=int(settings.evals.max_output_tokens),
            )
        except Exception as exc:
            target_errors[f"{provider_id}|{model_id}"] = f"{type(exc).__name__}: {exc}"[:500]
            continue
        targets.append(
            Target(provider_id, model_id, raw.get("name") or model_id, raw.get("provider_name") or "", client)
        )

    judge: Judge | None = None
    judge_client: LLMClient | None = None
    judge_setup_error: str | None = None
    try:
        judge_cfg = providers.get(int(judge_provider_id)) if judge_provider_id else None
        if judge_cfg is None or not judge_model:
            raise ChatError("no judge model configured")
        adapter = build_adapter(judge_cfg, ctx)
        if adapter is None:
            raise ChatError("the judge provider no longer exists")
        judge_client = LLMClient(
            adapter.chat_target(judge_model),
            timeout=float(settings.evals.request_timeout_seconds),
            max_tokens=min(int(settings.evals.max_output_tokens), 2048),
        )
        judge = Judge(judge_client)
        if not await judge.probe():
            judge_setup_error = f"Judge check failed: {judge.last_error or 'no response'}"
            judge = None
    except Exception as exc:
        judge_setup_error = f"{type(exc).__name__}: {exc}"[:400]
        log.warning("Judge unavailable: %s", exc)

    total = len(tasks) * len(targets)
    done = 0
    _update_progress(run_id, 0, total, None)

    for key, message in target_errors.items():
        provider_id, model_id = key.split("|", 1)
        for task in tasks:
            _store_result(
                run_id,
                {
                    "provider_id": int(provider_id),
                    "model_id": model_id,
                    "task_id": task.id,
                    "category": task.category,
                    "status": "error",
                    "error": message,
                },
            )

    semaphore = asyncio.Semaphore(max(1, int(settings.evals.concurrency)))
    summaries: dict[str, list[dict[str, Any]]] = {t.key: [] for t in targets}

    async def one(target: Target, task: TaskDef) -> None:
        nonlocal done
        if run_id in _cancelled:
            return
        async with semaphore:
            if run_id in _cancelled:
                return
            current = f"{target.name} · {task.id}"
            _update_progress(run_id, done, total, current)
            result = await _run_task(task, target, settings, judge)
            model = model_rows.get((target.provider_id, target.model_id))
            result.update(
                {
                    "provider_id": target.provider_id,
                    "model_id": target.model_id,
                    "canonical_id": model.canonical_id if model else None,
                }
            )
            input_tokens = int(result.get("input_tokens") or 0)
            output_tokens = int(result.get("output_tokens") or 0)
            priced = model is not None and model.price_input is not None and model.price_output is not None
            if result.get("cost_usd") is None and priced:
                result["cost_usd"] = round(
                    (input_tokens * model.price_input + output_tokens * model.price_output) / 1_000_000, 6
                )
            _store_result(run_id, result)
            summaries[target.key].append(
                {
                    "task": task.id,
                    "category": task.category,
                    "score": float(result.get("score") or 0.0),
                    "status": result.get("status") or "failed",
                    "notes": result.get("judge_notes"),
                }
            )
            done += 1
            _update_progress(run_id, done, total, current)

    try:
        await asyncio.gather(*(one(t, task) for t in targets for task in tasks))
        cancelled = run_id in _cancelled
        summary: dict[str, Any] = {
            "targets": {},
            "judge": {"provider_id": judge_provider_id, "model_id": judge_model},
            "sandbox": sandbox,
        }
        if sandbox == "disabled":
            summary["note"] = EXEC_DISABLED_NOTE
        for target in targets:
            items = summaries[target.key]
            assessment = None
            if judge is not None and items and not judge.last_error:
                try:
                    assessment = await judge.assess(target.name, items)
                except ChatError as exc:
                    log.warning("Judge assessment failed: %s", exc)
                    judge.last_error = str(exc)[:400]
            scores = [item["score"] for item in items]
            summary["targets"][target.key] = {
                "score": round(sum(scores) / len(scores), 1) if scores else None,
                "tasks": len(items),
                "passed": sum(1 for item in items if item["status"] == "passed"),
                "tokens": _sum(run_id, target, "tokens"),
                "cost": _sum(run_id, target, "cost"),
                "assessment": assessment,
            }
        judge_error = judge_setup_error or (judge.last_error if judge is not None else None)
        if judge_error:
            summary["judge_error"] = judge_error
            summary["note"] = ("The judge model failed, so scoring used deterministic checks only. " + judge_error)[
                :600
            ]
    finally:
        for target in targets:
            await target.client.aclose()
        if judge_client is not None:
            await judge_client.aclose()

    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        if run is not None:
            run.summary = summary
            run.progress = {**(run.progress or {}), "done": done, "total": total, "current": None}
            if cancelled:
                run.status = "cancelled"
            elif done == 0:
                run.status = "failed"
                run.error = run.error or "No task results were produced."
            else:
                run.status = "done"
            run.finished_at = datetime.now(UTC)
    publish("evals.finished", {"run_id": run_id, "status": "cancelled" if cancelled else "done"})


def _update_progress(run_id: int, done: int, total: int, current: str | None) -> None:
    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        if run is not None:
            run.progress = {**(run.progress or {}), "done": done, "total": total, "current": current}
    publish("evals.progress", {"run_id": run_id, "done": done, "total": total, "current": current})


def _store_result(run_id: int, result: dict[str, Any]) -> None:
    transcript = result.get("transcript") or []
    if len(transcript) > 60:
        transcript = transcript[:30] + transcript[-30:]
    with session_scope() as session:
        session.add(
            EvalResult(
                run_id=run_id,
                provider_config_id=int(result["provider_id"]),
                model_id=str(result["model_id"]),
                canonical_id=result.get("canonical_id"),
                task_id=str(result["task_id"]),
                category=str(result.get("category") or ""),
                status=str(result.get("status") or "error"),
                score=float(result.get("score") or 0.0),
                check_score=result.get("check_score"),
                judge_score=result.get("judge_score"),
                judge_notes=result.get("judge_notes"),
                steps=int(result.get("steps") or 0),
                input_tokens=int(result.get("input_tokens") or 0),
                output_tokens=int(result.get("output_tokens") or 0),
                cost_usd=result.get("cost_usd"),
                latency_ms=int(result.get("latency_ms") or 0),
                transcript=result.get("transcript") or [],
                error=result.get("error"),
            )
        )


def _sum(run_id: int, target: Target, kind: str) -> Any:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(EvalResult).where(
                    EvalResult.run_id == run_id,
                    EvalResult.provider_config_id == target.provider_id,
                    EvalResult.model_id == target.model_id,
                )
            )
        )
    if kind == "tokens":
        return sum(r.input_tokens + r.output_tokens for r in rows)
    costs = [r.cost_usd for r in rows if r.cost_usd is not None]
    return round(sum(costs), 6) if costs else None


# -- task execution --------------------------------------------------------------------------


class Usage:
    def __init__(self) -> None:
        self.steps = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost: float | None = None
        self.latency_ms = 0

    def add(self, reply: ChatReply) -> None:
        self.steps += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.latency_ms += reply.latency_ms
        if reply.cost_usd is not None:
            self.cost = (self.cost or 0.0) + reply.cost_usd


def _status(score: float | None) -> str:
    if score is None:
        return "error"
    if score >= PASS_THRESHOLD:
        return "passed"
    if score >= PARTIAL_THRESHOLD:
        return "partial"
    return "failed"


def _trim(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = 0
    trimmed: list[dict[str, Any]] = []
    for entry in transcript:
        blob = str(entry.get("content") or "") + str(entry.get("tool_calls") or "")
        total += len(blob)
        if total > MAX_TRANSCRIPT_CHARS:
            trimmed.append({"role": "system", "content": "…transcript truncated…"})
            break
        trimmed.append(entry)
    return trimmed


async def _run_task(task: TaskDef, target: Target, settings: AppSettings, judge: Judge | None) -> dict[str, Any]:
    usage = Usage()
    transcript: list[dict[str, Any]] = []
    started = asyncio.get_running_loop().time()
    try:
        if task.kind == "agentic":
            outcome = await _agentic(task, target, settings, judge, usage, transcript)
        elif task.kind == "tools":
            outcome = await _tools(task, target, usage, transcript)
        else:
            outcome = await _chat(task, target, usage, transcript)
    except ChatError as exc:
        return {
            "task_id": task.id,
            "category": task.category,
            "status": "error",
            "error": str(exc)[:1000],
            "steps": usage.steps,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost,
            "latency_ms": int((asyncio.get_running_loop().time() - started) * 1000),
            "transcript": _trim(transcript),
        }
    outcome.update(
        {
            "task_id": task.id,
            "category": task.category,
            "status": _status(outcome.get("score")),
            "steps": usage.steps,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost,
            "latency_ms": int((asyncio.get_running_loop().time() - started) * 1000),
            "transcript": _trim(transcript),
        }
    )
    return outcome


async def _chat(task: TaskDef, target: Target, usage: Usage, transcript: list[dict[str, Any]]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if task.system:
        messages.append({"role": "system", "content": task.system})
    messages.append({"role": "user", "content": task.prompt})
    transcript.append({"role": "user", "content": task.prompt})
    reply = await target.client.chat(messages)
    usage.add(reply)
    transcript.append({"role": "assistant", "content": reply.text, "tool_calls": tool_call_payload(reply.tool_calls)})
    score, detail = grade_answer(reply.text, task.check or {})
    return {"score": score, "check_score": score, "judge_notes": detail}


async def _tools(task: TaskDef, target: Target, usage: Usage, transcript: list[dict[str, Any]]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if task.system:
        messages.append({"role": "system", "content": task.system})
    messages.append({"role": "user", "content": task.prompt})
    transcript.append({"role": "user", "content": task.prompt})
    calls: list[dict[str, Any]] = []
    text = ""
    for _round in range(max(1, task.max_steps)):
        reply = await target.client.chat(messages, list(task.tools))
        usage.add(reply)
        text = reply.text or text
        transcript.append(
            {"role": "assistant", "content": reply.text, "tool_calls": tool_call_payload(reply.tool_calls)}
        )
        calls.extend({"name": c.name, "arguments": c.arguments, "error": c.error} for c in reply.tool_calls)
        messages.append(assistant_message(reply))
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            result = task.tool_results.get(call.name, '{"error":"no such tool"}')
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result})
            transcript.append({"role": "tool", "name": call.name, "content": result[:MAX_TOOL_OUTPUT]})
    check = task.check or {}
    score, detail = grade_tool_calls(calls, text, check)
    tool_spec = check.get("tool_calls") or {}
    # grade_tool_calls already includes the answer when no tool call was appropriate; blending it
    # again would double-weight the answer and repeat the note.
    if check.get("answer") and "tool_calls" in check and not tool_spec.get("no_tools"):
        answer_score, answer_detail = grade_answer(text, check)
        score = round(0.7 * score + 0.3 * answer_score, 1)
        detail = f"{detail}; {answer_detail}"
    return {"score": score, "check_score": score, "judge_notes": detail}


async def _agentic(
    task: TaskDef,
    target: Target,
    settings: AppSettings,
    judge: Judge | None,
    usage: Usage,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace = dict(task.workspace)
    messages: list[dict[str, Any]] = []
    if task.system:
        messages.append({"role": "system", "content": task.system})
    messages.append({"role": "user", "content": task.prompt})
    transcript.append({"role": "user", "content": task.prompt})
    max_steps = min(task.max_steps, int(settings.evals.max_steps))
    finished = False
    nudged = False
    for _step in range(max_steps):
        reply = await target.client.chat(messages, list(task.tools))
        usage.add(reply)
        transcript.append(
            {"role": "assistant", "content": reply.text, "tool_calls": tool_call_payload(reply.tool_calls)}
        )
        messages.append(assistant_message(reply))
        if not reply.tool_calls:
            # Some models describe the change instead of making it. Nudge once, then accept the
            # answer as final so the run still ends.
            if not nudged and _step + 1 < max_steps:
                nudged = True
                nudge = (
                    "Use the tools to make the change in the workspace, then call finish when the tests pass. "
                    "Do not paste the code in a chat reply."
                )
                messages.append({"role": "user", "content": nudge})
                transcript.append({"role": "user", "content": nudge})
                continue
            break
        for call in reply.tool_calls:
            output, done = await _agentic_tool(task, workspace, call, settings)
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": output})
            transcript.append({"role": "tool", "name": call.name, "content": output[:MAX_TOOL_OUTPUT]})
            if done:
                finished = True
                break
        if finished:
            break

    tests_note, check_score = await _grade_agentic(task, workspace, settings)
    judge_score: float | None = None
    judge_notes: str | None = None
    if judge is not None:
        judge_score, judge_notes = await judge.grade_task(
            task, transcript, tests_note, workspace_note=_workspace_note(workspace)
        )
    if check_score is not None and judge_score is not None:
        score = round(0.7 * check_score + 0.3 * judge_score * 10, 1)
    elif judge_score is not None:
        score = round(judge_score * 10, 1)
    elif check_score is not None:
        score = check_score
    else:
        score = 0.0
    return {
        "score": score,
        "check_score": check_score,
        "judge_score": judge_score,
        "judge_notes": judge_notes or tests_note,
    }


async def _agentic_tool(
    task: TaskDef, workspace: dict[str, str], call: ToolCall, settings: AppSettings
) -> tuple[str, bool]:
    if call.error:
        return f"error: {call.error}", False
    args = call.arguments or {}
    name = call.name
    try:
        if name == "list_files":
            prefix = str(args.get("path") or "").strip("/.")
            files = sorted(p for p in workspace if not prefix or p.startswith(prefix))
            return "\n".join(files[:200]) or "(empty)", False
        if name == "read_file":
            path = safe_relative_path(str(args.get("path") or ""))
            content = workspace.get(path.as_posix())
            return content if content is not None else f"error: no such file {path}", False
        if name == "write_file":
            path = safe_relative_path(str(args.get("path") or ""))
            workspace[path.as_posix()] = str(args.get("content") or "")
            return f"wrote {path} ({len(workspace[path.as_posix()])} bytes)", False
        if name == "edit_file":
            path = safe_relative_path(str(args.get("path") or "")).as_posix()
            old = str(args.get("old_text") or "")
            new = str(args.get("new_text") or "")
            if path not in workspace:
                return f"error: no such file {path}", False
            if not old or old not in workspace[path]:
                return "error: old_text was not found in the file", False
            if workspace[path].count(old) > 1:
                return "error: old_text appears more than once; include more context", False
            workspace[path] = workspace[path].replace(old, new, 1)
            return f"edited {path}", False
        if name == "run_tests":
            result = await sandbox_client.run_code(workspace, list(task.test_command), task.test_timeout, settings)
            if result is None:
                return "Code execution is disabled in this deployment; reason about the code instead.", False
            output = (result.stdout + "\n" + result.stderr).strip()
            return (
                f"exit {result.exit_code}{' (timed out)' if result.timed_out else ''}\n{output[:MAX_TOOL_OUTPUT]}",
                False,
            )
        if name == "finish":
            return f"finished: {args.get('summary') or ''}", True
        return f"error: unknown tool {name}", False
    except UnsafePath as exc:
        return f"error: {exc}", False
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}", False


async def _grade_agentic(task: TaskDef, workspace: dict[str, str], settings: AppSettings) -> tuple[str, float | None]:
    total = task.test_count()
    if sandbox_client.mode_for(settings) == "disabled":
        return "Tests were not executed (code execution disabled).", None
    # Pristine tests only: the model's edits to tests/ are discarded.
    pristine = {p: c for p, c in task.workspace.items() if p.startswith("tests/")}
    solution = {path: content for path, content in workspace.items() if not path.startswith("tests/")}
    files = {**solution, **pristine, **task.hidden_tests}
    result = await sandbox_client.run_code(files, list(task.test_command), task.test_timeout, settings)
    if result is None:
        return "Tests were not executed (sandbox unavailable).", None
    if result.timed_out:
        return f"Tests timed out after {task.test_timeout:.0f}s.", 0.0
    if result.exit_code == 0:
        return f"All {total} tests passed.", (100.0 if total else None)
    text = f"{result.stdout}\n{result.stderr}"
    failures = _count(text, "failures")
    errors = _count(text, "errors")
    skipped = _count(text, "skipped")
    passed = max(0, total - failures - errors - skipped) if total else 0
    score = round(100.0 * passed / total, 1) if total else 0.0
    note = f"{passed}/{total} tests passed"
    if failures or errors:
        note += f" (failures={failures}, errors={errors})"
    if skipped:
        note += f", skipped={skipped}"
    return note, score


def _count(text: str, name: str) -> int:
    match = re.search(rf"{name}=(\d+)", text)
    return int(match.group(1)) if match else 0


def _workspace_note(workspace: dict[str, str]) -> str:
    files = sorted(workspace)
    return "files after the run: " + ", ".join(files[:40])
