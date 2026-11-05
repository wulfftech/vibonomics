"""Tool-use, instruction-following and accuracy tasks with deterministic grading."""

from __future__ import annotations

from .llm import ToolSpec
from .suites import TaskDef

WEATHER_TOOLS = (
    ToolSpec(
        name="get_weather",
        description="Current weather for a city.",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    ),
    ToolSpec(
        name="get_forecast",
        description="Weather forecast for a city on a given day (today, tomorrow, or a date).",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}, "day": {"type": "string"}},
            "required": ["city"],
        },
    ),
    ToolSpec(
        name="search_web",
        description="Search the web for general information.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
)

GEO_TOOLS = (
    ToolSpec(
        name="lookup_capital",
        description="Look up the capital city of a country.",
        parameters={"type": "object", "properties": {"country": {"type": "string"}}, "required": ["country"]},
    ),
    ToolSpec(
        name="lookup_population",
        description="Look up the population of a city.",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    ),
)

NOTE_TOOLS = (
    ToolSpec(
        name="create_note",
        description="Create a note with a title and body.",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title", "body"],
        },
    ),
    ToolSpec(
        name="send_email",
        description="Send an email.",
        parameters={
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}},
            "required": ["to", "subject"],
        },
    ),
)

TOOL_PICK = TaskDef(
    id="tool-pick",
    name="Choose the right tool",
    description="Pick the forecast tool for a future-weather question, with the right argument.",
    kind="tools",
    category="tool_use",
    prompt="What is the weather going to be like in Paris tomorrow?",
    system="Use the available tools when they help. Answer briefly after you have the information.",
    tools=WEATHER_TOOLS,
    tool_results={
        "get_weather": '{"city":"Paris","conditions":"sunny","temperature_c":21}',
        "get_forecast": '{"city":"Paris","day":"tomorrow","conditions":"light rain","high_c":17,"low_c":11}',
        "search_web": '{"results":[]}',
    },
    check={
        "tool_calls": {
            "mode": "first",
            "expect": [{"name": "get_forecast", "args": {"city": "Paris"}}],
            "args_exact": False,
        }
    },
    max_steps=3,
)

TOOL_NO_CALL = TaskDef(
    id="tool-no-call",
    name="Do not call a tool when none is needed",
    description="A simple arithmetic question must be answered directly, without tool calls.",
    kind="tools",
    category="tool_use",
    prompt="What is 17 * 23? Answer with the number.",
    system="Use the available tools when they help. Answer briefly.",
    tools=WEATHER_TOOLS,
    check={"no_tools": True, "tool_calls": {"no_tools": True}, "answer": {"contains_all": ["391"]}},
    max_steps=2,
)

TOOL_SEQUENCE = TaskDef(
    id="tool-sequence",
    name="Chain two tool calls",
    description="Look up a capital, then that city's population, using both tools in order.",
    kind="tools",
    category="tool_use",
    prompt="What is the capital of France, and roughly how many people live there?",
    system="Use the available tools. Make one tool call at a time and answer at the end.",
    tools=GEO_TOOLS,
    tool_results={
        "lookup_capital": '{"country":"France","capital":"Paris"}',
        "lookup_population": '{"city":"Paris","population":2140000}',
    },
    check={
        "tool_calls": {
            "mode": "sequence",
            "expect": [
                {"name": "lookup_capital", "args": {"country": "France"}},
                {"name": "lookup_population", "args": {"city": "Paris"}},
            ],
            "args_exact": False,
        },
        "answer": {"contains_all": ["Paris"]},
    },
    max_steps=5,
)

TOOL_ARGS = TaskDef(
    id="tool-args",
    name="Exact tool arguments",
    description="Create a note with the exact title and body given, without sending an email.",
    kind="tools",
    category="tool_use",
    prompt="Create a note titled 'Groceries' with the body 'milk, eggs'. Do not email anyone.",
    system="Use the available tools.",
    tools=NOTE_TOOLS,
    tool_results={"create_note": '{"ok":true,"id":"note_1"}', "send_email": '{"ok":true}'},
    check={
        "tool_calls": {
            "mode": "sequence",
            "expect": [{"name": "create_note", "args": {"title": "Groceries", "body": "milk, eggs"}}],
            "args_exact": True,
        }
    },
    max_steps=3,
)

INSTR_JSON = TaskDef(
    id="instr-json",
    name="Exact JSON output",
    description="Emit a single JSON object with the requested keys and values, and nothing else.",
    kind="chat",
    category="instruction_following",
    prompt=(
        "Reply with only a JSON object (no prose, no code fence) describing this deployment: service name "
        "'zephyr', three replicas, tags ['api', 'eu-west']. Use exactly the keys service, replicas, tags."
    ),
    system="Follow the output format exactly.",
    check={
        "answer": {
            "json": {
                "required_keys": ["service", "replicas", "tags"],
                "equals": {"service": "zephyr", "replicas": 3},
                "one_of": {"tags": [["api", "eu-west"], ["api", "eu-west", "prod"]]},
            }
        }
    },
)

INSTR_WORDS = TaskDef(
    id="instr-words",
    name="Constrained explanation",
    description="Explain a concept in at most 40 words, including a required term, without code.",
    kind="chat",
    category="instruction_following",
    prompt=(
        "Explain what a database index does, in at most 40 words. Your answer must include the word 'lookup'. "
        "Do not include any code or code fences."
    ),
    check={"answer": {"max_words": 40, "contains_all": ["lookup"], "not_contains": ["```"]}},
)

CONTEXT_ANSWER = TaskDef(
    id="acc-context",
    name="Answer from the provided context",
    description="Extract a fact from a passage; the answer must come from the text.",
    kind="chat",
    category="accuracy",
    prompt=(
        "Use only this passage to answer.\n\n"
        "The Zephyr sync client retries failed uploads five times with exponential backoff, caps each "
        "attempt at thirty seconds, and writes progress to the local journal before every request. "
        "Operators can raise the concurrency limit with the ZEPHYR_WORKERS variable, which defaults to four.\n\n"
        "Question: How many times does the Zephyr sync client retry failed uploads? Answer in one short sentence."
    ),
    check={"answer": {"regex": r"\b(5|five)\b", "not_contains": ["four", "3", "three"]}},
)

# A model that admits the passage is silent can say so in many ways ("does not specify", "doesn't mention",
# "not answerable", "no information"). A fixed phrase list marked correct answers wrong, so this matches a
# negation followed shortly by a word about the passage not saying, within one sentence.
_NEGATION = r"(?:\b(?:not|never|no|cannot|unable)\b|n['’]t\b)"
_SILENT_VERB = (
    r"(?:stated?|states|mention(?:s|ed)?|specif(?:y|ies|ied)|provid(?:e|es|ed)|say|says|said|given|gives?|"
    r"includ(?:e|es|ed)|available|know(?:n)?|answerable|determin(?:e|ed|able)|defin(?:e|es|ed)|"
    r"list(?:s|ed)?|indicat(?:e|es|ed)|contain(?:s|ed)?|discuss(?:es|ed)?|cover(?:s|ed)?|describ(?:e|es|ed))"
)
ABSENT_ANSWER = (
    rf"{_NEGATION}[^.!?\n]{{0,40}}\b{_SILENT_VERB}\b"
    r"|\b(?:no|without)\s+(?:\w+\s+)?(?:information|mention|data|indication|details?|value)\b"
    r"|\b(?:unknown|unanswerable|undetermined|unspecified|unstated)\b"
)

CONTEXT_TRAP = TaskDef(
    id="acc-trap",
    name="Admit missing information",
    description="When the answer is absent from the context, say so instead of inventing it.",
    kind="chat",
    category="accuracy",
    # Ask about something the passage never touches. The earlier question, about a request timeout, sat next to
    # the thirty-second cap on each attempt, so "thirty seconds" was a defensible reading rather than an invention.
    prompt=(
        "Use only this passage to answer.\n\n"
        "The Zephyr sync client retries failed uploads five times with exponential backoff, caps each "
        "attempt at thirty seconds, and writes progress to the local journal before every request. "
        "Operators can raise the concurrency limit with the ZEPHYR_WORKERS variable, which defaults to four.\n\n"
        "Question: Which compression algorithm does the Zephyr sync client use for uploads? "
        "Answer in one short sentence."
    ),
    check={"answer": {"regex": ABSENT_ANSWER}},
)

REASON_MATH = TaskDef(
    id="reason-math",
    name="Reasoning puzzle",
    description="A verifiable arithmetic word problem that punishes pattern-matching.",
    kind="chat",
    category="accuracy",
    prompt=(
        "A bat and a ball cost $1.10 together. The bat costs $1.00 more than the ball. "
        "How much does the ball cost? Answer with the amount only."
    ),
    check={"answer": {"regex": r"(0?\.05|5\s*cents)"}},
)

TOOL_TASKS = (TOOL_PICK, TOOL_NO_CALL, TOOL_SEQUENCE, TOOL_ARGS)
CHAT_TASKS = (INSTR_JSON, INSTR_WORDS, CONTEXT_ANSWER, CONTEXT_TRAP, REASON_MATH)
