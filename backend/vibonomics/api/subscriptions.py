"""Subscription usage endpoints: read the latest snapshot and accept pushes from Claude Code.

Claude Code's status line receives rate-limit data (5-hour, weekly, and optionally a gateway spend
limit) on stdin. Pointing its status-line command at the ingest endpoint keeps Vibonomics's view live
without storing any token. The response of the ingest endpoint is intentionally tiny: status-line
commands are run frequently.
"""

from __future__ import annotations

import hmac
import json
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
from ..subscriptions import KINDS, latest_snapshot, normalize_statusline, store_snapshot

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


def _check_token(expected: str | None, provided: str | None) -> None:
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Bad subscription ingest token")


@router.get("/claude", response_model=ClaudeSubscriptionOut)
def claude_subscription(request: Request, session: Session = Depends(get_session)) -> ClaudeSubscriptionOut:
    settings = load_settings(session)
    token = _token(session, settings)
    latest = latest_snapshot(session, "claude")
    snapshot = None
    if latest is not None:
        received_at, payload = latest
        snapshot = SnapshotOut(
            source=str(payload.get("source") or "claude-code"),
            received_at=received_at,
            plan=payload.get("plan"),
            windows=[WindowOut(**w) for w in payload.get("windows") or []],
            extra_usage=ExtraUsageOut(**payload["extra_usage"]) if payload.get("extra_usage") else None,
            spend_limit=SpendLimitOut(**payload["spend_limit"]) if payload.get("spend_limit") else None,
            session=SessionStatsOut(**payload["session"]) if payload.get("session") else None,
            model=payload.get("model"),
            version=payload.get("version"),
        )
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
        ingest_path=f"/api/subscription/ingest/claude?token={token}",
        ingest_token=token,
        statusline_command=command,
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
    token: str | None = Query(default=None),
    x_vibonomics_token: str | None = Header(default=None),
) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 512_000:
        raise HTTPException(status_code=413, detail="Snapshot too large")
    with session_scope() as session:
        expected = crypto.decrypt(load_settings(session).subscriptions.claude_ingest_token_enc)
    _check_token(expected, token or x_vibonomics_token)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Body must be the Claude Code status-line JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    normalized = normalize_statusline(payload)
    with session_scope() as session:
        store_snapshot(session, "claude", normalized)
    events.publish("subscription.updated", {"kind": "claude"})
    return {"ok": True, "windows": len(normalized["windows"]), "received_at": datetime.now(UTC).isoformat()}
