"""Deterministic graders for answer and tool-use tasks.

A task carries a JSON-serialisable ``check`` spec so results are reproducible without a judge.
Scores are 0-100; partial credit is given where the spec allows it.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _answer_matches(answer: str, spec: dict[str, Any]) -> tuple[bool, str]:
    text = answer.strip()
    if not text:
        return False, "empty answer"
    if "equals" in spec:
        expected = str(spec["equals"]).strip()
        ok = _norm(text) == _norm(expected) or _norm(text).strip(".!\"'") == _norm(expected).strip(".!\"'")
        return ok, f"expected exactly {expected!r}"
    if "contains_all" in spec:
        missing = [v for v in spec["contains_all"] if _norm(v) not in _norm(text)]
        return not missing, "missing: " + ", ".join(missing) if missing else "all required strings present"
    if "any_of" in spec:
        found = [v for v in spec["any_of"] if _norm(v) in _norm(text)]
        return bool(found), "expected one of: " + ", ".join(spec["any_of"])
    if "regex" in spec:
        ok = re.search(spec["regex"], text, re.IGNORECASE | re.MULTILINE) is not None
        return ok, f"expected to match /{spec['regex']}/"
    return False, "unknown answer spec"


def grade_answer(answer: str, check: dict[str, Any]) -> tuple[float, str]:
    """Grade a plain chat answer. Supports equals / contains_all / any_of / regex, plus extras."""
    spec = check.get("answer") or {}
    if not spec:
        return 0.0, "no check defined"
    has_text_expectation = any(key in spec for key in ("equals", "contains_all", "any_of", "regex"))
    if has_text_expectation:
        ok, detail = _answer_matches(answer, spec)
        score = 100.0 if ok else 0.0
    else:
        score, detail = 100.0, "format constraints only"

    for banned in spec.get("not_contains") or []:
        if _norm(banned) in _norm(answer):
            score = 0.0
            detail += f"; contains forbidden text {banned!r}"
    if spec.get("max_words") and len(answer.split()) > int(spec["max_words"]):
        score = 0.0
        detail += f"; over {spec['max_words']} words"
    if spec.get("min_words") and len(answer.split()) < int(spec["min_words"]):
        score = 0.0
        detail += f"; under {spec['min_words']} words"
    if "json" in spec:
        try:
            parsed = json.loads(_strip_fence(answer))
        except ValueError as exc:
            return 0.0, f"answer was not valid JSON ({exc})"
        ok_json, json_detail = _json_matches(parsed, spec["json"])
        if not ok_json:
            return 0.0, json_detail
        detail += "; " + json_detail
    if "lines" in spec:
        ok_lines, lines_detail = _lines_match(answer, spec["lines"])
        if not ok_lines:
            score = 0.0
        detail += "; " + lines_detail
    return score, detail


def _lines_match(answer: str, spec: dict[str, Any]) -> tuple[bool, str]:
    """Per-line format rules: ``count``, ``prefix``, ``min_words``/``max_words`` (not counting the prefix),
    ``forbidden_letters``, ``forbidden_words`` and ``last_line_ends_with``. Blank lines are ignored."""
    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    if "count" in spec and len(lines) != int(spec["count"]):
        return False, f"expected {spec['count']} lines, got {len(lines)}"
    prefix = str(spec.get("prefix") or "")
    for number, line in enumerate(lines, start=1):
        if prefix and not line.startswith(prefix):
            return False, f"line {number} does not start with {prefix!r}"
        words = line[len(prefix) :].split()
        if spec.get("min_words") and len(words) < int(spec["min_words"]):
            return False, f"line {number} has {len(words)} words, under {spec['min_words']}"
        if spec.get("max_words") and len(words) > int(spec["max_words"]):
            return False, f"line {number} has {len(words)} words, over {spec['max_words']}"
        for letter in str(spec.get("forbidden_letters") or ""):
            if letter.lower() in line.lower():
                return False, f"line {number} contains the letter {letter!r}"
        for word in spec.get("forbidden_words") or []:
            if re.search(rf"\b{re.escape(str(word))}\b", line, re.IGNORECASE):
                return False, f"line {number} contains the word {word!r}"
    ending = spec.get("last_line_ends_with")
    if ending and (not lines or not lines[-1].lower().endswith(str(ending).lower())):
        return False, f"the last line does not end with {ending!r}"
    return True, "line rules met"


def _strip_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text.strip()


def _json_matches(parsed: Any, spec: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(parsed, dict):
        return False, "JSON was not an object"
    required = spec.get("required_keys") or []
    missing = [k for k in required if k not in parsed]
    if missing:
        return False, "missing keys: " + ", ".join(missing)
    for key, expected in (spec.get("equals") or {}).items():
        if parsed.get(key) != expected:
            return False, f"{key} was {parsed.get(key)!r}, expected {expected!r}"
    for key, values in (spec.get("one_of") or {}).items():
        if parsed.get(key) not in values:
            return False, f"{key} was {parsed.get(key)!r}, expected one of {values}"
    for key, pattern in (spec.get("patterns") or {}).items():
        if not isinstance(parsed.get(key), str) or re.search(pattern, parsed[key]) is None:
            return False, f"{key} did not match /{pattern}/"
    return True, "JSON matched the schema"


def grade_tool_calls(tool_calls: list[dict[str, Any]], answer: str, check: dict[str, Any]) -> tuple[float, str]:
    """Grade tool use. ``expect`` is a list of {name, args} in call order."""
    spec = check.get("tool_calls") or {}
    expect = spec.get("expect") or []
    if spec.get("no_tools"):
        if tool_calls:
            return 0.0, f"called {len(tool_calls)} tool(s); none were appropriate"
        answer_score, detail = grade_answer(answer, check)
        return answer_score, "no tools called; " + detail

    if not tool_calls:
        return 0.0, "no tool calls made"
    mode = spec.get("mode", "first")
    if mode == "first":
        call = tool_calls[0]
        ok, detail = _call_matches(call, expect[0] if expect else {}, spec)
        return (100.0 if ok else 0.0), detail
    calls = tool_calls[: len(expect)] if mode == "sequence" else tool_calls
    if mode == "sequence" and len(tool_calls) < len(expect):
        return 0.0, f"expected {len(expect)} calls, got {len(tool_calls)}"
    matched = 0
    details = []
    for index, wanted in enumerate(expect):
        if index >= len(calls):
            details.append(f"{wanted.get('name')}: not called")
            continue
        ok, detail = _call_matches(calls[index], wanted, spec)
        matched += 1 if ok else 0
        details.append(f"{wanted.get('name')}: {detail}")
    return round(100.0 * matched / len(expect), 1) if expect else 0.0, "; ".join(details)


def _call_matches(call: dict[str, Any], wanted: dict[str, Any], spec: dict[str, Any]) -> tuple[bool, str]:
    name_ok = call.get("name") == wanted.get("name")
    args = call.get("arguments") or {}
    expected_args = wanted.get("args") or {}
    exact = bool(spec.get("args_exact", True))
    if exact:
        args_ok = {k: args.get(k) for k in expected_args} == expected_args
    else:
        args_ok = all(str(args.get(k)).lower() == str(v).lower() for k, v in expected_args.items())
    if name_ok and args_ok:
        return True, "matched"
    return False, f"got {call.get('name')}({json.dumps(args)[:200]})"
