"""Subscription usage endpoints: read the latest snapshot and accept pushes from Claude Code.

Claude Code's status line receives rate-limit data (5-hour, weekly, and optionally a gateway spend
limit) on stdin. Pointing its status-line command at the ingest endpoint keeps Vibonomics's view live
without storing any token. The response of the ingest endpoint is intentionally tiny: status-line
commands are run frequently.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto, events
from ..config import get_config
from ..db import get_session, session_scope
from ..orm import SubscriptionSnapshot
from ..settings_store import AppSettings, load_settings, save_settings
from ..subscriptions import (
    KINDS,
    clean_model,
    clean_version,
    latest_snapshot,
    normalize_statusline,
    store_snapshot,
)
from .auth import is_authenticated

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/subscription", tags=["subscription"])

DOCS_URL = "https://code.claude.com/docs/en/statusline"


class WindowOut(BaseModel):
    key: str
    label: str
    used_percentage: float
    resets_at: str | None = None


class ExtraUsageOut(BaseModel):
    used: float | None = None
    limit: float | None = None
    utilization: float | None = None
    currency: str = "USD"
    monthly: bool = True


class SpendLimitOut(BaseModel):
    used_usd: float | None = None
    limit_usd: float | None = None
    used_percentage: float | None = None
    resets_at: str | None = None
    period: str = "monthly"


class SessionStatsOut(BaseModel):
    """What the status line says about the session that sent it (cost so far, context pressure)."""

    cost_usd: float | None = None
    duration_ms: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    context_used_percentage: float | None = None
    context_input_tokens: int | None = None
    context_output_tokens: int | None = None
    context_size: float | None = None
    cache_hit_ratio: float | None = None


class SnapshotOut(BaseModel):
    source: str
    received_at: datetime
    plan: str | None = None
    windows: list[WindowOut] = Field(default_factory=list)
    extra_usage: ExtraUsageOut | None = None
    spend_limit: SpendLimitOut | None = None
    session: SessionStatsOut | None = None
    model: str | None = None
    version: str | None = None


class ClaudeSubscriptionOut(BaseModel):
    kind: str
    title: str
    snapshot: SnapshotOut | None
    ingest_path: str
    ingest_token: str
    statusline_command: str
    docs_url: str
    note: str


def _token(session: Session, settings: AppSettings) -> str:
    token = crypto.decrypt(settings.subscriptions.claude_ingest_token_enc)
    if token:
        return token
    token = secrets.token_urlsafe(24)
    settings.subscriptions.claude_ingest_token_enc = crypto.encrypt(token)
    save_settings(session, settings)
    session.flush()
    return token


def _public_base(request: Request) -> str:
    """The URL the operator's Claude Code should post to (honours the proxy's forwarded headers)."""
    cfg = get_config()
    base = str(request.base_url).rstrip("/")
    return base + cfg.url_base


MAX_INGEST_BYTES = 512_000


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it passes ``limit`` instead of after it is all in memory."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Snapshot too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="Snapshot too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _check_token(expected: str | None, provided: str | None) -> None:
    # compare_digest raises TypeError on a str with non-ASCII characters, so compare the bytes.
    if not expected or not provided or not hmac.compare_digest(expected.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Bad subscription ingest token")


def _snapshot_out(received_at: datetime, payload: dict[str, Any]) -> SnapshotOut | None:
    """The stored snapshot as the API shows it, or None when the row cannot be read.

    A row stored by an older ingest can hold a model or version that is not text. One bad row must not take the
    whole endpoint down, because it also carries the ingest token the operator needs to fix things.
    """
    try:
        return SnapshotOut(
            source=str(payload.get("source") or "claude-code"),
            received_at=received_at,
            plan=payload.get("plan"),
            # Snapshots stored before spend-limit windows needed a percentage can hold one without it.
            windows=[WindowOut(**w) for w in payload.get("windows") or [] if w.get("used_percentage") is not None],
            extra_usage=ExtraUsageOut(**payload["extra_usage"]) if payload.get("extra_usage") else None,
            spend_limit=SpendLimitOut(**payload["spend_limit"]) if payload.get("spend_limit") else None,
            session=SessionStatsOut(**payload["session"]) if payload.get("session") else None,
            model=clean_model(payload.get("model")),
            version=clean_version(payload.get("version")),
        )
    except (ValueError, TypeError, AttributeError, KeyError):  # pydantic's ValidationError is a ValueError
        log.warning("Ignoring an unreadable stored Claude subscription snapshot")
        return None


@router.get("/claude", response_model=ClaudeSubscriptionOut)
def claude_subscription(request: Request, session: Session = Depends(get_session)) -> ClaudeSubscriptionOut:
    settings = load_settings(session)
    # The token lets anyone post usage figures, so a visitor with no session gets the usage and no token (and
    # none is minted for them).
    reveal = is_authenticated(request)
    token = _token(session, settings) if reveal else ""
    latest = latest_snapshot(session, "claude")
    snapshot = _snapshot_out(*latest) if latest is not None else None
    base = _public_base(request)
    ingest_url = f"{base}/api/subscription/ingest/claude"
    command = (
        f'sh -c \'curl -s -X POST "{ingest_url}" '
        '-H "Content-Type: application/json" '
        f'-H "X-Vibonomics-Token: {token}" --data-binary @- >/dev/null; '
        'echo "vibonomics: usage synced"\''
    )
    return ClaudeSubscriptionOut(
        kind="claude",
        title=KINDS["claude"],
        snapshot=snapshot,
        ingest_path="/api/subscription/ingest/claude",
        ingest_token=token,
        statusline_command=command if reveal else "",
        docs_url=DOCS_URL,
        note=(
            "Subscriptions have no dollar balance. The windows are the rolling 5-hour and weekly limits (cloud "
            "sessions count against the same windows). Usage-credit spend appears when an Anthropic login token "
            "with the profile scope is configured under Providers → Anthropic."
        ),
    )


@router.delete("/claude", status_code=204)
def clear_claude_snapshot(session: Session = Depends(get_session)) -> None:
    """Forget received snapshots (for example after testing the status-line command)."""
    for row in session.scalars(select(SubscriptionSnapshot).where(SubscriptionSnapshot.kind == "claude")):
        session.delete(row)
    events.publish("subscription.updated", {"kind": "claude"})


@router.post("/claude/rotate", response_model=ClaudeSubscriptionOut)
def rotate_claude_token(request: Request, session: Session = Depends(get_session)) -> ClaudeSubscriptionOut:
    """Replace the ingest token (for example after it was pasted somewhere public)."""
    settings = load_settings(session)
    settings.subscriptions.claude_ingest_token_enc = None
    save_settings(session, settings)
    session.flush()
    return claude_subscription(request, session)


@router.post("/ingest/claude", status_code=202)
async def ingest_claude(
    request: Request,
    # Still accepted for scripts written before the header; nothing the app shows or generates uses it.
    token: str | None = Query(
        default=None, deprecated=True, description="Send the token in the X-Vibonomics-Token header instead."
    ),
    x_vibonomics_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # This route is outside the login guard, so the token comes first: nobody without it gets the app to
    # read a body at all.
    with session_scope() as session:
        expected = crypto.decrypt(load_settings(session).subscriptions.claude_ingest_token_enc)
    _check_token(expected, token or x_vibonomics_token)
    raw = await _read_capped(request, MAX_INGEST_BYTES)
    try:
        # ValueError covers bad UTF-8, bad JSON and an integer over Python's digit limit; RecursionError, deep nesting.
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Body must be the Claude Code status-line JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    try:
        normalized = normalize_statusline(payload)
    except (ArithmeticError, ValueError, RecursionError) as exc:  # OverflowError is an ArithmeticError
        raise HTTPException(status_code=400, detail="Body must be the Claude Code status-line JSON") from exc
    with session_scope() as session:
        store_snapshot(session, "claude", normalized)
    events.publish("subscription.updated", {"kind": "claude"})
    return {"ok": True, "windows": len(normalized["windows"]), "received_at": datetime.now(UTC).isoformat()}
