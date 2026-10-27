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
    return {
        "used_usd": used,
        "limit_usd": limit,
        "used_percentage": _num(node.get("used_percentage")),
        "resets_at": _iso(node.get("resets_at")),
        "period": str(node.get("period") or "monthly"),
    }


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number == number else None


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


def normalize_statusline(payload: dict[str, Any]) -> dict[str, Any]:
    """Claude Code status-line JSON → displayable windows."""
    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    windows = [w for key, label in _WINDOWS if (w := _window(key, label, limits.get(key))) is not None]
    spend = _spend(limits.get("spend_limit"))
    if spend is not None:
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
        "received_at": utcnow().isoformat(),
    }


def normalize_usage(payload: dict[str, Any]) -> dict[str, Any]:
    """The claude.ai usage endpoint's response → displayable windows and credit spend."""
    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    windows = [w for key, label in _WINDOWS if (w := _window(key, label, limits.get(key))) is not None]
    for entry in limits.get("model_scoped") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("display_name") or entry.get("model") or "model")
        window = _window(f"model:{name}", f"Weekly · {name}", entry)
        if window is not None:
            windows.append(window)
    return {
        "windows": windows,
        "spend_limit": None,
        "extra_usage": _extra_usage(limits.get("extra_usage")),
        "plan": payload.get("subscription_type"),
        "source": "claude-usage-api",
        "model": None,
        "session_id": None,
        "version": None,
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
