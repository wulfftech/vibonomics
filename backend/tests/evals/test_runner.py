"""End-to-end run with a scripted model: exercises tools, the sandbox, grading and the judge."""

from __future__ import annotations

import pytest

from vibonomics import crypto
from vibonomics.db import init_db, session_scope
from vibonomics.evals import runner
from vibonomics.evals.llm import ChatError, ChatReply, ToolCall
from vibonomics.evals.tasks_coding import MEAN_REFERENCE
from vibonomics.orm import EvalResult, EvalRun, Model, ProviderConfig
from vibonomics.settings_store import load_settings, save_settings

MODEL_ID = "fake/agentic-model"


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


def _seed() -> tuple[int, int]:
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
        settings.evals.sandbox_mode = "local"
        settings.evals.concurrency = 1
        save_settings(session, settings)
        session.flush()
        run = EvalRun(
            suite="quick",
            judge_provider_config_id=provider.id,
            judge_model_id=MODEL_ID,
            targets=[{"provider_id": provider.id, "model_id": MODEL_ID, "name": "Fake model", "provider_name": "Fake"}],
            progress={"total": 3, "done": 0, "task_ids": ["instr-json", "tool-pick", "agentic-fix-mean"]},
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
