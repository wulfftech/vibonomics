"""What the judge is shown of a transcript."""

from __future__ import annotations

from vibonomics.evals.judge import _condense
from vibonomics.evals.llm import ToolCall, tool_call_payload


def test_the_judge_sees_the_tool_calls_as_stored():
    calls = [ToolCall(id="c1", name="write_file", arguments={"path": "stats.py", "content": "def mean(xs): ..."})]
    text = _condense([{"role": "assistant", "content": "Fixing it.", "tool_calls": tool_call_payload(calls)}])
    assert "CALL write_file(" in text
    assert "stats.py" in text
    assert "None(None)" not in text
