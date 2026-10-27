"""Agentic coding tasks: a small virtual workspace, file tools, and real unittest grading.

Each task ships a reference solution; ``tests/evals/test_suites.py`` applies it and asserts that all
visible and hidden tests pass, so the tasks themselves can't rot silently.
"""

from __future__ import annotations

from .llm import ToolSpec
from .suites import TaskDef

TOOLS = (
    ToolSpec(
        name="list_files",
        description="List files in the task workspace.",
        parameters={"type": "object", "properties": {"path": {"type": "string", "default": "."}}},
    ),
    ToolSpec(
        name="read_file",
        description="Read a text file from the workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_file",
        description="Create or overwrite a text file with the full content.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="edit_file",
        description="Replace the first exact occurrence of old_text with new_text in a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    ),
    ToolSpec(
        name="run_tests",
        description="Run the project's unit tests and return their output.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="finish",
        description="Finish the task. Say what you changed; tests are run afterwards regardless.",
        parameters={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    ),
)

SYSTEM = (
    "You are modifying a small Python project. Use the tools to inspect and edit files, run the tests, and "
    "iterate until they pass. Do not add dependencies. When the tests pass, call finish with a short summary. "
    "If you cannot fix something, say so honestly rather than claiming success."
)


def _task(**kwargs) -> TaskDef:
    kwargs.setdefault("category", "agentic_coding")
    kwargs.setdefault("kind", "agentic")
    kwargs.setdefault("system", SYSTEM)
    kwargs.setdefault("tools", TOOLS)
    kwargs.setdefault("test_command", ("python", "-m", "unittest", "discover", "-s", "tests"))
    return TaskDef(**kwargs)


MEAN_WORKSPACE = {
    "calc.py": '''def mean(values):
    """Return the arithmetic mean of a non-empty sequence of numbers."""
    return sum(values) // len(values)
''',
    "tests/test_calc.py": """import unittest

from calc import mean


class MeanTests(unittest.TestCase):
    def test_integers(self):
        self.assertEqual(mean([1, 2, 3]), 2)

    def test_fractional_result(self):
        self.assertAlmostEqual(mean([1, 2]), 1.5)

    def test_negative_numbers(self):
        self.assertAlmostEqual(mean([-2, 0, 1]), -1 / 3)
""",
}

MEAN_HIDDEN = {
    "tests/test_mean_edges.py": """import unittest

from calc import mean


class MeanEdgeTests(unittest.TestCase):
    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            mean([])

    def test_floats(self):
        self.assertAlmostEqual(mean([0.5, 0.25]), 0.375)
""",
}

MEAN_REFERENCE = {
    "calc.py": '''def mean(values):
    """Return the arithmetic mean of a non-empty sequence of numbers."""
    if not values:
        raise ValueError("mean() needs at least one value")
    return sum(values) / len(values)
''',
}

SLUG_WORKSPACE = {
    "textutil.py": '''def slugify(text):
    """Turn a title into a URL slug, e.g. "Hello World" -> "hello-world"."""
    raise NotImplementedError
''',
    "tests/test_textutil.py": """import unittest

from textutil import slugify


class SlugifyTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_lowercases(self):
        self.assertEqual(slugify("Mixed CASE"), "mixed-case")

    def test_strips_punctuation(self):
        self.assertEqual(slugify("C++ & Rust!"), "c-rust")

    def test_collapses_separators(self):
        self.assertEqual(slugify("  lots   of   space  "), "lots-of-space")

    def test_already_slugged(self):
        self.assertEqual(slugify("already-slugged"), "already-slugged")
""",
}

SLUG_HIDDEN = {
    "tests/test_slug_edges.py": """import unittest

from textutil import slugify


class SlugifyEdgeTests(unittest.TestCase):
    def test_underscores(self):
        self.assertEqual(slugify("snake_case_name"), "snake-case-name")

    def test_empty(self):
        self.assertEqual(slugify(""), "")

    def test_numbers_kept(self):
        self.assertEqual(slugify("Version 2.0 Beta"), "version-2-0-beta")

    def test_only_punctuation(self):
        self.assertEqual(slugify("!!! ???"), "")
""",
}

SLUG_REFERENCE = {
    "textutil.py": '''import re


def slugify(text):
    """Turn a title into a URL slug, e.g. "Hello World" -> "hello-world"."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")
''',
}

REPORT_WORKSPACE = {
    "stats.py": """def total(values):
    return sum(values)
""",
    "report.py": """from stats import total


def render(values):
    return f"total={total(values)}"
""",
    "tests/test_report.py": """import unittest

from report import render
from stats import average, total


class StatsTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(total([1, 2, 3]), 6)

    def test_average(self):
        self.assertAlmostEqual(average([1, 2, 3]), 2.0)

    def test_average_empty(self):
        self.assertIsNone(average([]))


class ReportTests(unittest.TestCase):
    def test_render(self):
        self.assertEqual(render([1, 2, 3]), "total=6 avg=2.0")

    def test_render_empty(self):
        self.assertEqual(render([]), "total=0 avg=n/a")
""",
}

REPORT_REFERENCE = {
    "stats.py": """def total(values):
    return sum(values)


def average(values):
    if not values:
        return None
    return total(values) / len(values)
""",
    "report.py": """from stats import average, total


def render(values):
    avg = average(values)
    shown = "n/a" if avg is None else f"{avg:.1f}"
    return f"total={total(values)} avg={shown}"
""",
}

CONFIG_WORKSPACE = {
    "config.py": """import json


def load(path):
    with open(path) as handle:
        return json.load(handle)
""",
    "app.py": """from config import load

settings = load("config.json")
print(f"retries={settings['retries']} timeout={settings['timeout']}")
""",
    "tests/test_config.py": """import json
import os
import tempfile
import unittest

from config import DEFAULTS, load


class ConfigTests(unittest.TestCase):
    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load(os.path.join(folder, "nope.json")), DEFAULTS)

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "bad.json")
            with open(path, "w") as handle:
                handle.write("{not json")
            with self.assertRaises(ValueError):
                load(path)

    def test_file_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w") as handle:
                json.dump({"timeout": 5}, handle)
            loaded = load(path)
            self.assertEqual(loaded["timeout"], 5)
            self.assertEqual(loaded["retries"], DEFAULTS["retries"])
""",
}

CONFIG_HIDDEN = {
    "tests/test_config_app.py": """import subprocess
import sys
import unittest


class AppTests(unittest.TestCase):
    def test_app_runs_without_config_file(self):
        result = subprocess.run([sys.executable, "app.py"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retries=3", result.stdout)
        self.assertIn("timeout=30", result.stdout)
""",
}

CONFIG_REFERENCE = {
    "config.py": """import json
import os

DEFAULTS = {"retries": 3, "timeout": 30}


def load(path):
    if not os.path.exists(path):
        return dict(DEFAULTS)
    with open(path) as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid config: {exc}") from exc
    return {**DEFAULTS, **data}
""",
}

DURATION_WORKSPACE = {
    "durations.py": '''def parse_duration(text):
    """Parse a duration like '90s' or '2m' into seconds."""
    return int(text)
''',
    "tests/test_durations.py": """import unittest

from durations import parse_duration


class DurationTests(unittest.TestCase):
    def test_plain_number_is_seconds(self):
        self.assertEqual(parse_duration("90"), 90)

    def test_seconds(self):
        self.assertEqual(parse_duration("90s"), 90)

    def test_minutes(self):
        self.assertEqual(parse_duration("2m"), 120)

    def test_hours_and_minutes(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
""",
}

DURATION_HIDDEN = {
    "tests/test_duration_edges.py": """import unittest

from durations import parse_duration


class DurationEdgeTests(unittest.TestCase):
    def test_days(self):
        self.assertEqual(parse_duration("1d2h"), 93600)

    def test_mixed(self):
        self.assertEqual(parse_duration("1h30m10s"), 5410)

    def test_invalid_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("10x")

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_duration("soon")
""",
}

DURATION_REFERENCE = {
    "durations.py": '''import re

_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """Parse a duration like '90s', '2m', '1h30m' or '1d2h' into seconds."""
    text = text.strip().lower()
    if not text:
        raise ValueError("empty duration")
    if text.isdigit():
        return int(text)
    total = 0
    position = 0
    for match in re.finditer(r"(\\d+)([dhms])", text):
        if match.start() != position:
            raise ValueError(f"invalid duration: {text!r}")
        total += int(match.group(1)) * _UNITS[match.group(2)]
        position = match.end()
    if position != len(text):
        raise ValueError(f"invalid duration: {text!r}")
    return total
''',
}

FIX_BUG = _task(
    id="agentic-fix-mean",
    name="Fix a failing function",
    description="A mean() helper truncates fractional results; make the existing tests pass.",
    prompt=(
        "The project's test suite fails. Find out why, fix `calc.py`, and make every test pass.\n"
        "`mean([])` must raise `ValueError`. Keep the change minimal and do not modify the tests."
    ),
    workspace=MEAN_WORKSPACE,
    hidden_tests=MEAN_HIDDEN,
    reference=MEAN_REFERENCE,
    judge_rubric=(
        "Did the model find the real cause (integer division) and fix it minimally? Did it run the tests? "
        "Penalise changes to the tests, invented APIs, or claims of success that the test output contradicts."
    ),
)

IMPLEMENT = _task(
    id="agentic-slugify",
    name="Implement a function from a spec",
    description="Implement slugify() exactly as described, including edge cases.",
    prompt=(
        "Implement `slugify(text)` in `textutil.py`.\n\n"
        "Rules: lowercase the text; replace every run of characters that are not a-z or 0-9 with a single "
        "hyphen; strip leading and trailing hyphens. Examples: 'Hello World' -> 'hello-world', "
        "'C++ & Rust!' -> 'c-rust', 'Version 2.0 Beta' -> 'version-2-0-beta', '' -> ''. "
        "Run the tests and make them pass."
    ),
    workspace=SLUG_WORKSPACE,
    hidden_tests=SLUG_HIDDEN,
    reference=SLUG_REFERENCE,
    judge_rubric=(
        "Is the implementation a clean, direct translation of the spec? Does it handle punctuation runs and "
        "empty input without crashing? Penalise regexes that only handle the visible examples."
    ),
)

MULTI_FILE = _task(
    id="agentic-report",
    name="Add an average across two files",
    description="Add stats.average() and use it in report.render() with exact formatting.",
    prompt=(
        "Add an `average(values)` function to `stats.py` that returns the mean as a float, or None for an "
        "empty list. Then update `report.render(values)` so it returns 'total=<sum> avg=<mean>' with the mean "
        "formatted to one decimal place, or 'n/a' when the list is empty (e.g. 'total=6 avg=2.0'). "
        "Run the tests and make them pass."
    ),
    workspace=REPORT_WORKSPACE,
    hidden_tests={},
    reference=REPORT_REFERENCE,
    max_steps=12,
    judge_rubric=(
        "Did the model update both files coherently, keep the existing total() behaviour, and match the exact "
        "output format? Penalise hard-coded results or changing the tests."
    ),
)

BUG_REPORT = _task(
    id="agentic-config-defaults",
    name="Fix a crash from a bug report",
    description="A CLI crashes when config.json is absent; add defaults without hiding invalid config.",
    prompt=(
        "Bug report: 'Running `python app.py` in a fresh directory crashes with FileNotFoundError.'\n\n"
        "Fix `config.py` so a missing file returns the defaults (retries=3, timeout=30), a partial file is "
        "merged over those defaults, and malformed JSON still raises ValueError. Export the defaults as "
        "`DEFAULTS`. Run the tests and make them pass."
    ),
    workspace=CONFIG_WORKSPACE,
    hidden_tests=CONFIG_HIDDEN,
    reference=CONFIG_REFERENCE,
    max_steps=12,
    judge_rubric=(
        "Does the fix handle missing files and partial overrides while still surfacing invalid JSON? "
        "Penalise silently swallowing all errors or catching Exception broadly."
    ),
)

REFACTOR = _task(
    id="agentic-durations",
    name="Extend a parser",
    description="Extend parse_duration() to support d/h/m/s components and reject bad input.",
    prompt=(
        "Extend `parse_duration(text)` in `durations.py`. It must accept a plain integer (seconds) or a "
        "combination of components in descending order, e.g. '90s', '2m', '1h30m', '1d2h', '1h30m10s'. "
        "Units are d=86400, h=3600, m=60, s=1. Anything else (unknown units, out-of-order or duplicate "
        "components, empty text) must raise ValueError. Run the tests and make them pass."
    ),
    workspace=DURATION_WORKSPACE,
    hidden_tests=DURATION_HIDDEN,
    reference=DURATION_REFERENCE,
    max_steps=12,
    judge_rubric=(
        "Is the parser general rather than a chain of ifs for the examples? Does it reject out-of-order or "
        "duplicate components? Penalise accepting garbage."
    ),
)

AGENTIC_TASKS = (FIX_BUG, IMPLEMENT, MULTI_FILE, BUG_REPORT, REFACTOR)
