"""Claude subscription usage.

A Claude Pro/Max subscription has no dollar balance and no public usage API. Usage is expressed as
windows (a rolling 5-hour session, a weekly window, and on Max a per-model weekly window) with a
fraction used and a reset time:

- Claude Code hands these numbers to status-line scripts, and that JSON can be pushed to Vibonomics
  (see ``POST /api/subscription/ingest/claude``). This is the live, supported route.
- Claude Code's own usage request to the claude.ai usage endpoint additionally reports optional
  usage-credit (overage) spend. That endpoint needs an interactive-login OAuth token with the
  ``user:profile`` scope; Vibonomics reads it only when the operator supplies such a token, and never
  refreshes it.

Cloud sessions (Claude Code on the web) draw on the same plan windows; there is no separate credit.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .orm import SubscriptionSnapshot, utcnow

KINDS = {"claude": "Claude subscription"}

_WINDOWS = (
    ("five_hour", "5-hour session"),
    ("seven_day", "Weekly"),
    ("seven_day_opus", "Weekly · Opus"),
    ("seven_day_sonnet", "Weekly · Sonnet"),
    ("seven_day_oauth_apps", "Weekly · apps"),
    ("seven_day_overage_included", "Weekly · overage-included"),
)


def _usage(value: Any) -> float | None:
    """Normalise a fraction to percent; the docs use 0-1 but headers can run above 1."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number * 100, 2) if number <= 1.5 else round(number, 2)


def _iso(value: Any) -> str | None:
    """Epoch seconds/milliseconds or an ISO string → ISO UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _iso(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    return None


def _window(key: str, label: str, node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    used = _usage(node.get("used_percentage", node.get("utilization")))
    if used is None:
        return None
    return {
        "key": key,
        "label": label,
        "used_percentage": used,
        "resets_at": _iso(node.get("resets_at", node.get("resetsAt"))),
    }


def _spend(node: object) -> dict[str, float | str | None] | None:
    if not isinstance(node, dict):
        return None
    used = _num(node.get("used_usd"))
    limit = _num(node.get("limit_usd"))
    percent = _num(node.get("used_percentage"))
    if percent is None and used is not None and limit:
        percent = round(used / limit * 100, 2)
    return {
        "used_usd": used,
        "limit_usd": limit,
        "used_percentage": percent,
        "resets_at": _iso(node.get("resets_at")),
        "period": str(node.get("period") or "monthly"),
    }


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number == number else None


def _money(value: object) -> float | None:
    """Per-session cost is often a fraction of a cent, so it keeps four decimals."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number, 4) if number == number else None


def _int(value: object) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _extra_usage(node: object) -> dict[str, object] | None:
    """Usage credits (extra usage), in major currency units."""
    if not isinstance(node, dict) or not node.get("is_enabled"):
        return None
    used = _num(node.get("used_credits"))
    limit = _num(node.get("monthly_limit"))
    return {
        "used": None if used is None else round(used / 100, 2),
        "limit": None if limit is None else round(limit / 100, 2),
        "utilization": _num(node.get("utilization")),
        "currency": str(node.get("currency") or "USD"),
        "monthly": True,
    }


def _session(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Session-level numbers from the status line: spend so far, elapsed time and context pressure.

    Claude Code drops the rate-limit windows when they reset, so these fields are what keep the card
    useful between resets. Every field is optional.
    """
    cost = payload.get("cost") if isinstance(payload.get("cost"), dict) else {}
    context = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    cache = payload.get("prompt_cache") if isinstance(payload.get("prompt_cache"), dict) else {}
    used = _usage(context.get("used_percentage"))
    tokens = _num(context.get("total_input_tokens"))
    output = _num(context.get("total_output_tokens"))
    session = {
        "cost_usd": _money(cost.get("total_cost_usd")),
        "duration_ms": _int(cost.get("total_duration_ms")),
        "lines_added": _int(cost.get("total_lines_added")),
        "lines_removed": _int(cost.get("total_lines_removed")),
        "context_used_percentage": used,
        "context_input_tokens": None if tokens is None else int(tokens),
        "context_output_tokens": None if output is None else int(output),
        "context_size": _num(context.get("context_window_size")),
        "cache_hit_ratio": _num(cache.get("hit_ratio")),
    }
    if all(value is None for value in session.values()):
        return None
    return session


def normalize_statusline(payload: dict[str, Any]) -> dict[str, Any]:
    """Claude Code status-line JSON → displayable windows."""
    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    windows = [w for key, label in _WINDOWS if (w := _window(key, label, limits.get(key))) is not None]
    spend = _spend(limits.get("spend_limit"))
    if spend is not None and spend["used_percentage"] is not None:
        windows.append(
            {
                "key": "spend_limit",
                "label": "Gateway spend limit",
                "used_percentage": spend["used_percentage"],
                "resets_at": spend["resets_at"],
            }
        )
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    return {
        "windows": windows,
        "spend_limit": spend,
        "extra_usage": None,
        "plan": None,
        "source": "claude-code",
        "model": model.get("display_name") or model.get("id"),
        "session_id": payload.get("session_id"),
        "version": payload.get("version"),
        "session": _session(payload),
        "received_at": utcnow().isoformat(),
    }


def _window_source(payload: dict[str, Any]) -> dict[str, Any]:
    """Where this version of the endpoint keeps its window objects.

    The endpoint is undocumented and has been seen in two shapes: the windows nested under
    ``rate_limits``, and the same keys at the top level. Either is accepted.
    """
    nested = payload.get("rate_limits")
    if isinstance(nested, dict) and any(isinstance(nested.get(key), dict) for key, _label in _WINDOWS):
        return nested
    if any(isinstance(payload.get(key), dict) for key, _label in _WINDOWS):
        return payload
    return nested if isinstance(nested, dict) else {}


def _percent_window(entry: dict[str, Any]) -> dict[str, Any] | None:
    """A ``limits`` array entry, whose ``percent`` is already 0-100 (not a 0-1 fraction)."""
    try:
        percent = float(entry.get("percent"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(percent) or percent < 0:
        return None
    return {"used_percentage": round(min(percent, 100.0), 2), "resets_at": _iso(entry.get("resets_at"))}


def _array_windows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Older clients read a ``limits`` list (``group``/``kind``/``percent``) instead of keyed objects."""
    entries = payload.get("limits")
    if not isinstance(entries, list):
        return []
    windows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        group = str(entry.get("group") or entry.get("kind") or "")
        if "session" in group:
            key, label = "five_hour", "5-hour session"
        elif "weekly" in group:
            key, label = "seven_day", "Weekly"
        else:
            continue
        measured = _percent_window(entry)
        if measured is not None:
            windows.append({"key": key, "label": label, **measured})
    return windows


def normalize_usage(payload: dict[str, Any]) -> dict[str, Any]:
    """Claude Code's usage endpoint → displayable windows and credit spend.

    Undocumented, so parsing tolerates every shape seen in the wild: windows under ``rate_limits``,
    the same keys at the top level, or a ``limits`` array of ``group``/``kind``/``percent`` entries.
    """
    limits = _window_source(payload)
    windows = [w for key, label in _WINDOWS if (w := _window(key, label, limits.get(key))) is not None]
    for entry in limits.get("model_scoped") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("display_name") or entry.get("model") or "model")
        window = _window(f"model:{name}", f"Weekly · {name}", entry)
        if window is not None:
            windows.append(window)
    seen = {window["key"] for window in windows}
    windows.extend(window for window in _array_windows(payload) if window["key"] not in seen)
    return {
        "windows": windows,
        "spend_limit": None,
        "extra_usage": _extra_usage(limits.get("extra_usage") or payload.get("extra_usage")),
        "plan": payload.get("subscription_type"),
        "source": "claude-usage-api",
        "model": None,
        "session_id": None,
        "version": None,
        "session": None,
        "received_at": utcnow().isoformat(),
    }


def store_snapshot(session: Session, kind: str, payload: dict[str, Any]) -> SubscriptionSnapshot:
    snapshot = SubscriptionSnapshot(kind=kind, received_at=utcnow(), payload=payload)
    session.add(snapshot)
    session.flush()
    # Keep a little history for debugging, not an unbounded log.
    old = list(
        session.scalars(
            select(SubscriptionSnapshot)
            .where(SubscriptionSnapshot.kind == kind)
            .order_by(SubscriptionSnapshot.id.desc())
            .offset(20)
        )
    )
    for row in old:
        session.delete(row)
    return snapshot


def latest_snapshot(session: Session, kind: str) -> tuple[datetime, dict[str, Any]] | None:
    row = session.scalars(
        select(SubscriptionSnapshot)
        .where(SubscriptionSnapshot.kind == kind)
        .order_by(SubscriptionSnapshot.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return row.received_at, row.payload
