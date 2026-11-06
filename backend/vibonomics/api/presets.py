"""The OpenRouter presets Vibonomics keeps pointed at the best-value model: preview one, then apply it.

``POST /api/presets/plan`` is the dry run for one preset: it reads from OpenRouter, ranks the models, and returns the
exact request an update would send, writing nothing. ``POST /api/presets/apply`` sends it, and only the plan whose
digest the caller names. All of them are POSTs because they call OpenRouter with the operator's key, so a visitor to
an instance that is open to read cannot trigger them. The settings that steer the ranking live in
``settings.presets.items``, one item per preset.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import events, presets, settings_store
from ..http import FetchError
from ..presets import Applied, Candidate, Plan
from ..settings_store import GuardrailKey, PresetSettings

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


class PresetSlugStateOut(BaseModel):
    model_id: str | None = None
    version: int | None = None
    applied_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = Field(None, description="applied, unchanged, blocked or failed")
    last_message: str | None = None
    last_trigger: str | None = None


class PresetStateOut(BaseModel):
    owned: list[str] = Field(description="Slugs Vibonomics created or adopted, the only ones it will ever write")
    slugs: dict[str, PresetSlugStateOut] = Field(description="What was last written and run, for each of them")


class PresetExcludedOut(BaseModel):
    models: int = Field(description="Models that passed the filters and were left out by a blocklist")
    providers: list[str] = Field(description="Providers whose endpoints a blocklist kept from being chosen")


class PresetGuardrailsOut(BaseModel):
    set: dict[str, Any] = Field(
        description="The guardrails this plan writes, by key, with their values: `temperature`, `max_tokens`, "
        "`repetition_penalty`, `frequency_penalty`, `presence_penalty`, `top_p`, `quantizations`, `ignore`, "
        "`require_parameters` and `system_prompt`. A key that is not here is not managed, and the live value stays."
    )
    cleared: list[GuardrailKey] = Field(description="The guardrails this plan takes off the preset")


class PresetSystemPromptOut(BaseModel):
    live: str | None = Field(
        description="The system prompt on the live preset, in full up to 20,000 characters; null when it has none"
    )
    planned: str | None = Field(
        description="The system prompt the write carries, in full up to 20,000 characters; null sends none"
    )
    source: Literal["managed", "cleared", "kept", "none"] = Field(
        description="`managed`: the text set under guardrails. `cleared`: the owner removes it. `kept`: the live "
        "prompt, sent back as it is. `none`: there is none and none is set."
    )
    changed: bool = Field(description="Whether the write changes the system prompt")


class PresetCapabilityOut(BaseModel):
    model_id: str
    provider: str | None = Field(
        description="The provider tag the model is pinned to; null for a model pinned to none, which OpenRouter routes"
    )
    supports: dict[str, Literal["yes", "no", "unknown"]] = Field(
        description="For each numeric parameter the owner sets, whether that endpoint takes it. `unknown` when the "
        "listing did not say, and for an unpinned model when its endpoints disagree"
    )


class PresetCheckOut(BaseModel):
    level: Literal["ok", "warning", "error"] = Field(description="An error refuses the write; a warning does not")
    check: str = Field(
        description="What was checked: listing, provider, pins, health, price, expiry, params, max_tokens, served "
        "(whether the preset's own quantization and ignore filters leave the model any endpoint) or free"
    )
    model: str | None = Field(description="The model the finding is about, by id; null when it is about none")
    provider: str | None = Field(description="The provider tag the finding is about; null when it is about none")
    message: str


class PresetPlanOut(BaseModel):
    action: Literal["create", "update", "unchanged", "blocked"]
    reason: str
    slug: str
    enabled: bool
    auto_apply: bool
    route: Literal["fallback", "sort"] = Field(description="The routing mode the preset is set to")
    sort: Literal["price", "throughput", "latency"] | None = Field(
        description="What `provider.sort` is on the sort route; price when null"
    )
    draft: bool = Field(description="Made from unsaved settings: a preview only, not something to apply")
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
    preset_id: str | None = Field(None, description="The id OpenRouter gave the preset; null when it names none")
    workspace: str | None = Field(
        None, description="The workspace OpenRouter names for the preset (slug or id); null when it names none"
    )
    current_version: int | None = Field(description="The version of the preset that is live on OpenRouter now")
    current_config: dict[str, Any] | None
    versions: list[PresetVersionOut]
    config: dict[str, Any] | None
    changes: list[PresetChangeOut]
    request: PresetRequestOut | None
    digest: str | None = Field(description="Names this plan; `apply` refuses a plan that has changed since")
    warnings: list[str]
    excluded: PresetExcludedOut = Field(description="What the blocklists removed from this run")
    own_tests_preferred: bool = Field(
        description="Whether the ranking took each model's own test score over its computed one: the preset's own "
        "choice, or the global Scoring setting when it has none"
    )
    passthrough: dict[str, Any] = Field(
        description="The keys Vibonomics does not manage and sends back as it found them, as raw JSON"
    )
    managed_keys: list[str] = Field(
        description="The keys Vibonomics decides: `provider.*` ones are inside `provider`. The guardrails the owner "
        "sets or removes for this preset follow the always-managed ones."
    )
    guardrails: PresetGuardrailsOut = Field(description="What this plan writes for the guardrail keys")
    system_prompt: PresetSystemPromptOut = Field(
        description="The system prompt on the live preset and the one this plan sends. A write with no system "
        "message would null the prompt, so the live one is always sent back unless the owner manages it"
    )
    capabilities: list[PresetCapabilityOut] = Field(
        description="Whether each pinned endpoint takes the numeric guardrails the owner sets; empty when none is set"
    )
    system_backup_at: datetime | None = Field(
        description="When the system prompt this preset had was last saved before a write replaced or removed it; "
        "null when none was"
    )
    validation: list[PresetCheckOut] = Field(
        description="What the live OpenRouter listing says about what this plan would write; empty when nothing was "
        "checked (a blocked plan)"
    )
    validated_at: datetime | None = Field(description="When the listing was read; null when nothing was checked")
    valid: bool = Field(
        description="False when the live check found an error, which refuses an apply; true when it found none, or "
        "was not made (see `validated_at`)"
    )
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


class PresetPlanIn(BaseModel):
    slug: str | None = Field(None, description="A saved preset to plan")
    settings: PresetSettings | None = Field(
        None,
        description="Settings for one preset to preview in place of the saved ones, to try them out; its own slug "
        "names the preset. Nothing is saved, and a plan made from them cannot be applied.",
    )


class PresetApplyIn(BaseModel):
    slug: str = Field(min_length=1, description="The saved preset to write")
    digest: str = Field(min_length=1, description="The digest of the plan that was reviewed")


class PresetInspectIn(BaseModel):
    slug: str = Field(min_length=1, description="Any preset on the OpenRouter account, managed by Vibonomics or not")


class PresetInspectOut(BaseModel):
    slug: str
    status: str = Field(description="`active` or `archived`")
    version: int | None = Field(description="The live version; null when OpenRouter returned none")
    updated_at: str | None
    preset_id: str | None = Field(description="The id OpenRouter gave the preset; null when it names none")
    workspace: str | None = Field(description="The workspace OpenRouter names for the preset; null when it names none")
    config: dict[str, Any] = Field(description="The live config, as OpenRouter holds it")
    has_system_prompt: bool
    system_prompt: str | None = Field(
        description="The live system prompt, in full up to 20,000 characters; null when the preset has none"
    )
    models: list[str] = Field(description="The models the config names, the lead first")
    providers: list[str] = Field(description="The providers the config restricts it to (`provider.only`)")
    versions: list[PresetVersionOut]
    managed_keys: list[str] = Field(description="The keys Vibonomics would decide if it managed the preset")
    passthrough: dict[str, Any] = Field(
        description="Every other key, which Vibonomics would send back as it found it, as raw JSON"
    )
    managed: bool = Field(description="Vibonomics already manages a preset with this name")
    adoptable: bool
    blockers: list[str] = Field(description="Why it cannot be adopted; empty when it can")
    digest: str = Field(description="Names the slug, the live version and the config; `adopt` refuses a changed one")


class PresetAdoptIn(BaseModel):
    slug: str = Field(min_length=1)
    digest: str = Field(min_length=1, description="The digest `inspect` returned for the preset the operator looked at")


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


def _capped(text: str | None) -> str | None:
    """A system prompt for the screen: whole up to what the owner may set, cut there when a live one is longer."""
    return text[: settings_store.GUARDRAIL_PROMPT_MAX] if text else None


def _when(raw: object) -> datetime | None:
    return datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None


def _state_out(state: dict[str, Any]) -> PresetStateOut:
    owned = list(state.get("owned") or [])
    all_applied, all_last = state.get("applied") or {}, state.get("last") or {}
    slugs: dict[str, PresetSlugStateOut] = {}
    for slug in dict.fromkeys([*owned, *all_applied, *all_last]):
        applied, last = all_applied.get(slug) or {}, all_last.get(slug) or {}
        slugs[slug] = PresetSlugStateOut(
            model_id=applied.get("model_id"),
            version=applied.get("version"),
            applied_at=_when(applied.get("at")),
            last_run_at=_when(last.get("at")),
            last_status=last.get("status"),
            last_message=last.get("message"),
            last_trigger=last.get("trigger"),
        )
    return PresetStateOut(owned=owned, slugs=slugs)


def _plan_out(plan: Plan) -> PresetPlanOut:
    cfg = plan.settings
    current = plan.current
    return PresetPlanOut(
        action=plan.action,  # type: ignore[arg-type]
        reason=plan.reason,
        slug=plan.slug,
        enabled=cfg.enabled,
        auto_apply=cfg.auto_apply,
        route=cfg.route,
        sort=cfg.sort,
        draft=plan.draft,
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
        preset_id=current.preset_id if current else None,
        workspace=current.workspace if current else None,
        current_version=current.version if current else None,
        current_config=current.config if current else None,
        versions=[PresetVersionOut(**v) for v in (current.versions if current else [])],
        config=plan.config,
        changes=[PresetChangeOut(path=c.path, before=c.before, after=c.after) for c in plan.changes],
        request=PresetRequestOut(url=plan.url, body=plan.body) if plan.url and plan.body else None,
        digest=plan.digest,
        warnings=plan.warnings,
        excluded=PresetExcludedOut(models=plan.excluded.models, providers=plan.excluded.providers),
        own_tests_preferred=plan.own_tests_preferred,
        passthrough=plan.passthrough,
        managed_keys=presets.managed_keys(cfg.guardrails),
        guardrails=PresetGuardrailsOut(set=cfg.guardrails.set_values(), cleared=list(cfg.guardrails.clear)),
        system_prompt=PresetSystemPromptOut(
            live=_capped(plan.live_prompt),
            planned=_capped(plan.prompt),
            source=plan.prompt_source,  # type: ignore[arg-type]
            changed=presets.prompts_differ(plan.live_prompt, plan.prompt),
        ),
        capabilities=[PresetCapabilityOut(**row) for row in presets.capability_rows(plan)],
        system_backup_at=_when(((plan.state.get("system_backups") or {}).get(plan.slug) or {}).get("at")),
        validation=[
            PresetCheckOut(level=c.level, check=c.check, model=c.model, provider=c.provider, message=c.message)
            for c in (plan.validation.items if plan.validation else [])
        ],
        validated_at=plan.validation.checked_at if plan.validation else None,
        valid=plan.validation.ok if plan.validation else True,
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
async def preset_plan(body: PresetPlanIn | None = None) -> PresetPlanOut:
    """The dry run: rank the models, read the preset, and show the exact request an update would send.

    ``slug`` plans a saved preset. ``settings`` previews those settings, unsaved, so a change can be tried before it is
    kept, or a preset that is not saved yet.
    """
    if body is None or (body.slug is None and body.settings is None):
        raise HTTPException(422, "Name the preset to plan with `slug`, or send its `settings` to preview.")
    draft = body.settings
    slug = draft.slug if draft is not None else str(body.slug)
    try:
        plan = await presets.make_plan(slug, draft=draft)
    except presets.PresetNotConfigured as exc:
        raise HTTPException(404, str(exc)) from exc
    return _plan_out(plan)


def _unavailable(exc: Exception) -> HTTPException:
    """The answer to a call that reads the account when it cannot: no key, a refusal, or no such preset."""
    if isinstance(exc, presets.NoApiKey):
        return HTTPException(409, str(exc))
    if isinstance(exc, presets.PresetNotFound):
        return HTTPException(404, str(exc))
    return HTTPException(502, f"Could not read the preset from OpenRouter: {exc}")


@router.post("/inspect", response_model=PresetInspectOut)
async def preset_inspect(body: PresetInspectIn) -> PresetInspectOut:
    """Read any preset on the account and say whether Vibonomics could take it over. Writes nothing."""
    try:
        seen = await presets.inspect(body.slug)
    except (presets.NoApiKey, presets.PresetNotFound, FetchError) as exc:
        raise _unavailable(exc) from exc
    remote = seen.remote
    provider = remote.config.get("provider") if isinstance(remote.config.get("provider"), dict) else {}
    return PresetInspectOut(
        slug=remote.slug,
        status=remote.status,
        version=remote.version,
        updated_at=remote.updated_at,
        preset_id=remote.preset_id,
        workspace=remote.workspace,
        config=remote.config,
        has_system_prompt=bool(remote.system_prompt),
        system_prompt=_capped(remote.system_prompt),
        models=presets.current_models(remote.config),
        providers=[p for p in provider.get("only") or [] if isinstance(p, str)],
        versions=[PresetVersionOut(**v) for v in remote.versions],
        managed_keys=list(presets.MANAGED_KEYS),
        passthrough=seen.passthrough,
        managed=seen.managed,
        adoptable=seen.adoptable,
        blockers=seen.blockers,
        digest=seen.digest,
    )


@router.post("/adopt", response_model=PresetSettings)
async def preset_adopt(body: PresetAdoptIn) -> PresetSettings:
    """Hand a preset made by hand over to Vibonomics, if it is still what `inspect` showed.

    Writes nothing to OpenRouter: the preset joins the list switched off, and its first update is a later step.
    """
    try:
        item = await presets.adopt(body.slug, body.digest)
    except (presets.NoApiKey, presets.PresetNotFound, FetchError) as exc:
        raise _unavailable(exc) from exc
    except (presets.PlanChanged, presets.AdoptRefused) as exc:
        raise HTTPException(409, str(exc)) from exc
    events.publish("settings.changed", {})
    events.publish("presets.changed", {"status": "adopted"})
    return item


@router.post("/apply", response_model=PresetApplyOut)
async def preset_apply(body: PresetApplyIn) -> PresetApplyOut:
    """Write the plan the caller reviewed, if it is still the plan and there is something to write."""
    try:
        plan, applied = await presets.apply(body.slug, body.digest, "manual")
    except presets.PresetNotConfigured as exc:
        raise HTTPException(404, str(exc)) from exc
    except presets.PresetDisabled as exc:
        raise HTTPException(409, str(exc)) from exc
    except (presets.PlanChanged, presets.PresetInvalid) as exc:
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
