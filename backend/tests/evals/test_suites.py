"""Validate every task: reference solutions must pass the visible and hidden tests."""

from __future__ import annotations

import pytest

from vibonomics.evals.suites import SUITES
from vibonomics.sandbox import executor

AGENTIC = list({task.id: task for suite in SUITES.values() for task in suite.tasks if task.kind == "agentic"}.values())
QUICK = {t.id for t in SUITES["quick"].tasks}
STANDARD = {t.id for t in SUITES["standard"].tasks}


def test_suite_ids_and_task_ids_are_unique():
    assert set(SUITES) == {"quick", "standard", "hard"}
    for suite in SUITES.values():
        ids = [t.id for t in suite.tasks]
        assert len(set(ids)) == len(ids), f"{suite.id} has duplicate task ids"
    assert QUICK < STANDARD, "the standard suite should include every quick task"
    # A task id names one task: the same id in two suites would mix their results in the scores.
    assert not (STANDARD & {t.id for t in SUITES["hard"].tasks}), "the hard suite should hold only its own tasks"
    assert (len(SUITES["quick"].tasks), len(SUITES["standard"].tasks), len(SUITES["hard"].tasks)) == (7, 14, 11)
    assert all(t.test_count() > 0 for t in AGENTIC)
    categories = {t.category for t in SUITES["standard"].tasks}
    assert {"agentic_coding", "tool_use", "instruction_following", "accuracy"} <= categories


@pytest.mark.parametrize("task", AGENTIC, ids=[t.id for t in AGENTIC])
def test_reference_solution_passes_every_test(task):
    files = {**task.workspace, **task.hidden_tests, **task.reference}
    result = executor.run(files, list(task.test_command), timeout=60)
    assert result.exit_code == 0, f"{task.id} reference failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("task", AGENTIC, ids=[t.id for t in AGENTIC])
def test_hidden_tests_fail_without_the_fix(task):
    """The hidden tests should actually discriminate (fail on the starting code)."""
    files = {**task.workspace, **task.hidden_tests}
    result = executor.run(files, list(task.test_command), timeout=60)
    assert result.exit_code != 0, f"{task.id} hidden tests pass without any changes"


def test_agentic_tasks_declare_a_reference_and_rubric():
    for task in AGENTIC:
        assert task.reference, f"{task.id} has no reference solution"
        assert task.judge_rubric, f"{task.id} has no judge rubric"
        assert task.uses_sandbox is True
