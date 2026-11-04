"""Manual refresh triggers and refresh history."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..jobs import KINDS, is_running, run_job, spawn
from ..orm import RefreshRun

router = APIRouter(prefix="/api/refresh", tags=["refresh"])

Kind = Literal["models", "balances", "benchmarks", "usage"]


class RefreshIn(BaseModel):
    kinds: list[Kind] = Field(default_factory=lambda: list(KINDS))


class RefreshRunOut(BaseModel):
    id: int
    kind: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    message: str | None
    stats: dict[str, Any]


class RefreshStatus(BaseModel):
    running: list[str]
    last: dict[str, RefreshRunOut]


async def _run_sequence(kinds: list[str]) -> None:
    # Models first so balances/benchmarks see fresh catalogue data.
    for kind in [k for k in KINDS if k in kinds]:
        await run_job(kind, "manual")


@router.post("", status_code=202, response_model=RefreshStatus)
async def trigger_refresh(body: RefreshIn) -> RefreshStatus:
    spawn(_run_sequence(list(body.kinds)))
    return RefreshStatus(running=list(body.kinds), last={})


@router.get("/status", response_model=RefreshStatus)
def refresh_status(session: Session = Depends(get_session)) -> RefreshStatus:
    last: dict[str, RefreshRunOut] = {}
    for kind in KINDS:
        run = session.scalars(
            select(RefreshRun).where(RefreshRun.kind == kind).order_by(RefreshRun.id.desc()).limit(1)
        ).first()
        if run:
            last[kind] = RefreshRunOut.model_validate(run, from_attributes=True)
    return RefreshStatus(running=[k for k in KINDS if is_running(k)], last=last)


@router.get("/runs", response_model=list[RefreshRunOut])
def refresh_runs(limit: int = 50, session: Session = Depends(get_session)) -> list[RefreshRunOut]:
    runs = session.scalars(select(RefreshRun).order_by(RefreshRun.id.desc()).limit(min(limit, 500))).all()
    return [RefreshRunOut.model_validate(r, from_attributes=True) for r in runs]
