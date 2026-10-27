"""End-to-end run with a scripted model: exercises tools, the sandbox, grading and the judge."""

from __future__ import annotations

import pytest

from vibonomics import crypto
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.db import init_db, session_scope
from vibonomics.evals import runner
from vibonomics.evals.llm import ChatError, ChatReply, ToolCall
from vibonomics.evals.tasks_coding import MEAN_REFERENCE
from vibonomics.orm import EvalResult, EvalRun, Model, ProviderConfig
from vibonomics.scoring import own_test_scores
from vibonomics.settings_store import load_settings, save_settings

MODEL_ID = "fake/agentic-model"
QUICK_TASKS = ("instr-json", "tool-pick", "agentic-fix-mean")


class FakeClient:
    """Stands in for LLMClient. Decides what to reply from the tool list / judge system prompt."""

    def __init__(self, target, *, timeout: float = 60, max_tokens: int = 1024):
        self.target = target
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def aclose(self) -> None:
        return None

    async def chat(self, messages, tools=None):
        system = next((m.get("content") for m in messages if m.get("role") == "system"), "") or ""
        if "exacting evaluator" in system:
            prompt = str(messages[-1].get("content") or "")
            if prompt.startswith("Model: "):
                return ChatReply(text='{"assessment": "Handles small tasks correctly."}')
            return ChatReply(text='{"score": 8, "notes": "Clear, minimal change."}')
        tools = tools or []
        names = {t.name for t in tools}
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if "write_file" in names:
            if not tool_messages:
                return ChatReply(
                    tool_calls=[
                        ToolCall(
                            id="call_write",
                            name="write_file",
                            arguments={"path": "calc.py", "content": MEAN_REFERENCE["calc.py"]},
                        )
                    ],
                    input_tokens=100,
                    output_tokens=40,
                )
            return ChatReply(
                tool_calls=[ToolCall(id="call_finish", name="finish", arguments={"summary": "fixed the mean"})],
                input_tokens=20,
                output_tokens=10,
            )
        if "get_forecast" in names:
            if not tool_messages:
                return ChatReply(
                    tool_calls=[
                        ToolCall(
                            id="call_forecast", name="get_forecast", arguments={"city": "Paris", "day": "tomorrow"}
                        )
                    ],
                    input_tokens=30,
                    output_tokens=15,
                )
            return ChatReply(text="Light rain, around 17C.", input_tokens=40, output_tokens=8)
        return ChatReply(
            text='{"service": "zephyr", "replicas": 3, "tags": ["api", "eu-west"]}',
            input_tokens=50,
            output_tokens=20,
        )


@pytest.fixture(scope="module", autouse=True)
def _database():
    init_db()


@pytest.fixture(autouse=True)
def _no_catalog(monkeypatch):
    """Tests must not fetch models.dev; the fake client needs nothing from the catalog."""

    async def load_catalog(force: bool = False) -> ModelsDevCatalog:
        return ModelsDevCatalog({})

    monkeypatch.setattr(runner, "load_catalog", load_catalog)


def _seed(
    task_ids: tuple[str, ...] = QUICK_TASKS, sandbox_mode: str = "local", extra_targets: tuple[dict, ...] = ()
) -> tuple[int, int]:
    with session_scope() as session:
        provider = ProviderConfig(
            type="openrouter",
            name="Fake provider",
            enabled=True,
            api_key_enc=crypto.encrypt("sk-or-test"),
            options={},
        )
        session.add(provider)
        session.flush()
        session.add(
            Model(
                provider_config_id=provider.id,
                model_id=MODEL_ID,
                name="Fake model",
                canonical_id="fake/agentic-model",
                price_input=1.0,
                price_output=2.0,
                active=True,
            )
        )
        settings = load_settings(session)
        settings.evals.sandbox_mode = sandbox_mode
        settings.evals.concurrency = 1
        save_settings(session, settings)
        session.flush()
        run = EvalRun(
            suite="quick",
            judge_provider_config_id=provider.id,
            judge_model_id=MODEL_ID,
            targets=[
                {"provider_id": provider.id, "model_id": MODEL_ID, "name": "Fake model", "provider_name": "Fake"},
                *extra_targets,
            ],
            progress={"total": len(task_ids), "done": 0, "task_ids": list(task_ids)},
            status="queued",
        )
        session.add(run)
        session.flush()
        return run.id, provider.id


@pytest.mark.asyncio
async def test_full_run(monkeypatch):
    monkeypatch.setattr(runner, "LLMClient", FakeClient)
    run_id, provider_id = _seed()
    await runner.execute(run_id)

    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        results = {r.task_id: r for r in session.query(EvalResult).filter(EvalResult.run_id == run_id)}
        assert run is not None
        assert run.status == "done", run.error
        assert set(results) == {"instr-json", "tool-pick", "agentic-fix-mean"}

        assert results["instr-json"].score == 100
        assert results["instr-json"].status == "passed"

        tool = results["tool-pick"]
        assert tool.score == 100
        assert tool.status == "passed"

        agentic = results["agentic-fix-mean"]
        assert agentic.check_score == 100, agentic.judge_notes
        assert agentic.judge_score == 8
        assert agentic.score == pytest.approx(94.0)
        assert agentic.cost_usd is not None and agentic.cost_usd > 0
        assert any(entry.get("role") == "tool" for entry in agentic.transcript)

        key = f"{provider_id}|{MODEL_ID}"
        summary = run.summary["targets"][key]
        assert summary["assessment"] == "Handles small tasks correctly."
        assert summary["passed"] == 3
        assert run.progress["done"] == 3


def test_recover_interrupted_marks_runs_failed():
    with session_scope() as session:
        run = EvalRun(suite="quick", status="running", targets=[], progress={})
        session.add(run)
        session.flush()
        run_id = run.id
    runner.recover_interrupted()
    with session_scope() as session:
        assert session.get(EvalRun, run_id).status == "failed"


class FailingJudgeClient(FakeClient):
    async def chat(self, messages, tools=None):
        system = next((m.get("content") for m in messages if m.get("role") == "system"), "") or ""
        if "exacting evaluator" in system:
            raise ChatError("HTTP 429: rate limited by the provider")
        return await super().chat(messages, tools)


@pytest.mark.asyncio
async def test_judge_failure_is_reported_without_losing_checks(monkeypatch):
    monkeypatch.setattr(runner, "LLMClient", FailingJudgeClient)
    run_id, provider_id = _seed()
    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        run.progress = {**run.progress, "task_ids": ["agentic-fix-mean"], "total": 1}
    await runner.execute(run_id)
    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        result = session.query(EvalResult).filter(EvalResult.run_id == run_id).one()
        assert run is not None
        assert run.status == "done"
        assert "429" in run.summary["judge_error"]
        assert "deterministic checks only" in run.summary["note"]
        assert result.check_score == 100
        assert result.judge_score is None
        assert result.score == 100
        assert run.summary["targets"][f"{provider_id}|{MODEL_ID}"]["assessment"] is None


def _agentic_task(task_id: str):
    from vibonomics.evals.suites import SUITES

    return next(t for suite in SUITES.values() for t in suite.tasks if t.id == task_id)


def _local_sandbox():
    from vibonomics.settings_store import AppSettings

    settings = AppSettings()
    settings.evals.sandbox_mode = "local"
    return settings


@pytest.mark.asyncio
async def test_a_test_module_that_fails_to_import_scores_none_of_its_tests():
    task = _agentic_task("agentic-report")
    note, score = await runner._grade_agentic(task, dict(task.workspace), _local_sandbox())
    assert score == 0.0
    assert note.startswith(f"0/{task.test_count()} tests passed")
    note, score = await runner._grade_agentic(task, {**task.workspace, **task.reference}, _local_sandbox())
    assert score == 100.0


@pytest.mark.asyncio
async def test_an_unavailable_sandbox_is_not_a_pass(monkeypatch):
    from vibonomics.evals import sandbox_client
    from vibonomics.sandbox.executor import ExecResult

    async def unavailable(*args, **kwargs):
        return ExecResult(None, "", "Sandbox unavailable: ConnectError: connection refused", False, 0)

    monkeypatch.setattr(sandbox_client, "run_code", unavailable)
    task = _agentic_task("agentic-fix-mean")
    note, score = await runner._grade_agentic(task, dict(task.workspace), _local_sandbox())
    assert score is None
    assert note == "Tests were not executed (Sandbox unavailable: ConnectError: connection refused)."
class RateLimitedToolsClient(FakeClient):
    """The model under test hits a provider rate limit on the tool-use task."""

    async def chat(self, messages, tools=None):
        if any(t.name == "get_forecast" for t in tools or []):
            raise ChatError("HTTP 429: rate limited by the provider")
        return await super().chat(messages, tools)


@pytest.mark.asyncio
async def test_model_errors_are_reported_and_left_out_of_scores(monkeypatch):
    monkeypatch.setattr(runner, "LLMClient", RateLimitedToolsClient)
    gone = {"provider_id": 987_654, "model_id": "gone/model", "name": "Gone model", "provider_name": "Gone"}
    run_id, provider_id = _seed(extra_targets=(gone,))
    await runner.execute(run_id)

    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        results = list(session.query(EvalResult).filter(EvalResult.run_id == run_id))
        assert run is not None
        assert run.status == "done", run.error
        mine = {r.task_id: r for r in results if r.provider_config_id == provider_id}
        assert mine["tool-pick"].status == "error"
        assert "429" in mine["tool-pick"].error
        missing = [r for r in results if r.provider_config_id == gone["provider_id"]]
        assert len(missing) == len(QUICK_TASKS)
        assert {r.status for r in missing} == {"error"}

        summary = run.summary["targets"][f"{provider_id}|{MODEL_ID}"]
        # instr-json scores 100 and agentic-fix-mean 94; the rate-limited task does not average in as 0.
        assert summary["score"] == pytest.approx(97.0)
        assert summary["tasks"] == 3
        assert summary["passed"] == 2
        assert summary["errors"] == 1
        assert summary["ungraded"] == 0
        # One task of the model under test plus every task of the target that could not be set up.
        assert "ended in an error (4)" in run.summary["note"]

        own = own_test_scores(session)
        assert own[(provider_id, MODEL_ID)]["score"] == pytest.approx(97.0)
        assert own[(provider_id, MODEL_ID)]["tasks"] == 2
        assert (gone["provider_id"], gone["model_id"]) not in own


@pytest.mark.asyncio
async def test_ungraded_task_is_reported_and_left_out_of_scores(monkeypatch):
    # No sandbox and no judge: the agentic task has neither a test result nor a judge score.
    monkeypatch.setattr(runner, "LLMClient", FailingJudgeClient)
    run_id, provider_id = _seed(task_ids=("instr-json", "agentic-fix-mean"), sandbox_mode="disabled")
    await runner.execute(run_id)

    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        results = {r.task_id: r for r in session.query(EvalResult).filter(EvalResult.run_id == run_id)}
        assert run is not None
        assert run.status == "done", run.error
        assert results["instr-json"].status == "passed"
        agentic = results["agentic-fix-mean"]
        assert agentic.status == "ungraded"
        assert agentic.check_score is None
        assert agentic.judge_score is None
        assert "Not graded" in agentic.error

        summary = run.summary["targets"][f"{provider_id}|{MODEL_ID}"]
        assert summary["score"] == pytest.approx(100.0)
        assert summary["tasks"] == 2
        assert summary["passed"] == 1
        assert summary["ungraded"] == 1
        assert summary["errors"] == 0
        assert "429" in run.summary["judge_error"]
        note = run.summary["note"]
        assert runner.EXEC_DISABLED_NOTE in note
        assert "deterministic checks only" in note
        assert "no judge score (1) are marked ungraded" in note

        own = own_test_scores(session)
        assert own[(provider_id, MODEL_ID)]["score"] == pytest.approx(100.0)
        assert own[(provider_id, MODEL_ID)]["tasks"] == 1


class UnparseableScoreJudgeClient(FakeClient):
    """The judge answers the probe, then grades with JSON whose score is not a number."""

    reply = ""
    grading_requests = 0

    async def chat(self, messages, tools=None):
        system = next((m.get("content") for m in messages if m.get("role") == "system"), "") or ""
        if "exacting evaluator" in system and str(messages[1].get("content") or "").startswith("TASK:"):
            type(self).grading_requests += 1
            return ChatReply(text=self.reply)
        return await super().chat(messages, tools)


@pytest.mark.parametrize(
    ("reply", "reported"),
    [
        ('{"score": "8/10", "notes": "Clear, minimal change."}', '"8/10"'),
        ('{"notes": "Clear, minimal change."}', "null"),
        ('{"score": NaN, "notes": "Clear, minimal change."}', "NaN"),
    ],
)
@pytest.mark.asyncio
async def test_unparseable_judge_score_is_reported(monkeypatch, reply, reported):
    monkeypatch.setattr(runner, "LLMClient", UnparseableScoreJudgeClient)
    monkeypatch.setattr(UnparseableScoreJudgeClient, "reply", reply)
    monkeypatch.setattr(UnparseableScoreJudgeClient, "grading_requests", 0)
    run_id, provider_id = _seed(task_ids=("agentic-fix-mean",), sandbox_mode="disabled")
    await runner.execute(run_id)

    with session_scope() as session:
        run = session.get(EvalRun, run_id)
        result = session.query(EvalResult).filter(EvalResult.run_id == run_id).one()
        assert run is not None
        assert run.status == "done", run.error
        # Asked once more for a numeric score, then reported instead of scored.
        assert UnparseableScoreJudgeClient.grading_requests == 2
        assert result.judge_score is None
        assert result.status == "ungraded"
        assert "numeric score" in run.summary["judge_error"]
        assert reported in run.summary["judge_error"]
        assert runner.EXEC_DISABLED_NOTE in run.summary["note"]
        summary = run.summary["targets"][f"{provider_id}|{MODEL_ID}"]
        assert summary["score"] is None
        assert summary["ungraded"] == 1
        assert summary["assessment"] is None
