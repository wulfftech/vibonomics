"""Vibonomics's own model tests: suites, judge candidates, runs and results.

Contract (the frontend depends on it):
- ``GET  /api/evals/suites``           suites with their tasks
- ``GET  /api/evals/judges``           models usable as the administering/judging model
- ``POST /api/evals/runs``             start a run → ``EvalRunOut`` (status "queued")
- ``GET  /api/evals/runs``             recent runs
- ``GET  /api/evals/runs/{id}``        run with per-task results and transcripts
- ``POST /api/evals/runs/{id}/cancel`` cancel a queued/running run
- ``DELETE /api/evals/runs/{id}``      delete a finished run
Events: ``evals.progress`` {run_id, done, total, current}, ``evals.finished`` {run_id, status}.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events
from ..db import get_session
from ..evals import runner
from ..evals.suites import SUITES
from ..orm import EvalResult, EvalRun, Model, ProviderConfig
from ..provider_service import resolve
from ..providers.registry import get_adapter_class
from ..scoring import compute_scores, own_test_scores, score_key
from ..settings_store import load_settings
from .settings import effective_sandbox_mode

router = APIRouter(prefix="/api/evals", tags=["evals"])


class TaskOut(BaseModel):
    id: str
    name: str
    category: str
    description: str
    uses_sandbox: bool


class SuiteOut(BaseModel):
    id: str
    name: str
    description: str
    tasks: list[TaskOut]


class ModelRef(BaseModel):
    provider_id: int
    model_id: str


class JudgeOut(BaseModel):
    provider_id: int
    provider_name: str
    provider_type: str
    model_id: str
    name: str
    composite: float | None
    is_free: bool
    note: str | None = None


class EvalRunIn(BaseModel):
    suite: str
    targets: list[ModelRef] = Field(min_length=1, max_length=25)
    judge: ModelRef
    task_ids: list[str] | None = None


class TargetOut(BaseModel):
    provider_id: int
    model_id: str
    name: str
    provider_name: str


class EvalRunOut(BaseModel):
    id: int
    created_at: datetime
    finished_at: datetime | None
    status: str
    suite: str
    judge_provider_id: int | None
    judge_model_id: str | None
    targets: list[TargetOut]
    progress: dict[str, Any]
    summary: dict[str, Any]
    error: str | None


class EvalResultOut(BaseModel):
    id: int
    provider_id: int
    model_id: str
    task_id: str
    category: str
    status: str
    score: float
    check_score: float | None
    judge_score: float | None
    judge_notes: str | None
    steps: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int
    error: str | None
    transcript: list[Any] = Field(default_factory=list)


class EvalRunDetail(BaseModel):
    run: EvalRunOut
    results: list[EvalResultOut]


def _run_out(run: EvalRun) -> EvalRunOut:
    return EvalRunOut(
        id=run.id,
        created_at=run.created_at,
        finished_at=run.finished_at,
        status=run.status,
        suite=run.suite,
        judge_provider_id=run.judge_provider_config_id,
        judge_model_id=run.judge_model_id,
        targets=[TargetOut(**t) for t in run.targets or []],
        progress=run.progress or {},
        summary=run.summary or {},
        error=run.error,
    )


def _result_out(r: EvalResult, with_transcript: bool) -> EvalResultOut:
    return EvalResultOut(
        id=r.id,
        provider_id=r.provider_config_id,
        model_id=r.model_id,
        task_id=r.task_id,
        category=r.category,
        status=r.status,
        score=r.score,
        check_score=r.check_score,
        judge_score=r.judge_score,
        judge_notes=r.judge_notes,
        steps=r.steps,
        input_tokens=r.input_tokens,
        output_tokens=r.output_tokens,
        cost_usd=r.cost_usd,
        latency_ms=r.latency_ms,
        error=r.error,
        transcript=r.transcript if with_transcript else [],
    )


@router.get("/suites", response_model=list[SuiteOut])
def suites() -> list[SuiteOut]:
    return [
        SuiteOut(
            id=s.id,
            name=s.name,
            description=s.description,
            tasks=[
                TaskOut(
                    id=t.id, name=t.name, category=t.category, description=t.description, uses_sandbox=t.uses_sandbox
                )
                for t in s.tasks
            ],
        )
        for s in SUITES.values()
    ]


def _usable(provider: ProviderConfig) -> bool:
    cls = get_adapter_class(provider.type)
    return bool(cls and (resolve(provider).api_key or cls.spec.custom))


def _judge_supported(provider: ProviderConfig, model: Model) -> bool:
    """Models Vibonomics can actually call as a judge (no client-only or unsupported routes)."""
    if provider.type == "opencode" and model.is_free:
        return False
    endpoint = (model.extra or {}).get("endpoint")
    if provider.type in {"opencode", "opencode-go"} and isinstance(endpoint, dict):
        npm = str(endpoint.get("npm") or "")
        if "google" in npm:
            return False
    return True


@router.get("/judges", response_model=list[JudgeOut])
def judges(session: Session = Depends(get_session)) -> list[JudgeOut]:
    settings = load_settings(session)
    providers = {
        p.id: p for p in session.scalars(select(ProviderConfig).where(ProviderConfig.enabled.is_(True))) if _usable(p)
    }
    if not providers:
        return []
    scores = compute_scores(session, settings, own_test_scores(session))
    out = []
    for m in session.scalars(
        select(Model).where(Model.provider_config_id.in_(providers.keys()), Model.active.is_(True))
    ):
        if m.tool_call is False:
            continue
        provider = providers[m.provider_config_id]
        if not _judge_supported(provider, m):
            continue
        sc = scores.get(score_key(m.canonical_id, m.provider_config_id, m.model_id))
        note = "Free models are often rate-limited; a paid judge is more reliable." if m.is_free else None
        out.append(
            JudgeOut(
                provider_id=m.provider_config_id,
                provider_name=provider.name,
                provider_type=provider.type,
                model_id=m.model_id,
                name=m.name,
                composite=sc.composite if sc else None,
                is_free=m.is_free,
                note=note,
            )
        )
    # Warned models go last so the default judge is one that can actually be called.
    out.sort(key=lambda j: (j.note is not None, j.composite is None, -(j.composite or 0), j.name.lower()))
    return out


@router.post("/runs", response_model=EvalRunOut, status_code=201)
async def start_run(body: EvalRunIn, session: Session = Depends(get_session)) -> EvalRunOut:
    suite = SUITES.get(body.suite)
    if suite is None:
        raise HTTPException(400, f"Unknown suite '{body.suite}'")
    settings = load_settings(session)
    providers = {p.id: p for p in session.scalars(select(ProviderConfig))}
    targets = []
    for ref in [*body.targets, body.judge]:
        p = providers.get(ref.provider_id)
        if p is None or not _usable(p):
            raise HTTPException(400, f"Provider {ref.provider_id} is missing or has no API key")
    for ref in body.targets:
        m = session.scalars(
            select(Model).where(Model.provider_config_id == ref.provider_id, Model.model_id == ref.model_id)
        ).first()
        targets.append(
            {
                "provider_id": ref.provider_id,
                "model_id": ref.model_id,
                "name": m.name if m else ref.model_id,
                "provider_name": providers[ref.provider_id].name,
            }
        )
    task_ids = body.task_ids or [t.id for t in suite.tasks]
    if effective_sandbox_mode(settings) == "disabled":
        note = "Sandbox disabled: tasks that execute code are graded without running tests."
    else:
        note = None
    run = EvalRun(
        suite=suite.id,
        judge_provider_config_id=body.judge.provider_id,
        judge_model_id=body.judge.model_id,
        targets=targets,
        progress={"total": len(task_ids) * len(targets), "done": 0, "task_ids": task_ids, "note": note},
        status="queued",
    )
    session.add(run)
    session.flush()
    session.commit()
    runner.schedule(run.id)
    events.publish("evals.progress", {"run_id": run.id, "done": 0, "total": run.progress["total"]})
    return _run_out(run)


@router.get("/runs", response_model=list[EvalRunOut])
def list_runs(limit: int = 50, session: Session = Depends(get_session)) -> list[EvalRunOut]:
    runs = session.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(min(limit, 200))).all()
    return [_run_out(r) for r in runs]


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
def run_detail(run_id: int, session: Session = Depends(get_session)) -> EvalRunDetail:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    results = session.scalars(select(EvalResult).where(EvalResult.run_id == run_id).order_by(EvalResult.id)).all()
    return EvalRunDetail(run=_run_out(run), results=[_result_out(r, True) for r in results])


@router.post("/runs/{run_id}/cancel", response_model=EvalRunOut)
async def cancel_run(run_id: int, session: Session = Depends(get_session)) -> EvalRunOut:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status in ("queued", "running"):
        runner.cancel(run_id)
        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
        session.commit()
        events.publish("evals.finished", {"run_id": run_id, "status": "cancelled"})
    return _run_out(run)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int, session: Session = Depends(get_session)) -> None:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status in ("queued", "running"):
        raise HTTPException(409, "Cancel the run first")
    session.delete(run)
    session.commit()
    events.publish("evals.finished", {"run_id": run_id, "status": "deleted"})
