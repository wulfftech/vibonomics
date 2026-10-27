"""Test suites: what an operator can run against a model.

A task is declarative so the runner, the judge and the tests can all work from the same definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .llm import ToolSpec

TaskKind = Literal["agentic", "chat", "tools"]


@dataclass(frozen=True)
class TaskDef:
    id: str
    name: str
    category: str
    description: str
    kind: TaskKind
    prompt: str
    system: str | None = None
    tools: tuple[ToolSpec, ...] = ()
    workspace: dict[str, str] = field(default_factory=dict)
    hidden_tests: dict[str, str] = field(default_factory=dict)
    test_command: tuple[str, ...] = ("python", "-m", "unittest", "discover", "-s", "tests")
    reference: dict[str, str] = field(default_factory=dict)
    tool_results: dict[str, str] = field(default_factory=dict)
    check: dict[str, Any] | None = None
    judge_rubric: str = ""
    max_steps: int = 8
    test_timeout: float = 60.0

    @property
    def uses_sandbox(self) -> bool:
        return self.kind == "agentic"

    def test_count(self) -> int:
        total = 0
        for files in (self.workspace, self.hidden_tests):
            for name, content in files.items():
                if name.startswith("tests/"):
                    total += len(re.findall(r"^\s*def test_", content, re.MULTILINE))
        return total


@dataclass(frozen=True)
class SuiteDef:
    id: str
    name: str
    description: str
    tasks: tuple[TaskDef, ...]

    def task(self, task_id: str) -> TaskDef | None:
        return next((t for t in self.tasks if t.id == task_id), None)


def _build() -> dict[str, SuiteDef]:
    from .tasks_chat import CHAT_TASKS, TOOL_TASKS
    from .tasks_coding import AGENTIC_TASKS

    agentic = {t.id: t for t in AGENTIC_TASKS}
    chat = {t.id: t for t in CHAT_TASKS}
    tools = {t.id: t for t in TOOL_TASKS}

    quick = (
        agentic["agentic-fix-mean"],
        agentic["agentic-slugify"],
        tools["tool-pick"],
        tools["tool-no-call"],
        chat["instr-json"],
        chat["acc-context"],
        chat["acc-trap"],
    )
    standard = (
        *quick,
        agentic["agentic-report"],
        agentic["agentic-config-defaults"],
        agentic["agentic-durations"],
        tools["tool-sequence"],
        tools["tool-args"],
        chat["instr-words"],
        chat["reason-math"],
    )
    return {
        "quick": SuiteDef(
            id="quick",
            name="Quick screen",
            description="Seven cheap tasks: two coding tasks, two tool-use checks, an exact-format check, and "
            "two accuracy checks (including a hallucination trap). Good for triaging free and unscored models.",
            tasks=quick,
        ),
        "standard": SuiteDef(
            id="standard",
            name="Standard agentic coding",
            description="Thirteen tasks across agentic coding, tool use, instruction following and accuracy. "
            "Takes longer and costs more, but separates models clearly.",
            tasks=standard,
        ),
    }


SUITES: dict[str, SuiteDef] = _build()
