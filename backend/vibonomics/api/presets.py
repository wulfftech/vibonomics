"""The OpenRouter preset Vibonomics keeps pointed at the best-value model: preview it, then apply it.

``POST /api/presets/plan`` is the dry run: it reads from OpenRouter, ranks the models, and returns the exact request
an update would send, writing nothing. ``POST /api/presets/apply`` sends it, and only the plan whose digest the caller
names. Both are POSTs because they call OpenRouter with the operator's key, so a visitor to an instance that is open to
read cannot trigger them. The settings that steer the ranking live in ``settings.presets``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import presets
from ..http import FetchError
from ..presets import Applied, Candidate, Plan

router = APIRouter(prefix="/api/presets", tags=["presets"])


class PresetTaskOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_hit_rate: float


class PresetCandidateOut(BaseModel):
    model_id: str
    name: str
    score: float
    task_cost: float = Field(description="Estimated USD per task at the current price")
    value: float = Field(description="Score points per dollar per task")
    discount: float | None


class PresetProviderOut(BaseModel):
    tag: str = Field(description="The provider slug the preset lists, such as `deepinfra/fp8`")
    provider: str | None
    input: float | None
    output: float | None
    discount: float
    quantization: str | None
    task_cost: float
    models: list[str] = Field(description="The listed models this provider serves, by model id")


class PresetChangeOut(BaseModel):
    path: str
    before: str | None = Field(description="The old value as JSON, null when the key is new")
    after: str | None = Field(description="The new value as JSON, null when the key goes")


class PresetVersionOut(BaseModel):
    version: int | None
    created_at: str | None
    model: str | None
    providers: list[str]
    live: bool = Field(description="Whether this is the version requests to the preset use now")


class PresetRequestOut(BaseModel):
    method: Literal["POST"] = "POST"
    url: str
    body: dict[str, Any] = Field(description="Sent exactly as shown; the API requires `messages` and then ignores it")


class PresetStateOut(BaseModel):
    owned: list[str] = Field(description="Slugs Vibonomics created, the only ones it will ever write")
    slug: str | None = Field(None, description="The preset last written")
    model_id: str | None = None
    version: int | None = None
    applied_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = Field(None, description="applied, unchanged, blocked or failed")
    last_message: str | None = None
    last_trigger: str | None = None


class PresetPlanOut(BaseModel):
    action: Literal["create", "update", "unchanged", "blocked"]
    reason: str
    slug: str
    enabled: bool
    auto_apply: bool
    generated_at: datetime
    profile: str | None
    task: PresetTaskOut | None
    considered: int = Field(description="Models that pass the filters")
    pick: PresetCandidateOut | None = Field(description="The model the preset leads with")
    picks: list[PresetCandidateOut] = Field(description="Every model the preset lists, in order, the lead first")
    kept_current: bool = Field(
        description="The lineup differs from raw best value because a model already in it is favoured"
    )
    providers: list[PresetProviderOut]
    runners_up: list[PresetCandidateOut]
    current_version: int | None = Field(description="The version of the preset that is live on OpenRouter now")
    current_config: dict[str, Any] | None
    versions: list[PresetVersionOut]
    config: dict[str, Any] | None
    changes: list[PresetChangeOut]
    request: PresetRequestOut | None
    digest: str | None = Field(description="Names this plan; `apply` refuses a plan that has changed since")
    warnings: list[str]
    state: PresetStateOut


class PresetSummaryOut(BaseModel):
    slug: str
    name: str
    version: int | None
    updated_at: str | None
    models: list[str]
    providers: list[str]


class PresetListOut(BaseModel):
    presets: list[PresetSummaryOut]
    error: str | None = Field(description="Why the list could not be read; the list is then empty")


class PresetApplyIn(BaseModel):
    digest: str = Field(min_length=1, description="The digest of the plan that was reviewed")


class PresetApplyOut(BaseModel):
    applied: bool
    message: str
    version: int | None
    unsaved: list[str] = Field(description="Keys OpenRouter did not keep as sent")
    state: PresetStateOut


def _candidate(c: Candidate) -> PresetCandidateOut:
    return PresetCandidateOut(
        model_id=c.model_id,
        name=c.name,
        score=round(c.score, 1),
        task_cost=round(c.task_cost, 6),
        value=round(c.value, 1),
        discount=c.discount,
    )


def _state_out(state: dict[str, Any]) -> PresetStateOut:
    applied = state.get("applied") or {}
    last = state.get("last") or {}

    def when(raw: object) -> datetime | None:
        return datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None

    return PresetStateOut(
        owned=list(state.get("owned") or []),
        slug=applied.get("slug"),
        model_id=applied.get("model_id"),
        version=applied.get("version"),
        applied_at=when(applied.get("at")),
        last_run_at=when(last.get("at")),
        last_status=last.get("status"),
        last_message=last.get("message"),
        last_trigger=last.get("trigger"),
    )


def _plan_out(plan: Plan) -> PresetPlanOut:
    cfg = plan.settings
    current = plan.current
    return PresetPlanOut(
        action=plan.action,  # type: ignore[arg-type]
        reason=plan.reason,
        slug=plan.slug,
        enabled=cfg.enabled,
        auto_apply=cfg.auto_apply,
        generated_at=plan.generated_at,
        profile=plan.profile,
        task=PresetTaskOut(
            input_tokens=plan.task.input_tokens,
            output_tokens=plan.task.output_tokens,
            cache_hit_rate=plan.task.cache_hit_rate,
        )
        if plan.task
        else None,
        considered=plan.considered,
        pick=_candidate(plan.pick) if plan.pick else None,
        picks=[_candidate(c) for c in plan.picks],
        kept_current=plan.kept_current,
        providers=[PresetProviderOut(**p) for p in plan.providers],
        runners_up=[_candidate(c) for c in plan.runners_up],
        current_version=current.version if current else None,
        current_config=current.config if current else None,
        versions=[PresetVersionOut(**v) for v in (current.versions if current else [])],
        config=plan.config,
        changes=[PresetChangeOut(path=c.path, before=c.before, after=c.after) for c in plan.changes],
        request=PresetRequestOut(url=plan.url, body=plan.body) if plan.url and plan.body else None,
        digest=plan.digest,
        warnings=plan.warnings,
        state=_state_out(plan.state),
    )


@router.get("/state", response_model=PresetStateOut)
async def preset_state() -> PresetStateOut:
    """What Vibonomics last wrote and when. Reads the local record only; nothing leaves this instance."""
    return _state_out(await asyncio.to_thread(presets.read_state))


@router.post("/list", response_model=PresetListOut)
async def preset_list() -> PresetListOut:
    """The account's own presets, to start a new one from. POST because it calls OpenRouter with the operator's key."""
    base, key, state = await asyncio.to_thread(presets.load_access)
    if not key:
        return PresetListOut(presets=[], error="Add an OpenRouter API key under Settings, Providers first.")
    try:
        rows = await presets.list_presets(base, key, list(state.get("owned") or []))
    except FetchError as exc:
        return PresetListOut(presets=[], error=f"Could not read your presets from OpenRouter: {exc}")
    return PresetListOut(presets=[PresetSummaryOut(**row) for row in rows], error=None)


@router.post("/plan", response_model=PresetPlanOut)
async def preset_plan() -> PresetPlanOut:
    """The dry run: rank the models, read the preset, and show the exact request an update would send."""
    return _plan_out(await presets.make_plan())


@router.post("/apply", response_model=PresetApplyOut)
async def preset_apply(body: PresetApplyIn) -> PresetApplyOut:
    """Write the plan the caller reviewed, if it is still the plan and there is something to write."""
    try:
        plan, applied = await presets.apply(body.digest, "manual")
    except presets.PresetDisabled as exc:
        raise HTTPException(409, str(exc)) from exc
    except presets.PlanChanged as exc:
        raise HTTPException(409, str(exc)) from exc
    except presets.PresetWriteError as exc:
        raise HTTPException(502, str(exc)) from exc
    state = _state_out(await asyncio.to_thread(presets.read_state))
    result: Applied | None = applied
    return PresetApplyOut(
        applied=result is not None,
        message=result.message if result else plan.reason,
        version=result.version if result else None,
        unsaved=result.unsaved if result else [],
        state=state,
    )
