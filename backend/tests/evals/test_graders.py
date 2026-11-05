from vibonomics.evals.graders import grade_answer, grade_tool_calls
from vibonomics.evals.tasks_chat import CONTEXT_TRAP


def test_trap_accepts_any_way_of_saying_the_passage_is_silent():
    for answer in (
        "The passage does not specify a compression algorithm.",
        "The passage doesn't mention which compression is used.",
        "Not answerable.",
        "There is no information about compression in the passage.",
        "That is not stated in the text.",
        "The text does not state which algorithm it uses.",
        "I don’t know; the passage never says.",
        "Unknown.",
        "It cannot be determined from the passage.",
    ):
        assert grade_answer(answer, CONTEXT_TRAP.check)[0] == 100, answer


def test_trap_rejects_an_invented_answer():
    for answer in (
        "The Zephyr sync client uses gzip compression for uploads.",
        "It compresses uploads with zstd.",
        "It uses gzip, not zstd.",
        "The sync client retries five times, so it uses exponential backoff.",
        "",
    ):
        assert grade_answer(answer, CONTEXT_TRAP.check)[0] == 0, answer


def test_equals_and_normalisation():
    assert grade_answer("Paris", {"answer": {"equals": "paris"}})[0] == 100
    assert grade_answer("paris.", {"answer": {"equals": "Paris"}})[0] == 100
    assert grade_answer("Lyon", {"answer": {"equals": "Paris"}})[0] == 0


def test_contains_all_and_forbidden():
    spec = {"answer": {"contains_all": ["5", "retries"], "not_contains": ["four"]}}
    assert grade_answer("It retries 5 times.", spec)[0] == 100
    assert grade_answer("It retries five times, never four.", spec)[0] == 0
    assert grade_answer("It does 5 attempts.", spec)[0] == 0


def test_word_limits():
    spec = {"answer": {"max_words": 3, "contains_all": ["lookup"]}}
    assert grade_answer("index speeds lookup", spec)[0] == 100
    assert grade_answer("index speeds up the lookup", spec)[0] == 0


def test_json_grading_accepts_fences():
    spec = {
        "answer": {
            "json": {
                "required_keys": ["service", "replicas"],
                "equals": {"service": "zephyr", "replicas": 3},
            }
        }
    }
    good = '```json\n{"service": "zephyr", "replicas": 3}\n```'
    assert grade_answer(good, spec)[0] == 100
    assert grade_answer('{"service": "zephyr", "replicas": 2}', spec)[0] == 0
    assert grade_answer("not json", spec)[0] == 0


def test_tool_first_call_grading():
    spec = {"tool_calls": {"mode": "first", "expect": [{"name": "get_forecast", "args": {"city": "Paris"}}]}}
    calls = [{"name": "get_forecast", "arguments": {"city": "Paris", "day": "tomorrow"}}]
    assert grade_tool_calls(calls, "", spec)[0] == 100
    wrong = [{"name": "get_weather", "arguments": {"city": "Paris"}}]
    assert grade_tool_calls(wrong, "", spec)[0] == 0


def test_tool_sequence_and_no_tools():
    spec = {
        "tool_calls": {
            "mode": "sequence",
            "expect": [{"name": "a", "args": {"x": 1}}, {"name": "b", "args": {"y": 2}}],
        }
    }
    assert (
        grade_tool_calls([{"name": "a", "arguments": {"x": 1}}, {"name": "b", "arguments": {"y": 2}}], "", spec)[0]
        == 100
    )
    assert grade_tool_calls([{"name": "a", "arguments": {"x": 1}}], "", spec)[0] == 0
    no_tools = {"no_tools": True, "tool_calls": {"no_tools": True}, "answer": {"contains_all": ["391"]}}
    assert grade_tool_calls([], "391", no_tools)[0] == 100
    assert grade_tool_calls([{"name": "search_web", "arguments": {}}], "391", no_tools)[0] == 0


def test_expected_arguments_must_match():
    spec = {
        "tool_calls": {"mode": "sequence", "expect": [{"name": "create_note", "args": {"title": "T", "body": "B"}}]}
    }
    exact = [{"name": "create_note", "arguments": {"title": "T", "body": "B"}}]
    extra_keys = [{"name": "create_note", "arguments": {"title": "T", "body": "B", "tags": ["x"]}}]
    wrong_body = [{"name": "create_note", "arguments": {"title": "T", "body": "C"}}]
    assert grade_tool_calls(exact, "", spec)[0] == 100
    assert grade_tool_calls(extra_keys, "", spec)[0] == 100
    assert grade_tool_calls(wrong_body, "", spec)[0] == 0
