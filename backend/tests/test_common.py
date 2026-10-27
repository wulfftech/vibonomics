from vibonomics.benchmarks.matching import split_effort
from vibonomics.catalog.modelsdev import normalize_model_key
from vibonomics.jobs import _percentiles
from vibonomics.providers.common import parse_markdown_tables, parse_money


def test_parse_money():
    assert parse_money("$1.25 / MTok") == 1.25
    assert parse_money("Free of charge") == 0.0
    assert parse_money("—") is None
    assert parse_money("0.3") == 0.3


def test_markdown_tables():
    md = "intro\n\n| Model | Input | Output |\n|---|---|---|\n| **Foo** | $1 | $2 |\n| [Bar](x) | $3 | $4 |\n"
    tables = parse_markdown_tables(md)
    assert tables == [
        [{"Model": "Foo", "Input": "$1", "Output": "$2"}, {"Model": "Bar", "Input": "$3", "Output": "$4"}]
    ]


def test_normalize_model_key():
    assert normalize_model_key("anthropic/claude-opus-5.5") == normalize_model_key("claude-opus-5-5")
    assert normalize_model_key("gemma-4-31b-it:free") == "gemma-4-31b-it"
    assert normalize_model_key("claude-opus-5-5-20260921") == "claude-opus-5-5"


def test_split_effort():
    assert split_effort("Claude Opus 5.5 (Max)") == ("Claude Opus 5.5", "max")
    assert split_effort("gpt-6_xhigh") == ("gpt-6", "xhigh")
    assert split_effort("plain-model") == ("plain-model", "")


def test_percentiles():
    assert _percentiles([1, 2, 3], True) == [0.0, 50.0, 100.0]
    assert _percentiles([1, 2, 3], False) == [100.0, 50.0, 0.0]
    assert _percentiles([5], True) == [50.0]
