"""Find out what OpenRouter's preset API really does, so the guardrail settings are built on facts.

Run it by hand against a TEST workspace, with a key that has no spend:

    OPENROUTER_API_KEY=... OPENROUTER_MANAGEMENT_KEY=... python scripts/preset_probe.py --out probe-report.json

OpenRouter takes a preset write only under a read-write management key, so OPENROUTER_MANAGEMENT_KEY (a key of the
same test workspace) is used for the writes; every read and every completion call uses OPENROUTER_API_KEY.

It creates at most two presets (``vibonomics-probe-a`` and ``vibonomics-probe-b``, reused as new versions: the API
has no delete) and makes at most ``--max-live-calls`` completion calls, all to free models, ``max_tokens`` 8. Every
request is a step in the JSON report (what was asked, the body sent, the status, a scrubbed excerpt of the answer, and
what was observed); a short summary of the open questions is printed at the end.

The key comes from the environment only. It is never an argument, never written to the report and scrubbed, with
anything shaped like an OpenRouter key, from every line printed.

Exit codes: 0 the probe ran (an individual step may still have failed: that is a finding), 1 it could not start
(the key was refused or OpenRouter was unreachable), 2 it refused to run (no key, a bad base URL, or the workspace
holds presets that are not the probe's own).
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

DEFAULT_BASE = "https://openrouter.ai/api/v1"
KEY_ENV = "OPENROUTER_API_KEY"
# OpenRouter takes a preset write only under a read-write management key (since 2026-11-07); this one is used for the
# writes alone. Without it every write goes out under KEY_ENV, which OpenRouter now answers with HTTP 401.
WRITE_KEY_ENV = "OPENROUTER_MANAGEMENT_KEY"
PROBE_PREFIX = "vibonomics-probe-"
SLUG_A = PROBE_PREFIX + "a"
SLUG_B = PROBE_PREFIX + "b"
PROBE_SLUGS = (SLUG_A, SLUG_B)
EXCERPT_CHARS = 600
LIVE_MAX_TOKENS = 8
LIVE_PROMPT = "Reply with the single word: ok"
# Long enough that a cap of 3 tokens visibly cuts it, short enough that a cap of 8 costs nothing.
COUNT_PROMPT = "Count from one to twenty in words, separated by commas."
STEALTH_ID = "stealth/crouton-alpha"
# Only used to write the first four experiments' presets when the public model list cannot be read. They are never
# verified free, so no live call can ever go to them.
FALLBACK_MODELS = ("openai/gpt-oss-20b:free", "qwen/qwen3-coder:free")
FALLBACK_TAGS = ("deepinfra",)
SYSTEM_ONE = "PROBE system prompt one"
SYSTEM_THREE = "PROBE system prompt three"
USER_MESSAGE = "Vibonomics preset probe. This request only makes a preset version."
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
REDACTED = "[redacted]"
MAX_PEELS = 3
# Most free models have a single provider, so finding one with two takes some looking.
MAX_A_LOOKUPS = 25
MAX_B_LOOKUPS = 8
ECHO_KEYS = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "repetition_penalty",
    "parameters",
    "preset",
)
# OpenRouter may spell a parameter another way when it stores it.
ALIASES: dict[str, tuple[str, ...]] = {
    "max_tokens": ("max_completion_tokens",),
    "max_completion_tokens": ("max_tokens",),
}

QUESTIONS: tuple[tuple[str, str], ...] = (
    ("persisted_keys", "Which extra keys persist, and under which names"),
    ("system_prompt", "System prompt across versions (kept, nulled or replaced)"),
    ("sort_shape", "Shape of provider.sort when read back"),
    ("unknown_keys", "Unknown keys, at the top level and inside provider"),
    ("precedence", "A request's parameter against the preset's"),
    ("provider_merge", "A request's provider block against the preset's: merge or replace"),
    ("unknown_quantization", "provider.quantizations against an endpoint whose quantization is unknown"),
    ("require_parameters", "require_parameters with an unsupported parameter: the error"),
    ("write_version", "Does a write answer with the version it made"),
)


# ---- secrets -------------------------------------------------------------------------------------------------


_KEY_RE = re.compile(r"sk-or-[\w.\-]*", re.IGNORECASE)
_OTHER_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9][\w\-]{15,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[\w.~+/=\-]{8,}")


def scrub(text: str, secret: str | None = None) -> str:
    """``text`` with the key, anything shaped like an OpenRouter key, and any bearer token taken out."""
    if secret:
        text = text.replace(secret, REDACTED)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _KEY_RE.sub(REDACTED, text)
    return _OTHER_KEY_RE.sub(REDACTED, text)


class Refused(Exception):
    """A safety rail stopped a request or the whole run."""


# ---- one HTTP answer, one step of the report -------------------------------------------------------------------


@dataclass
class Reply:
    status: int | None
    text: str = ""
    body: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """A 2xx whose body carries no error: OpenRouter sometimes puts one in a 200."""
        return self.status is not None and 200 <= self.status < 300 and not _body_error(self.body)


def _body_error(body: Any) -> Any:
    return body.get("error") if isinstance(body, dict) else None


def error_text(reply: Reply) -> str:
    """The error in an answer, in one line: its message, its code when that is not just the HTTP status, and the
    upstream provider's own words when OpenRouter passes them on. The start of the body when it is none of those."""
    if reply.error:
        return reply.error
    err = _body_error(reply.body)
    if isinstance(err, dict):
        message, code, meta = err.get("message"), err.get("code"), err.get("metadata")
        text = str(message) if message not in (None, "") else ""
        if code not in (None, "") and code != reply.status:
            text = f"code {code}: {text}" if text else f"code {code}"
        raw = meta.get("raw") if isinstance(meta, dict) else None
        if raw:
            text += f" (upstream: {str(raw)[:200]})"
        if text:
            return text
    if isinstance(err, str):
        return err
    if isinstance(reply.body, dict):
        for k in ("message", "detail"):
            if isinstance(reply.body.get(k), str):
                return reply.body[k]
    return " ".join(reply.text.split())[:200]


def error_shape(reply: Reply) -> str:
    err = _body_error(reply.body)
    if isinstance(err, dict):
        return "error object with keys " + ", ".join(sorted(err))
    if err is not None:
        return f"error as a {type(err).__name__}"
    if isinstance(reply.body, dict):
        return "no error key; body keys " + ", ".join(sorted(reply.body))
    return "body is not a JSON object"


@dataclass
class Step:
    name: str
    asked: str
    method: str | None = None
    path: str | None = None
    request_body: Any = None
    status: int | None = None
    response_excerpt: str = ""
    observed: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "asked": self.asked,
            "method": self.method,
            "path": self.path,
            "request_body": self.request_body,
            "status": self.status,
            "response_excerpt": self.response_excerpt,
            "observed": self.observed,
        }
        if self.details:
            out["details"] = self.details
        return out


# ---- reading what OpenRouter answered --------------------------------------------------------------------------


def written_version(body: Any) -> Any:
    """``data.designated_version.version`` of a write's answer, or None."""
    data = body.get("data") if isinstance(body, dict) else None
    live = data.get("designated_version") if isinstance(data, dict) else None
    return live.get("version") if isinstance(live, dict) else None


@dataclass
class PresetView:
    reply: Reply
    config: dict[str, Any] | None
    system_prompt: str | None
    version: Any


def parse_preset(reply: Reply) -> PresetView:
    data = reply.body.get("data") if isinstance(reply.body, dict) else None
    live = data.get("designated_version") if isinstance(data, dict) else None
    live = live if isinstance(live, dict) else {}
    config = live.get("config")
    prompt = live.get("system_prompt")
    return PresetView(
        reply=reply,
        config=config if isinstance(config, dict) else None,
        system_prompt=prompt if isinstance(prompt, str) and prompt else None,
        version=live.get("version"),
    )


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) < 1e-9
    return a == b


def _flat(cfg: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in cfg.items():
        if key == "provider" and isinstance(value, dict) and value:
            for sub, subvalue in value.items():
                out[f"provider.{sub}"] = subvalue
        else:
            out[key] = value
    return out


@dataclass
class Outcome:
    key: str
    status: str  # kept, reordered, changed, renamed, renamed_changed, dropped
    stored_as: str | None
    sent: Any
    stored: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "stored_as": self.stored_as,
            "sent": self.sent,
            "stored": self.stored,
        }


def compare_config(sent: Mapping[str, Any], stored: Mapping[str, Any]) -> tuple[list[Outcome], dict[str, Any]]:
    """For each key sent: kept, changed, renamed or dropped in the stored config; plus the keys OpenRouter added."""
    s, t = _flat(sent), _flat(stored)
    outcomes: list[Outcome] = []
    claimed: set[str] = set()
    for key, value in s.items():
        if key in t:
            claimed.add(key)
            if _same(value, t[key]):
                status = "kept"
            elif isinstance(value, list) and isinstance(t[key], list) and _sorted_json(value) == _sorted_json(t[key]):
                status = "reordered"
            else:
                status = "changed"
            outcomes.append(Outcome(key, status, key, value, t[key]))
            continue
        alias = next((a for a in ALIASES.get(key, ()) if a in t and a not in s), None)
        if alias is not None:
            claimed.add(alias)
            outcomes.append(
                Outcome(key, "renamed" if _same(value, t[alias]) else "renamed_changed", alias, value, t[alias])
            )
        else:
            outcomes.append(Outcome(key, "dropped", None, value, None))
    extras = {k: v for k, v in t.items() if k not in claimed and k not in s}
    return outcomes, extras


def _sorted_json(items: list[Any]) -> list[str]:
    return sorted(json.dumps(i, sort_keys=True) for i in items)


def describe_outcomes(outcomes: Sequence[Outcome], extras: Mapping[str, Any]) -> list[str]:
    """One line per key sent, then one for what OpenRouter added."""
    lines: list[str] = []
    for o in outcomes:
        if o.status == "kept":
            lines.append(f"{o.key}: kept")
        elif o.status == "reordered":
            lines.append(f"{o.key}: kept, in another order ({json.dumps(o.stored)})")
        elif o.status == "changed":
            lines.append(f"{o.key}: changed, sent {json.dumps(o.sent)} and stored {json.dumps(o.stored)}")
        elif o.status == "renamed":
            lines.append(f"{o.key}: stored under the name {o.stored_as}")
        elif o.status == "renamed_changed":
            lines.append(f"{o.key}: stored under the name {o.stored_as} with another value ({json.dumps(o.stored)})")
        else:
            lines.append(f"{o.key}: dropped, not in the stored config")
    if extras:
        lines.append("added by OpenRouter: " + ", ".join(f"{k}={json.dumps(v)}" for k, v in extras.items()))
    return lines


def summarise_outcomes(outcomes: Sequence[Outcome]) -> str:
    groups: dict[str, list[str]] = {}
    for o in outcomes:
        label = {
            "kept": "kept",
            "reordered": "kept (reordered)",
            "changed": "changed",
            "renamed": "renamed",
            "renamed_changed": "renamed and changed",
            "dropped": "dropped",
        }[o.status]
        groups.setdefault(label, []).append(f"{o.key} -> {o.stored_as}" if o.status.startswith("renamed") else o.key)
    order = ["kept", "kept (reordered)", "changed", "renamed", "renamed and changed", "dropped"]
    return "; ".join(f"{label}: {', '.join(groups[label])}" for label in order if label in groups) or "nothing compared"


def sort_shape(config: Mapping[str, Any] | None) -> str:
    """How ``provider.sort`` reads back: a string, an object such as {"by": ..., "partition": ...}, or absent."""
    provider = config.get("provider") if isinstance(config, Mapping) else None
    if not isinstance(provider, dict) or "sort" not in provider:
        return "absent"
    value = provider["sort"]
    if isinstance(value, str):
        return f"string {json.dumps(value)}"
    if isinstance(value, dict):
        return f"object {json.dumps(value, sort_keys=True)}"
    return f"{type(value).__name__} {json.dumps(value)}"


def classify_system_prompt(previous: str | None, current: str | None, sent: str | None) -> tuple[str, str]:
    """What a write did to the preset's system prompt: (code, sentence)."""
    prev, cur = previous or None, current or None
    if sent is not None:
        if cur == sent:
            return "stored", f"the system message sent was stored ({sent!r})"
        if cur is None:
            tail = ", and the earlier prompt went with it" if prev else ""
            return "not_stored", f"the system message sent was not stored: system_prompt is null or absent{tail}"
        if cur == prev:
            return "ignored", f"the system message sent was ignored: system_prompt is still {prev!r}"
        return "changed", f"system_prompt holds {cur!r}, neither what was sent nor the earlier prompt"
    if cur is None:
        if prev is None:
            return "empty", "no system message sent and none held before, so there was nothing to carry over"
        return "nulled", f"no system message sent: system_prompt went from {prev!r} to null"
    if cur == prev:
        return "kept", f"no system message sent: system_prompt was kept ({cur!r})"
    return "changed", f"no system message sent, yet system_prompt changed from {prev!r} to {cur!r}"


def classify_unknown_key(reply: Reply, stored: Mapping[str, Any] | None, key: str, nested: bool) -> tuple[str, str]:
    """Whether a made-up key was rejected, dropped or kept: (verdict, sentence)."""
    where = f"provider.{key}" if nested else key
    if not reply.ok:
        return "rejected", f"{where} was rejected: {error_text(reply)[:200]}" + (
            f" (HTTP {reply.status})" if reply.status else ""
        )
    if stored is None:
        return "inconclusive", f"{where} was accepted but the preset could not be read back"
    scope = stored.get("provider") if nested else stored
    if isinstance(scope, dict) and key in scope:
        return "kept", f"{where} was accepted and kept ({json.dumps(scope[key])})"
    return "dropped", f"{where} was accepted and silently dropped"


# ---- discovery of free models ----------------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_free_row(row: Mapping[str, Any]) -> bool:
    """A model from the public list that costs nothing: a ``:free`` id, or every listed price zero."""
    mid = row.get("id")
    if not isinstance(mid, str) or not mid:
        return False
    if mid.endswith(":free"):
        return True
    pricing = row.get("pricing")
    if not isinstance(pricing, dict):
        return False
    if _num(pricing.get("prompt")) != 0 or _num(pricing.get("completion")) != 0:
        return False
    return all(n == 0 for n in (_num(v) for v in pricing.values()) if n is not None)


def row_supports(row: Mapping[str, Any], param: str) -> bool | None:
    listed = row.get("supported_parameters")
    return param in listed if isinstance(listed, list) else None


@dataclass
class Endpoint:
    provider: str
    tag: str
    quantization: str | None
    supported: frozenset[str]
    has_list: bool
    free: bool

    def names(self) -> set[str]:
        return {_norm(self.provider), _norm(self.tag), _norm(self.tag.split("/", 1)[0])} - {""}

    def provider_id(self) -> str:
        return _norm(self.tag.split("/", 1)[0] or self.provider)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def parse_endpoints(body: Any) -> list[Endpoint]:
    data = body.get("data") if isinstance(body, dict) else None
    listed = data.get("endpoints") if isinstance(data, dict) else None
    out: list[Endpoint] = []
    for ep in listed if isinstance(listed, list) else []:
        if not isinstance(ep, dict):
            continue
        sp = ep.get("supported_parameters")
        pricing = ep.get("pricing")
        free = True
        if isinstance(pricing, dict):
            free = all(_num(pricing.get(k)) in (None, 0.0) for k in ("prompt", "completion"))
        out.append(
            Endpoint(
                provider=str(ep.get("provider_name") or ep.get("name") or ""),
                tag=str(ep.get("tag") or ""),
                quantization=ep.get("quantization") if isinstance(ep.get("quantization"), str) else None,
                supported=frozenset(x for x in sp if isinstance(x, str)) if isinstance(sp, list) else frozenset(),
                has_list=isinstance(sp, list),
                free=free,
            )
        )
    return out


def describe_endpoints(eps: Sequence[Endpoint]) -> str:
    if not eps:
        return "no endpoints listed"
    return f"{len(eps)} endpoints: " + "; ".join(
        f"{e.tag or '(no tag)'} ({e.provider or '?'}, quantization {e.quantization or 'none listed'}, "
        f"repetition_penalty {'yes' if 'repetition_penalty' in e.supported else 'no'}, "
        f"{'free' if e.free else 'PRICED'})"
        for e in eps
    )


def _usable(eps: Sequence[Endpoint]) -> list[Endpoint]:
    return [e for e in eps if e.free and e.tag]


@dataclass
class Pick:
    model_id: str
    endpoints: list[Endpoint]

    def providers(self) -> int:
        return len({e.provider_id() for e in _usable(self.endpoints)})

    def supporting(self) -> Endpoint | None:
        return next((e for e in _usable(self.endpoints) if "repetition_penalty" in e.supported), None)

    def other_than(self, ep: Endpoint) -> Endpoint | None:
        return next((e for e in _usable(self.endpoints) if e.provider_id() != ep.provider_id()), None)


@dataclass
class Found:
    """What the public model list gave: the free models, and the picks the live experiments use."""

    listed: bool = False
    free_ids: set[str] = field(default_factory=set)
    a: Pick | None = None  # a free model with an endpoint that supports repetition_penalty
    b: Pick | None = None  # a free model whose endpoints all lack it
    c: Pick | None = None  # stealth/crouton-alpha, when listed and free
    stealth_note: str = ""
    models: tuple[str, str] = FALLBACK_MODELS
    tags: tuple[str, ...] = FALLBACK_TAGS
    verified_models: bool = False


# ---- what a live call showed ------------------------------------------------------------------------------------


def completion_facts(body: Any) -> dict[str, Any]:
    """The fields of a chat completion that the live experiments read."""
    body = body if isinstance(body, dict) else {}
    choice = (body.get("choices") or [{}])[0] if isinstance(body.get("choices"), list) and body["choices"] else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return {
        "provider": body.get("provider") if isinstance(body.get("provider"), str) else None,
        "model": body.get("model") if isinstance(body.get("model"), str) else None,
        "content": message.get("content"),
        "finish_reason": choice.get("finish_reason"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def find_keys(body: Any, names: Sequence[str]) -> dict[str, Any]:
    """The first value found for each of ``names`` anywhere in ``body``: what an answer echoes about parameters."""
    found: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in names and k not in found:
                    found[k] = v
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return found


def _failed(reply: Reply) -> str:
    where = f"HTTP {reply.status}" if reply.status is not None else "no HTTP answer"
    return f"the call failed ({where}): {error_text(reply)[:300]}"


def classify_precedence(
    reply: Reply, *, preset_cap: int, request_cap: int, preset_temp: float, request_temp: float
) -> tuple[str, str]:
    """Which one wins when preset and request set the same parameter: ("request"|"preset"|"inconclusive", why).

    The response does not normally echo temperature, so max_tokens stands in: the preset caps at a number the request
    asks to exceed, and the length of the answer shows which cap held.
    """
    if not reply.ok:
        return "inconclusive", _failed(reply)
    echoed = find_keys(reply.body, ECHO_KEYS)
    temp = _num(echoed.get("temperature"))
    if temp is not None:
        if abs(temp - request_temp) < 1e-9:
            return (
                "request",
                f"the response echoes temperature {temp}, the request's value (the preset had {preset_temp})",
            )
        if abs(temp - preset_temp) < 1e-9:
            return (
                "preset",
                f"the response echoes temperature {temp}, the preset's value (the request sent {request_temp})",
            )
    facts = completion_facts(reply.body)
    head = (
        f"Echoed by the response: {json.dumps(echoed)}. " if echoed else "The response echoes none of the parameters. "
    ) + f"Temperature (preset {preset_temp}, request {request_temp}) cannot be observed, so max_tokens stands in: "
    tokens = facts["completion_tokens"]
    if not isinstance(tokens, int):
        return "inconclusive", head + "the answer carries no usage.completion_tokens."
    span = f"preset {preset_cap}, request {request_cap}; the reply used {tokens} completion tokens"
    assumes = " (assumes every parameter merges the same way)"
    if tokens > preset_cap:
        return "request", head + f"{span}, more than the preset's cap, so the request's value was used{assumes}."
    if facts["finish_reason"] == "length":
        return "preset", head + f"{span} and was cut off, so the preset's cap held over the request's{assumes}."
    return "inconclusive", head + f"{span} but ended by itself, which does not show which cap applied."


def classify_merge(reply: Reply, pinned: Endpoint, other: Endpoint | None) -> tuple[str, str]:
    """Preset pins provider.only to ``pinned``; the request orders ``other``. Who served shows merge or replace."""
    if not reply.ok:
        return "inconclusive", _failed(reply)
    served = completion_facts(reply.body)["provider"]
    if not served:
        return "inconclusive", "the answer names no provider (no `provider` field), so who served cannot be told."
    key = _norm(served)
    got = f"HTTP {reply.status}: served by {served}"
    if key in pinned.names():
        if other is None:
            return (
                "inconclusive",
                f"{got}, the pinned provider; the model has no second provider to order, so the request's provider "
                "block could have replaced the preset's and the answer would look the same.",
            )
        return (
            "merge",
            f"{got}, the provider the preset pinned with only, though the request ordered {other.tag}: "
            "the preset's provider.only survived the request's provider block (merged, or the preset wins).",
        )
    if other is not None and key in other.names():
        return (
            "replace",
            f"{got}, the provider the request ordered: the request's provider block replaced the preset's only.",
        )
    return (
        "replace",
        f"{got}, which is not the pinned provider ({pinned.tag}): "
        "the preset's only was not applied alongside the request's provider block.",
    )


def classify_merge_by_requirement(reply: Reply, refused_without_it: bool) -> tuple[str, str]:
    """The preset requires a parameter no endpoint supports, so a call fails; here the request adds a provider block.

    Still failing means the preset's require_parameters survived the request's provider block (merge, or the preset
    wins). Succeeding means the request's provider block replaced the preset's, require_parameters included. It works
    with one provider only, which the served-by test cannot.
    """
    if not refused_without_it:
        return "inconclusive", "the same preset was not refused on its own, so this call shows nothing."
    if reply.ok:
        return (
            "replace",
            f"HTTP {reply.status}: served. The preset alone is refused, so the request's provider block replaced the "
            "preset's, require_parameters with it.",
        )
    if reply.status in (400, 404):
        return (
            "merge",
            f"HTTP {reply.status}: still refused ({error_text(reply)[:200]}), so the preset's require_parameters "
            "survived the request's provider block (merged, or the preset wins).",
        )
    return "inconclusive", _failed(reply)


def classify_quantization(reply: Reply, quantization: str | None) -> tuple[str, str]:
    """A preset asks for fp8 from an endpoint whose listed quantization is ``quantization`` (unknown)."""
    listed = quantization or "not listed"
    if not reply.ok:
        if reply.status is None:
            return "inconclusive", _failed(reply)
        return (
            "excluded",
            f"HTTP {reply.status}: the endpoint (quantization {listed}) did not pass provider.quantizations ['fp8']: "
            f"{error_text(reply)[:300]}",
        )
    return (
        "passes",
        f"HTTP {reply.status}: the call was served although the endpoint's quantization is {listed} and the preset "
        "asks for fp8, so an endpoint of unknown quantization passes the filter.",
    )


def classify_require_parameters(reply: Reply, required: bool) -> tuple[str, str]:
    if reply.error:
        return "inconclusive", _failed(reply)
    if not reply.ok:
        raw = " ".join(reply.text.split())[:400]
        return (
            "error",
            f"HTTP {reply.status}, {error_shape(reply)}; message: {error_text(reply)[:300]}; body: {raw}",
        )
    facts = completion_facts(reply.body)
    served = f" by {facts['provider']}" if facts["provider"] else ""
    if required:
        return (
            "served",
            f"HTTP {reply.status}: served{served} although no listed endpoint supports repetition_penalty, "
            "so require_parameters did not block it.",
        )
    return (
        "dropped",
        f"HTTP {reply.status}: served{served}. No listed endpoint supports repetition_penalty, so it was dropped "
        "without an error (inferred: the response does not echo the parameters).",
    )


def build_summary(findings: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"question": q, "answer": str(findings.get(key) or "inconclusive")} for key, q in QUESTIONS]


# ---- the probe -------------------------------------------------------------------------------------------------


@dataclass
class WriteRead:
    sent: dict[str, Any]
    reply: Reply
    system_sent: str | None
    write_step: Step
    read_step: Step | None = None
    view: PresetView | None = None
    outcomes: list[Outcome] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    system: tuple[str, str] = ("", "")

    @property
    def accepted(self) -> bool:
        """A 2xx with no error in the body. Whether it named the version it made is a finding of its own."""
        return self.reply.ok


def blamed_keys(config: Mapping[str, Any], message: str) -> list[str]:
    """The keys of ``config`` (top level, and ``provider.x``) that an error message names. Never model(s)/provider."""
    names: list[tuple[str, str]] = []
    for key in config:
        if key not in ("model", "models", "provider"):
            names.append((key, key))
    provider = config.get("provider")
    for sub in provider if isinstance(provider, dict) else []:
        names.append((f"provider.{sub}", sub))
    found = []
    for path, word in names:
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", message):
            found.append(path)
    return found


def drop_key(config: dict[str, Any], path: str) -> None:
    if path.startswith("provider."):
        provider = config.get("provider")
        if isinstance(provider, dict):
            provider.pop(path.split(".", 1)[1], None)
            if not provider:
                config.pop("provider", None)
    else:
        config.pop(path, None)


class Probe:
    def __init__(
        self,
        client: httpx.Client,
        base: str,
        key: str,
        *,
        skip_calls: bool,
        max_live_calls: int,
        say: Callable[[str], None],
        write_key: str | None = None,
    ) -> None:
        self.client = client
        self.base = base
        self.key = key
        self.write_key = write_key or key
        self.skip_calls = skip_calls
        self.max_live_calls = max_live_calls
        self.say = say
        self.steps: list[Step] = []
        self.findings: dict[str, Any] = {}
        self.found = Found()
        self.live_calls = 0
        self.writes = 0
        self.created: set[str] = set()
        self.write_versions: list[Any] = []
        self.system_seen: dict[str, str | None] = {}
        self.working: dict[str, Any] = {}
        self.first_system: tuple[str, str] = ("", "")
        self._temps = itertools.count(30)

    # -- plumbing

    def next_temperature(self) -> float:
        """A different temperature for each write, so no two versions are the same config."""
        return round(next(self._temps) / 100, 2)

    def _guard(self, method: str, path: str, body: Any) -> None:
        """The rails that hold whatever the experiments do: what may be sent at all."""
        if method == "GET":
            return
        if method == "POST" and path in {f"/presets/{slug}/chat/completions" for slug in PROBE_SLUGS}:
            return
        if method == "POST" and path == "/chat/completions" and isinstance(body, dict):
            model = body.get("model")
            slug = model.removeprefix("@preset/") if isinstance(model, str) and model.startswith("@preset/") else None
            if slug not in PROBE_SLUGS:
                raise Refused(f"a completion for {model!r} is not allowed: only the probe presets may be called")
            if body.get("max_tokens") != LIVE_MAX_TOKENS:
                raise Refused(f"a live call must set max_tokens {LIVE_MAX_TOKENS}")
            if self.skip_calls or self.live_calls > self.max_live_calls:
                raise Refused("the live-call limit is reached")
            return
        raise Refused(f"{method} {path} is not something the probe may send")

    def _send(self, method: str, path: str, body: Any = None, *, auth: bool = True, timeout: float = 60.0) -> Reply:
        self._guard(method, path, body)
        headers = {"Accept": "application/json"}
        if auth:
            writes = method == "POST" and path.startswith("/presets/")
            headers["Authorization"] = f"Bearer {self.write_key if writes else self.key}"
        try:
            resp = self.client.request(method, self.base + path, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            return Reply(None, error=f"{type(exc).__name__}: {exc}")
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None
        return Reply(resp.status_code, resp.text, parsed)

    def record(
        self,
        name: str,
        asked: str,
        method: str | None,
        path: str | None,
        body: Any,
        reply: Reply | None,
        observed: str = "",
    ) -> Step:
        if reply is None:
            excerpt = ""
        elif reply.error:
            excerpt = f"(no HTTP answer) {reply.error}"
        else:
            excerpt = reply.text
        step = Step(
            name=name,
            asked=asked,
            method=method,
            path=path,
            request_body=body,
            status=reply.status if reply else None,
            response_excerpt=scrub(scrub(" ".join(excerpt.split()), self.key), self.write_key)[:EXCERPT_CHARS],
            observed=observed,
        )
        self.steps.append(step)
        status = "no answer" if step.status is None else f"HTTP {step.status}"
        self.say(
            f"[{len(self.steps):02d}] {name}: {status}"
            if method
            else f"[{len(self.steps):02d}] {name}: {observed[:100]}"
        )
        return step

    def note(self, name: str, asked: str, observed: str, **details: Any) -> Step:
        """A step with no request: a skip, a refusal, or a result worked out from earlier steps."""
        step = self.record(name, asked, None, None, None, None, observed)
        step.details = details
        return step

    def fetch(self, name: str, asked: str, path: str, *, auth: bool = True) -> tuple[Reply, Step]:
        reply = self._send("GET", path, auth=auth, timeout=30.0)
        return reply, self.record(name, asked, "GET", path, None, reply)

    def run_guarded(self, label: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:
            self.note(f"{label} stopped", f"Run {label}.", f"{type(exc).__name__}: {exc}")

    # -- presets

    def write(
        self, slug: str, config: Mapping[str, Any], system: str | None, name: str, asked: str
    ) -> tuple[Reply, Step]:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": USER_MESSAGE}
        ]
        # A copy, so that dropping a rejected key from `config` later does not rewrite what this step says was sent.
        body = copy.deepcopy({**config, "messages": messages})
        path = f"/presets/{slug}/chat/completions"
        self.writes += 1
        reply = self._send("POST", path, body, timeout=60.0)
        version = written_version(reply.body) if reply.ok else None
        if reply.error:
            observed = f"no HTTP answer: {reply.error}"
        elif not reply.ok:
            observed = f"rejected: HTTP {reply.status}: {error_text(reply)[:300]}"
        elif version is None:
            observed = (
                f"HTTP {reply.status}, but the answer names no version (data.designated_version.version is absent)"
            )
        else:
            observed = f"accepted: HTTP {reply.status}; the answer carries data.designated_version.version = {version}"
        if reply.ok:
            self.created.add(slug)
            self.write_versions.append(version)
        return reply, self.record(name, asked, "POST", path, body, reply, observed)

    def read_preset(self, slug: str, name: str, asked: str) -> tuple[PresetView, Step]:
        reply, step = self.fetch(name, asked, f"/presets/{slug}")
        return parse_preset(reply), step

    def write_and_read(
        self, slug: str, config: Mapping[str, Any], system: str | None, name: str, asked: str, *, peel: bool = False
    ) -> WriteRead:
        """Write a version, then read the preset back and compare what came back with what was sent."""
        sent = copy.deepcopy(dict(config))
        reply, step = self.write(slug, sent, system, f"{name}: write", asked)
        attempts = 1
        while peel and not reply.ok and attempts <= MAX_PEELS and reply.status is not None:
            if not 400 <= reply.status < 500 or reply.status in (401, 403, 404, 429):
                break
            blamed = blamed_keys(sent, error_text(reply))
            if not blamed:
                break
            for path in blamed:
                drop_key(sent, path)
            step.observed += f"; the message names {', '.join(blamed)}, so the next attempt leaves them out"
            attempts += 1
            reply, step = self.write(
                slug, sent, system, f"{name}: write, attempt {attempts} without {', '.join(blamed)}", asked
            )
        out = WriteRead(sent=sent, reply=reply, system_sent=system, write_step=step)
        if not out.accepted:
            return out
        view, rstep = self.read_preset(
            slug, f"{name}: read back", f"Read {slug} back with GET /presets/{slug} to see what was stored."
        )
        out.view, out.read_step = view, rstep
        if view.config is None:
            rstep.observed = "the answer carries no designated_version.config, so nothing could be compared"
            return out
        out.outcomes, out.extras = compare_config(sent, view.config)
        out.system = classify_system_prompt(self.system_seen.get(slug), view.system_prompt, system)
        self.system_seen[slug] = view.system_prompt
        lines = describe_outcomes(out.outcomes, out.extras)
        if out.system[0] != "empty":
            lines.append(f"system_prompt: {out.system[1]}")
        if sort_shape(view.config) != "absent":
            lines.append(f"sort as read back: {sort_shape(view.config)}")
        lines.append(f"designated version {view.version}; the write answered {written_version(reply.body)}")
        rstep.observed = "\n".join(lines)
        rstep.details = {
            "outcomes": [o.to_dict() for o in out.outcomes],
            "added_by_openrouter": out.extras,
            "stored_config": view.config,
            "system_prompt": view.system_prompt,
        }
        return out

    # -- discovery

    def discover(self) -> None:
        reply, step = self.fetch(
            "discover: public model list",
            "List the public models with GET /models (no key sent) to find free ones for the presets and live calls.",
            "/models",
            auth=False,
        )
        rows = (
            [r for r in (reply.body.get("data") or []) if isinstance(r, dict)]
            if isinstance(reply.body, dict) and isinstance(reply.body.get("data"), list)
            else []
        )
        found = self.found
        if not rows:
            step.observed = "no models could be read: the presets use stand-in model ids and no live call will be made"
            return
        found.listed = True
        free = sorted((r for r in rows if is_free_row(r)), key=lambda r: r["id"])
        found.free_ids = {r["id"] for r in free}
        stealth = next((r for r in rows if r.get("id") == STEALTH_ID), None)
        if stealth is None:
            found.stealth_note = "not listed"
        elif is_free_row(stealth):
            found.stealth_note = "listed and free"
        else:
            found.stealth_note = "listed, but not free, so no live call will go to it"
        step.observed = f"{len(rows)} models listed, {len(free)} free. {STEALTH_ID}: {found.stealth_note}."

        # A model is only picked when every endpoint it lists is free: a call that is not pinned to one endpoint could
        # land on any of them.
        a_rows = [r for r in free if row_supports(r, "repetition_penalty") is not False]
        for row in a_rows[:MAX_A_LOOKUPS]:
            eps = self.lookup(row["id"])
            pick = Pick(row["id"], eps)
            if pick.supporting() is None or not all(e.free for e in eps):
                continue
            if found.a is None or pick.providers() > found.a.providers():
                found.a = pick
            if pick.providers() >= 2:
                break
        b_rows = [
            r
            for r in free
            if row_supports(r, "repetition_penalty") is not True and (found.a is None or r["id"] != found.a.model_id)
        ]
        for row in b_rows[:MAX_B_LOOKUPS]:
            eps = self.lookup(row["id"])
            usable = _usable(eps)
            lacks = (
                usable and all(e.has_list for e in eps) and not any("repetition_penalty" in e.supported for e in eps)
            )
            if lacks and all(e.free for e in eps):
                found.b = Pick(row["id"], eps)
                break
        if stealth is not None and is_free_row(stealth):
            eps = self.lookup(STEALTH_ID)
            if all(e.free for e in eps):
                found.c = Pick(STEALTH_ID, eps)
            else:
                found.stealth_note = "listed and free, but an endpoint of it is priced, so no live call will go to it"

        ids = [p.model_id for p in (found.a, found.b) if p is not None]
        ids += [r["id"] for r in free if r["id"] not in ids]
        if len(ids) >= 2:
            found.models, found.verified_models = (ids[0], ids[1]), True
        if found.a is not None:
            found.tags = tuple(dict.fromkeys(e.tag for e in _usable(found.a.endpoints)))[:2] or FALLBACK_TAGS
        self.note(
            "discover: choices",
            "Choose the models and providers the experiments use.",
            f"presets in experiments 1 to 4 use {found.models[0]} and {found.models[1]}"
            f"{'' if found.verified_models else ' (stand-ins, not verified free)'}; "
            f"repetition_penalty supported: {found.a.model_id if found.a else 'none found'}; "
            f"not supported: {found.b.model_id if found.b else 'none found'}; stealth: {found.stealth_note}",
        )

    def lookup(self, model_id: str) -> list[Endpoint]:
        reply, step = self.fetch(
            f"discover: endpoints of {model_id}",
            f"Read the public endpoint list of {model_id} for provider tags and supported parameters.",
            f"/models/{quote(model_id, safe='/:')}/endpoints",
            auth=False,
        )
        eps = parse_endpoints(reply.body) if reply.ok else []
        step.observed = describe_endpoints(eps) if reply.ok else _failed(reply)
        return eps

    # -- experiments 1 to 4

    def base_config(self) -> dict[str, Any]:
        m1, m2 = self.found.models
        only = list(self.found.tags)
        return {
            "model": m1,
            "models": [m1, m2],
            "provider": {
                "only": only,
                "order": list(reversed(only)),
                "sort": "price",
                "quantizations": ["fp8", "bf16"],
                "ignore": ["azure"],
                "require_parameters": False,
            },
            "temperature": self.next_temperature(),
            "top_p": 0.9,
            "repetition_penalty": 1.05,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.1,
            "route": "sort",
        }

    def exp1(self) -> None:
        asked = (
            "Create vibonomics-probe-a with POST /presets/{slug}/chat/completions setting model, models, provider "
            "(only, order, sort as the string 'price', quantizations, ignore, require_parameters false), temperature, "
            "top_p, max_tokens, repetition_penalty, frequency_penalty, presence_penalty, route 'sort' and a system "
            "message, then read it back."
        )
        sent = {**self.base_config(), "max_tokens": 64}
        first = self.write_and_read(SLUG_A, sent, SYSTEM_ONE, "1a", asked, peel=True)
        self.working = first.sent
        variant = {
            k: v for k, v in first.sent.items() if k not in ("max_tokens", "max_completion_tokens", "temperature")
        }
        variant.update({"max_completion_tokens": 64, "temperature": self.next_temperature()})
        second = self.write_and_read(
            SLUG_A,
            variant,
            SYSTEM_ONE,
            "1b",
            "Write another version that sets max_completion_tokens instead of max_tokens, to see which name is stored.",
        )
        parts: list[str] = []
        if not first.accepted:
            parts.append(f"1a: {first.write_step.observed}")
        left_out = [k for k in _flat(sent) if k not in _flat(first.sent)]
        if left_out:
            parts.append(f"OpenRouter rejected these, so later writes leave them out: {', '.join(left_out)}")
        for label, wr in (("1a (max_tokens)", first), ("1b (max_completion_tokens)", second)):
            if wr.view is not None and wr.view.config is not None:
                parts.append(f"{label}: {summarise_outcomes(wr.outcomes)}")
                if wr.extras:
                    parts.append(f"{label} added by OpenRouter: {', '.join(sorted(wr.extras))}")
        names = [
            f"sent {o.key}, stored as {o.stored_as or 'nothing (dropped)'}"
            for wr in (first, second)
            for o in wr.outcomes
            if o.key in ALIASES
        ]
        if names:
            parts.append("token cap: " + "; ".join(names))
        self.findings["persisted_keys"] = " | ".join(parts)
        for wr in (first, second):
            if wr.view is not None and wr.view.config is not None and "sort_shape" not in self.findings:
                self.findings["sort_shape"] = f"sent the string 'price'; read back as {sort_shape(wr.view.config)}"
        if first.view is not None:
            self.first_system = first.system

    def exp2(self) -> None:
        v2 = self.write_and_read(
            SLUG_A,
            {**self.working, "temperature": self.next_temperature()},
            None,
            "2a",
            "Write a version with NO system message and a changed temperature, to see if system_prompt carries over.",
        )
        v3 = self.write_and_read(
            SLUG_A,
            {**self.working, "temperature": self.next_temperature()},
            SYSTEM_THREE,
            "2b",
            "Write a version that sends a different system message, to see whether it replaces the stored one.",
        )
        parts = []
        if self.first_system[1]:
            parts.append(f"first write: {self.first_system[1]}")
        if v2.view is not None:
            parts.append(f"a version with no system message: {v2.system[1]}")
        if v3.view is not None:
            parts.append(f"a version with a different system message: {v3.system[1]}")
        if not parts:
            return
        answer = "; ".join(parts)
        if self.first_system[0] != "stored" and v2.system[0] in ("empty", ""):
            answer = "inconclusive about carry-over, because no system prompt was ever stored. " + answer
        self.findings["system_prompt"] = answer

    def exp3(self) -> None:
        asked = "Send one made-up key {where} on top of the working config, to see if it is dropped, kept or rejected."
        top = {**self.working, "temperature": self.next_temperature(), "probe_unknown_key": "probe"}
        nested = {**self.working, "temperature": self.next_temperature()}
        nested["provider"] = {**(nested.get("provider") or {}), "probe_unknown_nested": "probe"}
        results = []
        for label, cfg, key, is_nested, where in (
            ("3a", top, "probe_unknown_key", False, "at the top level (probe_unknown_key)"),
            ("3b", nested, "probe_unknown_nested", True, "inside provider (probe_unknown_nested)"),
        ):
            wr = self.write_and_read(SLUG_A, cfg, SYSTEM_THREE, label, asked.format(where=where))
            stored = wr.view.config if wr.view is not None else None
            _verdict, text = classify_unknown_key(wr.reply, stored, key, is_nested)
            target = wr.read_step or wr.write_step
            target.observed += "\n" + text
            results.append(text)
        self.findings["unknown_keys"] = "; ".join(results)

    def exp4(self) -> None:
        view, rstep = self.read_preset(
            SLUG_A, "4a: read the preset", f"Read {SLUG_A} to see which version is designated."
        )
        reply, vstep = self.fetch(
            "4b: versions",
            f"List the versions of {SLUG_A} with GET /presets/{SLUG_A}/versions.",
            f"/presets/{SLUG_A}/versions",
        )
        rstep.observed = f"designated version: {view.version}"
        data = reply.body.get("data") if isinstance(reply.body, dict) else None
        rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        numbers = sorted(r["version"] for r in rows if isinstance(r.get("version"), int))
        flagged = [
            r.get("version")
            for r in rows
            if any(r.get(f) is True for f in ("designated", "is_designated", "is_current", "current"))
        ]
        returned = [v for v in self.write_versions if v is not None]
        if not reply.ok:
            vstep.observed = _failed(reply)
        else:
            vstep.observed = (
                f"{len(numbers)} versions listed: {numbers}. Designated: {view.version}"
                + (
                    f" (the list flags {flagged} too)"
                    if flagged
                    else " (read from the preset: the list carries no flag)"
                )
                + f". Writes that answered data.designated_version.version: {len(returned)} of {self.writes} "
                f"({returned})."
            )
        self.findings["versions"] = {"listed": numbers, "designated": view.version, "flagged": flagged}
        if not self.write_versions:
            self.findings["write_version"] = "inconclusive (no write was accepted)"
        elif len(returned) == len(self.write_versions):
            self.findings["write_version"] = f"yes: every accepted write answered with the version it made ({returned})"
        else:
            self.findings["write_version"] = (
                f"no: only {len(returned)} of {len(self.write_versions)} accepted writes named a version"
            )

    # -- experiment 5: live calls

    def live_call(
        self,
        name: str,
        asked: str,
        slug: str,
        view: PresetView | None,
        extra: Mapping[str, Any],
        prompt: str = LIVE_PROMPT,
    ) -> tuple[Reply, Step] | None:
        """One completion through a probe preset, only when the preset holds free models and the cap allows it."""
        if self.skip_calls:
            self.note(name, asked, "skipped: --skip-calls")
            return None
        if self.live_calls >= self.max_live_calls:
            self.note(name, asked, f"skipped: the limit of {self.max_live_calls} live calls is reached")
            return None
        config = view.config if view is not None else None
        models = _models_of(config)
        stray = [m for m in models if m not in self.found.free_ids]
        if config is None or not models or stray:
            why = (
                "the preset could not be read back"
                if config is None
                else f"{', '.join(stray) or 'no model'} is not a verified free model"
            )
            self.note(name, asked, f"refused: {why}, so no call was made (live calls go to free models only)")
            return None
        body = {
            "model": f"@preset/{slug}",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": LIVE_MAX_TOKENS,
            **extra,
        }
        self.live_calls += 1
        reply = self._send("POST", "/chat/completions", body, timeout=90.0)
        step = self.record(name, asked, "POST", "/chat/completions", body, reply)
        step.observed = "answered" if reply.ok else _failed(reply)
        return reply, step

    def prepare_b(self, name: str, config: Mapping[str, Any], asked: str) -> PresetView | None:
        wr = self.write_and_read(SLUG_B, config, None, name, asked)
        if not wr.accepted or wr.view is None:
            return None
        lost = [o.key for o in wr.outcomes if o.status == "dropped"]
        if lost and wr.read_step is not None:
            wr.read_step.observed += (
                f"\nNot kept by OpenRouter, so the call after this does not test it: {', '.join(lost)}"
            )
        return wr.view

    def exp5(self) -> None:
        if self.skip_calls:
            self.note("5: live routing", "Run the live routing calls.", "skipped: --skip-calls")
            return
        if not self.found.free_ids:
            self.note(
                "5: live routing",
                "Run the live routing calls.",
                "skipped: no model is known to be free (the public model list could not be read, or lists none)",
            )
            return
        for part in (self.exp5_precedence, self.exp5_merge, self.exp5_quantization, self.exp5_require_parameters):
            self.run_guarded(part.__name__, part)

    def exp5_precedence(self) -> None:
        pick = self.found.a or self.found.b or self.found.c
        asked = "Precedence: the preset sets temperature 0.1 and max_tokens 3; the call asks for 1.5 and max_tokens 8."
        if pick is None:
            self.note("5i: precedence", asked, "skipped: no free model was found")
            return
        view = self.prepare_b(
            "5i setup",
            {"model": pick.model_id, "temperature": 0.1, "max_tokens": 3},
            "Write the precedence preset to vibonomics-probe-b.",
        )
        got = self.live_call("5i: precedence", asked, SLUG_B, view, {"temperature": 1.5}, COUNT_PROMPT)
        if got is None:
            return
        reply, step = got
        verdict, text = classify_precedence(
            reply, preset_cap=3, request_cap=LIVE_MAX_TOKENS, preset_temp=0.1, request_temp=1.5
        )
        step.observed = text
        self.findings["precedence"] = f"{verdict}: {text}"

    def exp5_merge(self) -> None:
        a = self.found.a
        asked = "Provider merge: the preset pins provider.only to one endpoint; the call orders another."
        pinned = a.supporting() if a is not None else None
        if a is None or pinned is None:
            self.note("5ii: provider merge", asked, "skipped: no free model with a usable endpoint was found")
            return
        other = a.other_than(pinned)
        view = self.prepare_b(
            "5ii setup",
            {"model": a.model_id, "provider": {"only": [pinned.tag]}},
            f"Write a preset that pins provider.only to {pinned.tag}.",
        )
        got = self.live_call(
            "5ii: provider merge",
            asked,
            SLUG_B,
            view,
            {"provider": {"order": [other.tag if other else "vibonomics-probe-other"]}},
        )
        if got is None:
            return
        reply, step = got
        verdict, text = classify_merge(reply, pinned, other)
        step.observed = text
        self.add_finding("provider_merge", f"{verdict} (by who served): {text}")

    def exp5_quantization(self) -> None:
        c = self.found.c
        asked = "Unknown quantization: the preset pins the stealth provider and asks quantizations ['fp8']."
        if c is None:
            self.note(
                "5iii: unknown quantization",
                asked,
                f"skipped: {STEALTH_ID} is {self.found.stealth_note or 'not listed'}",
            )
            return
        ep = next(iter(_usable(c.endpoints)), None)
        tag = ep.tag if ep else "stealth"
        view = self.prepare_b(
            "5iii setup",
            {"model": c.model_id, "provider": {"only": [tag], "quantizations": ["fp8"]}},
            f"Write a preset for {c.model_id} that pins {tag} and asks for fp8.",
        )
        got = self.live_call("5iii: unknown quantization", asked, SLUG_B, view, {})
        if got is None:
            return
        reply, step = got
        verdict, text = classify_quantization(reply, ep.quantization if ep else None)
        step.observed = text
        self.findings["unknown_quantization"] = f"{verdict}: {text}"

    def exp5_require_parameters(self) -> None:
        b = self.found.b
        asked = (
            "require_parameters: the preset sets repetition_penalty 1.1 for a model whose endpoints do not support it."
        )
        if b is None:
            self.note(
                "5iv: require_parameters", asked, "skipped: no free model without repetition_penalty support was found"
            )
            return
        results: dict[bool, str] = {}
        for label, required in (("5iv", True), ("5v", False)):
            view = self.prepare_b(
                f"{label} setup",
                {"model": b.model_id, "repetition_penalty": 1.1, "provider": {"require_parameters": required}},
                f"Write a preset with repetition_penalty 1.1 and provider.require_parameters {str(required).lower()}.",
            )
            got = self.live_call(f"{label}: require_parameters {str(required).lower()}", asked, SLUG_B, view, {})
            if got is None:
                continue
            reply, step = got
            verdict, text = classify_require_parameters(reply, required)
            step.observed = text
            results[required] = f"{verdict}: {text}"
            if required:
                self.merge_through_requirement(b, view, refused=verdict == "error")
        if results:
            self.findings["require_parameters"] = (
                f"with true: {results.get(True, 'inconclusive')} | with false: {results.get(False, 'inconclusive')}"
            )

    def merge_through_requirement(self, b: Pick, view: PresetView | None, *, refused: bool) -> None:
        """Provider merge, tested where it works with a single provider: the preset that is refused for
        require_parameters, called again with a provider block of the request's own."""
        usable = _usable(b.endpoints)
        tag = usable[0].tag if usable else "vibonomics-probe-other"
        name = "5vi: provider merge through require_parameters"
        asked = (
            "Provider merge again: the preset requires parameters no endpoint supports and is refused; the call adds "
            "its own provider block (order only). If it is served, the request's block replaced the preset's."
        )
        if not refused:
            self.note(name, asked, "skipped: the preset was not refused on its own, so the call would show nothing")
            return
        got = self.live_call(name, asked, SLUG_B, view, {"provider": {"order": [tag]}})
        if got is None:
            return
        reply, step = got
        verdict, text = classify_merge_by_requirement(reply, refused)
        step.observed = text
        self.add_finding("provider_merge", f"{verdict} (through require_parameters): {text}")

    def add_finding(self, key: str, text: str) -> None:
        self.findings[key] = f"{self.findings[key]} | {text}" if key in self.findings else text

    # -- the whole run

    def run(self) -> None:
        self.run_guarded("discovery", self.discover)
        for label, fn in (
            ("experiment 1", self.exp1),
            ("experiment 2", self.exp2),
            ("experiment 3", self.exp3),
            ("experiment 4", self.exp4),
            ("experiment 5", self.exp5),
        ):
            self.run_guarded(label, fn)


def _models_of(config: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(config, Mapping):
        return []
    out = [config["model"]] if isinstance(config.get("model"), str) else []
    models = config.get("models")
    return out + ([m for m in models if isinstance(m, str)] if isinstance(models, list) else [])


# ---- the command line ---------------------------------------------------------------------------------------------


def listing_incomplete(body: Any, rows: Sequence[Any]) -> bool:
    """Whether the preset list hints at more presets than it holds, so the workspace cannot be vouched for."""
    if not isinstance(body, dict):
        return False
    if any(body.get(k) is True for k in ("has_more", "hasMore", "more")):
        return True
    if any(body.get(k) for k in ("next", "next_cursor", "nextCursor", "next_page", "next_page_token")):
        return True
    return any(isinstance(body.get(k), int) and body[k] > len(rows) for k in ("total_count", "total", "count"))


def check_base(base: str) -> str:
    """The API base to use. The key is sent to it, so it must be https (or a local test server)."""
    parsed = urlparse(base)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
        return base.rstrip("/")
    raise Refused(f"--base must be an https URL (the key is sent to it); got {scrub(base)!r}")


def preflight(probe: Probe, allow_foreign: bool, err: Callable[[str], None]) -> int | None:
    """List the workspace's presets. An exit code when the probe must not go on, None when it may."""
    reply, step = probe.fetch(
        "preflight: list the workspace's presets",
        "List the presets already in the workspace with GET /presets, to refuse to run in a workspace that holds "
        f"anything but {PROBE_PREFIX}* presets.",
        "/presets",
    )
    rows = reply.body.get("data") if isinstance(reply.body, dict) else None
    if not reply.ok or not isinstance(rows, list):
        step.observed = _failed(reply) if not reply.ok else "the answer has no `data` list of presets"
        if reply.status in (401, 403):
            err(f"OpenRouter refused the key (HTTP {reply.status}). Nothing was changed.")
        else:
            err(f"Could not list the workspace's presets: {step.observed} Nothing was changed.")
        return EXIT_FAILED
    slugs = [str(r.get("slug") or "") if isinstance(r, dict) else "" for r in rows]
    foreign = [s for s in slugs if not s.startswith(PROBE_PREFIX)]
    incomplete = listing_incomplete(reply.body, rows)
    step.observed = (
        f"{len(slugs)} presets in the workspace"
        + (f", {len(foreign)} that are not the probe's" if foreign else ", all of them the probe's")
        + ("; the listing hints that there are more than it shows" if incomplete else "")
    )
    if (foreign or incomplete) and not allow_foreign:
        shown = ", ".join(repr(s) for s in foreign[:10]) + (" ..." if len(foreign) > 10 else "")
        err(
            "Refusing to run: "
            + (f"the workspace already holds presets that are not the probe's ({shown}). " if foreign else "")
            + ("The preset list is paginated, so the workspace cannot be checked. " if incomplete else "")
            + "The probe writes presets and the API cannot delete them, so run it in a throwaway workspace, or pass "
            "--i-know-this-is-a-test-workspace if this one really is."
        )
        return EXIT_REFUSED
    return None


def build_report(
    probe: Probe, args: argparse.Namespace, base: str, started: str, exit_code: int | None
) -> dict[str, Any]:
    return {
        "tool": "preset_probe",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base": base,
        "options": {"skip_calls": args.skip_calls, "max_live_calls": args.max_live_calls},
        "counts": {
            "steps": len(probe.steps),
            "preset_writes": probe.writes,
            "presets_created_or_updated": sorted(probe.created),
            "live_calls": probe.live_calls,
        },
        "exit_code": exit_code,
        "steps": [s.to_dict() for s in probe.steps],
        "findings": probe.findings,
        "summary": build_summary(probe.findings),
    }


def write_report(path: str, report: Mapping[str, Any], key: str, write_key: str | None = None) -> None:
    text = scrub(scrub(json.dumps(report, indent=2, ensure_ascii=False), key), write_key)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        # An argument that is really the key, typed in the wrong place, must not be echoed back.
        self.print_usage(sys.stderr)
        sys.stderr.write(scrub(f"{self.prog}: error: {message}") + "\n")
        raise SystemExit(EXIT_REFUSED)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="preset_probe.py",
        description=(
            "Probe OpenRouter's preset API in a TEST workspace. The key is read from the environment variable "
            f"{KEY_ENV} only. Creates at most two presets ({SLUG_A}, {SLUG_B}) and makes at most --max-live-calls "
            "completion calls, all to free models."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="API base URL (default: %(default)s)")
    parser.add_argument("--out", default="preset_probe_report.json", help="where to write the JSON report")
    parser.add_argument(
        "--skip-calls",
        action="store_true",
        help="run only the preset create and read experiments: no completion call (public model reads still happen)",
    )
    parser.add_argument("--max-live-calls", type=int, default=8, help="most completion calls to make (default 8)")
    parser.add_argument(
        "--i-know-this-is-a-test-workspace",
        action="store_true",
        dest="i_know",
        help="run even though the workspace holds presets that are not the probe's",
    )
    return parser


def _print(text: str, stream: Any) -> None:
    """Print without ever failing: an error body with a character the console cannot show must not stop the run."""
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), file=stream, flush=True)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_REFUSED
    source = os.environ if env is None else env
    key = (source.get(KEY_ENV) or "").strip()
    write_key = (source.get(WRITE_KEY_ENV) or "").strip() or None

    def say(text: str) -> None:
        _print(scrub(scrub(text, key or None), write_key), sys.stdout)

    def err(text: str) -> None:
        _print(scrub(scrub(text, key or None), write_key), sys.stderr)

    if not key:
        err(
            f"{KEY_ENV} is not set. Put the test workspace's key in that environment variable, not on the command line."
        )
        return EXIT_REFUSED
    if args.max_live_calls < 0:
        err("--max-live-calls cannot be negative.")
        return EXIT_REFUSED
    try:
        base = check_base(args.base)
    except Refused as exc:
        err(str(exc))
        return EXIT_REFUSED

    started = datetime.now(UTC).isoformat(timespec="seconds")
    say(
        f"Probing {base} (at most {args.max_live_calls} live calls{', none: --skip-calls' if args.skip_calls else ''})."
    )
    with httpx.Client(follow_redirects=False) as client:
        probe = Probe(
            client,
            base,
            key,
            skip_calls=args.skip_calls,
            max_live_calls=args.max_live_calls,
            say=say,
            write_key=write_key,
        )
        code = preflight(probe, args.i_know, err)
        if code is not None:
            if code == EXIT_FAILED:
                write_report(args.out, build_report(probe, args, base, started, code), key, write_key)
            return code
        try:
            probe.run()
        finally:
            write_report(args.out, build_report(probe, args, base, started, EXIT_OK), key, write_key)
    say("")
    presets = ", ".join(sorted(probe.created)) or "no preset"
    say(
        f"Summary ({len(probe.steps)} steps, {probe.writes} preset writes to {presets}, {probe.live_calls} live calls):"
    )
    for item in build_summary(probe.findings):
        say(f"- {item['question']}")
        say(f"    {item['answer']}")
    say(f"Report: {args.out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
