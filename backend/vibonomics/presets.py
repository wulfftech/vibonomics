"""Keeps one OpenRouter preset pointed at the best-value model, and shows what it would write before it writes.

A preset is a named bundle of model and routing settings that a client selects with ``model: "@preset/<slug>"``.
Each write creates a new version and makes it the live one, so a harness that cannot pin a provider can still follow
whichever model is the best value today.

What this module touches, and what it leaves alone:

* It owns the ``model`` and the ``provider.only`` / ``provider.order`` lists. Everything else in the preset
  (tools, reasoning, caching, other provider filters) is read from the preset as it stands and sent back as it was, so
  an edit made in the OpenRouter UI survives. OpenRouter keeps no partial update: each write is the whole config.
* It only ever writes a slug that starts with ``vibonomics-`` and that it created itself. A preset with that name made
  by hand is refused, never overwritten. The ``template_slug`` preset is read and never written.
* Every plan carries the exact request, so the dry run is the same body the apply sends. Apply takes the digest of the
  plan the operator looked at and refuses if the plan has changed since.

OpenRouter has no way to delete a preset through its API, so this module never does.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from .db import session_scope
from .http import FetchError, get_json, request
from .jobs import failure_text
from .orm import Model, ProviderConfig, Setting, utcnow
from .pricing import Task, task_cost, value_of
from .provider_service import resolve
from .providers.openrouter import OpenRouterAdapter
from .scoring import compute_scores, own_test_scores, score_key
from .settings_store import DEFAULT_PROFILES, AppSettings, PresetSettings, load_settings

log = logging.getLogger(__name__)

STATE_KEY = "preset_state"
# The create endpoint insists on `messages` and then ignores them: only fields that overlap the preset config are kept.
PLACEHOLDER_MESSAGES = [{"role": "user", "content": "Vibonomics preset update"}]
RUNNERS_UP = 5
VERSIONS_SHOWN = 8
LISTED_PRESETS = 50
# Provider filters OpenRouter applies itself, which the endpoint ranking here cannot see.
UNSEEN_FILTERS = ("zdr", "data_collection", "max_price", "require_parameters")

_lock = asyncio.Lock()


class PresetDisabled(Exception):
    """Applying was asked for while the operator has not allowed Vibonomics to write the preset."""


class PlanChanged(Exception):
    """The plan the operator reviewed is no longer what would be written."""


class PresetWriteError(Exception):
    """OpenRouter refused or failed the write."""


@dataclass
class Candidate:
    model_id: str
    name: str
    score: float
    task_cost: float
    value: float
    discount: float | None
    cache_read: float | None
    endpoints: list[dict[str, Any]]


@dataclass
class Change:
    path: str
    before: str | None
    after: str | None


@dataclass
class Remote:
    """A preset as OpenRouter holds it: its live version, and the history behind it."""

    slug: str
    version: int | None
    config: dict[str, Any]
    system_prompt: str | None
    updated_at: str | None
    status: str = "active"  # an archived preset takes no new versions
    versions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Plan:
    slug: str
    action: str  # create | update | unchanged | blocked
    reason: str
    settings: PresetSettings
    profile: str | None = None
    task: Task | None = None
    considered: int = 0
    pick: Candidate | None = None  # the model the preset leads with
    picks: list[Candidate] = field(default_factory=list)  # every model it lists, in order
    kept_current: bool = False
    providers: list[dict[str, Any]] = field(default_factory=list)
    runners_up: list[Candidate] = field(default_factory=list)
    current: Remote | None = None
    config: dict[str, Any] | None = None
    url: str | None = None
    body: dict[str, Any] | None = None
    digest: str | None = None
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=utcnow)


@dataclass
class Applied:
    version: int | None
    unsaved: list[str]
    message: str


@dataclass
class Inputs:
    settings: AppSettings
    candidates: list[Candidate]
    task: Task
    profile_id: str
    base_url: str
    api_key: str | None
    state: dict[str, Any]


# ---- ranking -------------------------------------------------------------------------------------------------


def _task(settings: AppSettings) -> tuple[Task, str]:
    profiles = settings.scoring.profiles or DEFAULT_PROFILES
    wanted = settings.presets.profile or settings.scoring.default_profile
    profile = next((p for p in profiles if p.id == wanted), profiles[0])
    return Task(profile.task_input_tokens, profile.task_output_tokens, profile.cache_hit_rate), profile.id


def rank(session: Session, settings: AppSettings, provider_ids: list[int]) -> tuple[list[Candidate], Task, str]:
    """The OpenRouter models that pass the operator's filters, best value first."""
    cfg = settings.presets
    task, profile_id = _task(settings)
    if not provider_ids:
        return [], task, profile_id
    scores = compute_scores(session, settings, own_test_scores(session))
    free_cost = max(0.0, settings.scoring.free_model_cost)
    best: dict[str, Candidate] = {}
    query = select(Model).where(Model.provider_config_id.in_(provider_ids), Model.active.is_(True))
    for m in session.scalars(query):
        if m.model_id.endswith(":batch") or (m.is_free and not cfg.include_free):
            continue
        if cfg.require_tool_call and m.tool_call is not True:
            continue
        if cfg.min_context and (m.context_length or 0) < cfg.min_context:
            continue
        sc = scores.get(score_key(m.canonical_id, m.provider_config_id, m.model_id))
        score = getattr(sc, cfg.dimension) if sc else None
        cost = task_cost(m.price_input, m.price_output, m.price_cache_read, task, m.is_free)
        if score is None or cost is None or score < cfg.min_score:
            continue
        value_cost = task_cost(free_cost, free_cost, free_cost, task) if m.is_free else cost
        value = value_of(score, value_cost)
        if value is None or not math.isfinite(value):
            continue
        found = Candidate(
            model_id=m.model_id,
            name=m.name,
            score=score,
            task_cost=cost,
            value=value,
            discount=m.discount,
            cache_read=m.price_cache_read,
            endpoints=list((m.extra or {}).get("endpoints") or []),
        )
        if m.model_id not in best or found.value > best[m.model_id].value:
            best[m.model_id] = found
    return sorted(best.values(), key=lambda c: (-c.value, -c.score, c.task_cost, c.model_id)), task, profile_id


def choose_endpoints(
    endpoints: list[dict[str, Any]],
    cache_read: float | None,
    task: Task,
    provider_block: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """The cheapest endpoints for one task that the preset's own provider filters would still let through."""
    quantizations = {q.lower() for q in provider_block.get("quantizations") or [] if isinstance(q, str)}
    ignored = {s.lower() for s in provider_block.get("ignore") or [] if isinstance(s, str)}
    usable: list[tuple[float, float, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for ep in endpoints:
        tag, price_in, price_out = ep.get("tag"), ep.get("input"), ep.get("output")
        if not isinstance(tag, str) or not tag or tag in seen or price_in is None or price_out is None:
            continue
        if quantizations and str(ep.get("quantization") or "").lower() not in quantizations:
            continue
        if {tag.lower(), tag.split("/")[0].lower(), str(ep.get("provider") or "").lower()} & ignored:
            continue
        cost = task_cost(price_in, price_out, cache_read, task)
        if cost is None:
            continue
        seen.add(tag)
        uptime = ep.get("uptime_1d")
        usable.append((cost, -(uptime if isinstance(uptime, int | float) else 0), tag, ep))
    usable.sort(key=lambda u: u[:3])
    return [
        {
            "tag": tag,
            "provider": ep.get("provider"),
            "input": ep.get("input"),
            "output": ep.get("output"),
            "discount": ep.get("discount") or 0.0,
            "quantization": ep.get("quantization"),
            "task_cost": round(cost, 6),
        }
        for cost, _uptime, tag, ep in usable[:limit]
    ]


# ---- the config ----------------------------------------------------------------------------------------------


def build_config(base: dict[str, Any] | None, model_ids: list[str], tags: list[str]) -> dict[str, Any]:
    """``base`` with the models and the provider lists replaced; every other key is the operator's.

    One model is ``model``. Several are ``model`` (the lead) and ``models`` (all of them, in order, the lead first),
    so a preset that keeps only one of the two is still a working preset.
    """
    config = copy.deepcopy(base) if base else {}
    config["model"] = model_ids[0]
    if len(model_ids) > 1:
        config["models"] = list(model_ids)
    else:
        # A fallback list left from an earlier, longer lineup would put models back that were dropped.
        config.pop("models", None)
    provider = dict(config["provider"]) if isinstance(config.get("provider"), dict) else {}
    if tags:
        provider["only"] = list(tags)
        provider["order"] = list(tags)
    else:
        # A list left over from another model would name providers that do not serve this one.
        provider.pop("only", None)
        provider.pop("order", None)
    provider.setdefault("allow_fallbacks", True)
    config["provider"] = provider
    return config


def current_models(config: dict[str, Any]) -> list[str]:
    """The model ids a preset config names, in order: its ``model``, then any ``models`` fallbacks."""
    listed = config.get("models")
    found = [config.get("model"), *(listed if isinstance(listed, list) else [])]
    out: list[str] = []
    for value in found:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def select_models(
    ranked: list[Candidate], incumbents: list[str], count: int, margin_pct: float
) -> tuple[list[Candidate], bool]:
    """The ``count`` best-value models, with a model already in the preset favoured by ``margin_pct``.

    A challenger has to beat an incumbent by the margin to displace it, and the incumbent lead has to be beaten by
    the margin to lose the lead, so near ties do not rewrite the preset every day. Returns the models in order, and
    whether the margin kept the lineup from being what raw value alone would pick.
    """
    boost = 1 + margin_pct / 100
    chosen = sorted(
        ranked,
        key=lambda c: (-(c.value * (boost if c.model_id in incumbents else 1)), -c.score, c.task_cost, c.model_id),
    )[:count]
    lead = incumbents[0] if incumbents else None
    ordered = sorted(
        chosen,
        key=lambda c: (-(c.value * (boost if c.model_id == lead else 1)), -c.score, c.task_cost, c.model_id),
    )
    return ordered, [c.model_id for c in ordered] != [c.model_id for c in ranked[:count]]


def merge_endpoints(per_model: list[tuple[str, list[dict[str, Any]]]], limit: int) -> list[dict[str, Any]]:
    """One provider list for several models: each model's cheapest endpoint in turn, then its next, up to ``limit``.

    An endpoint two models share is listed once and names both. Round-robin, so the first model cannot use every slot.
    """
    merged: dict[str, dict[str, Any]] = {}
    for rank in range(max((len(eps) for _model, eps in per_model), default=0)):
        for model_id, eps in per_model:
            if rank >= len(eps):
                continue
            tag = eps[rank]["tag"]
            if tag in merged:
                if model_id not in merged[tag]["models"]:
                    merged[tag]["models"].append(model_id)
            elif len(merged) < limit:
                merged[tag] = {**eps[rank], "models": [model_id]}
    return list(merged.values())


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True)


def diff_config(before: dict[str, Any] | None, after: dict[str, Any]) -> list[Change]:
    """What differs, key by key, with the provider block opened up one level."""
    before = before or {}
    changes: list[Change] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if key == "provider" and isinstance(old, dict | type(None)) and isinstance(new, dict):
            old = old or {}
            for sub in sorted(set(old) | set(new)):
                if old.get(sub) != new.get(sub):
                    changes.append(Change(f"provider.{sub}", _json(old.get(sub)), _json(new.get(sub))))
        elif old != new:
            changes.append(Change(key, _json(old), _json(new)))
    return changes


def unsaved_keys(desired: dict[str, Any], stored: dict[str, Any]) -> list[str]:
    """The keys OpenRouter did not keep as sent: it persists only what overlaps its own preset config."""
    out: list[str] = []
    for key, want in desired.items():
        have = stored.get(key)
        if key == "provider" and isinstance(want, dict) and isinstance(have, dict):
            out += [f"provider.{sub}" for sub, value in want.items() if have.get(sub) != value]
        elif have != want:
            out.append(key)
    return out


def digest_of(slug: str, config: dict[str, Any]) -> str:
    raw = json.dumps({"slug": slug, "config": config}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---- state ---------------------------------------------------------------------------------------------------


def _load_state(session: Session) -> dict[str, Any]:
    row = session.get(Setting, STATE_KEY)
    return dict(row.value) if row is not None and row.value else {}


def read_state() -> dict[str, Any]:
    """The local record of what was written; reads no network."""
    with session_scope() as s:
        return _load_state(s)


def _update_state(patch: dict[str, Any], own: str | None = None) -> None:
    with session_scope() as s:
        row = s.get(Setting, STATE_KEY)
        state = dict(row.value) if row is not None and row.value else {}
        state.update(patch)
        if own and own not in state.get("owned", []):
            state["owned"] = [*state.get("owned", []), own]
        if row is None:
            s.add(Setting(key=STATE_KEY, value=state))
        else:
            row.value = state


def _record_run(slug: str, status: str, message: str, trigger: str, patch: dict[str, Any] | None = None) -> None:
    last = {"at": utcnow().isoformat(), "status": status, "message": message[:500], "trigger": trigger, "slug": slug}
    _update_state({**(patch or {}), "last": last})
    events.publish("presets.changed", {"status": status})


def is_owned(state: dict[str, Any], slug: str) -> bool:
    return slug in (state.get("owned") or [])


# ---- OpenRouter ----------------------------------------------------------------------------------------------


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def fetch_remote(base: str, key: str, slug: str) -> Remote | None:
    """The preset with this slug and its recent versions; None when the account has no such preset."""
    path = f"{base}/presets/{quote(slug, safe='')}"
    try:
        data = (await get_json(path, headers=_auth(key), timeout=20, retries=1)).get("data") or {}
    except FetchError as exc:
        if exc.status == 404:
            return None
        raise
    live = data.get("designated_version") or {}
    remote = Remote(
        slug=slug,
        version=live.get("version"),
        config=live.get("config") if isinstance(live.get("config"), dict) else {},
        system_prompt=live.get("system_prompt"),
        updated_at=data.get("updated_at"),
        status=str(data.get("status") or "active"),
    )
    try:
        listing = (await get_json(f"{path}/versions", headers=_auth(key), timeout=20, retries=1)).get("data") or []
    except FetchError:
        listing = []  # the history is for show; the plan stands without it
    rows = sorted((v for v in listing if isinstance(v, dict)), key=lambda v: v.get("version") or 0, reverse=True)
    for v in rows[:VERSIONS_SHOWN]:
        cfg = v.get("config") if isinstance(v.get("config"), dict) else {}
        provider = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
        remote.versions.append(
            {
                "version": v.get("version"),
                "created_at": v.get("created_at"),
                "model": cfg.get("model"),
                "providers": [p for p in provider.get("only") or [] if isinstance(p, str)],
                "live": v.get("version") == remote.version,
            }
        )
    return remote


# ---- planning ------------------------------------------------------------------------------------------------


def _access(resolved: list[Any]) -> tuple[str, str | None]:
    keyed = next((r for r in resolved if r.api_key), None)
    base = (keyed.base_url if keyed and keyed.base_url else OpenRouterAdapter.spec.default_base_url).rstrip("/")
    return base, keyed.api_key if keyed else None


def _openrouter_providers(s: Session) -> list[ProviderConfig]:
    return list(
        s.scalars(
            select(ProviderConfig)
            .where(ProviderConfig.type == "openrouter", ProviderConfig.enabled.is_(True))
            .order_by(ProviderConfig.id)
        )
    )


def load_access() -> tuple[str, str | None, dict[str, Any]]:
    """Where to reach OpenRouter, with which key, and what was written before. Ranks nothing."""
    with session_scope() as s:
        base, key = _access([resolve(p) for p in _openrouter_providers(s)])
        return base, key, _load_state(s)


def load_inputs() -> Inputs:
    with session_scope() as s:
        settings = load_settings(s)
        providers = _openrouter_providers(s)
        resolved = [resolve(p) for p in providers]
        candidates, task, profile_id = rank(s, settings, [p.id for p in providers])
        state = _load_state(s)
    base, key = _access(resolved)
    return Inputs(settings, candidates, task, profile_id, base, key, state)


async def list_presets(base: str, key: str, owned: list[str]) -> list[dict[str, Any]]:
    """The account's active presets, newest first, with the models and providers each names.

    Presets Vibonomics wrote are left out: one of them is no use as the template for another.
    """
    listing = (await get_json(f"{base}/presets", headers=_auth(key), timeout=20, retries=1)).get("data") or []
    rows = [
        p
        for p in listing
        if isinstance(p, dict)
        and isinstance(p.get("slug"), str)
        and p["slug"] not in owned
        and p.get("status") != "archived"
    ][:LISTED_PRESETS]
    gate = asyncio.Semaphore(6)

    async def one(p: dict[str, Any]) -> dict[str, Any]:
        data: dict[str, Any] = {}
        async with gate:
            try:
                got = await get_json(f"{base}/presets/{quote(p['slug'], safe='')}", headers=_auth(key), timeout=20)
                data = got.get("data") or {}
            except FetchError:
                pass  # the row is still worth listing, without its models
        live = data.get("designated_version") or {}
        cfg = live.get("config") if isinstance(live.get("config"), dict) else {}
        provider = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
        return {
            "slug": p["slug"],
            "name": str(p.get("name") or p["slug"]),
            "version": live.get("version"),
            "updated_at": p.get("updated_at"),
            "models": current_models(cfg),
            "providers": [x for x in provider.get("only") or [] if isinstance(x, str)],
        }

    found = await asyncio.gather(*(one(p) for p in rows))
    return sorted(found, key=lambda r: r["updated_at"] or "", reverse=True)


def _blocked(plan: Plan, reason: str) -> Plan:
    plan.action, plan.reason = "blocked", reason
    return plan


def _percent(better: float, worse: float) -> int:
    return round((better / worse - 1) * 100) if worse > 0 else 0


async def make_plan(inputs: Inputs | None = None) -> Plan:
    """What the next update would do. Reads from OpenRouter, writes nothing."""
    inputs = inputs or await asyncio.to_thread(load_inputs)
    cfg = inputs.settings.presets
    plan = Plan(
        slug=cfg.slug,
        action="blocked",
        reason="",
        settings=cfg,
        profile=inputs.profile_id,
        task=inputs.task,
        considered=len(inputs.candidates),
        state=inputs.state,
    )
    if not inputs.api_key:
        return _blocked(plan, "Add an OpenRouter API key under Settings, Providers first.")
    try:
        remote = await fetch_remote(inputs.base_url, inputs.api_key, cfg.slug)
    except FetchError as exc:
        return _blocked(plan, f"Could not read the preset from OpenRouter: {exc}")
    plan.current = remote
    if remote is not None and remote.status == "archived":
        return _blocked(
            plan,
            f"The preset {cfg.slug} is archived on OpenRouter, and an archived preset takes no new versions. "
            "Restore it in the OpenRouter web UI, or give this one another name.",
        )
    if remote is not None and not is_owned(inputs.state, cfg.slug):
        return _blocked(
            plan,
            f"A preset called {cfg.slug} already exists on this account and Vibonomics did not create it, so it is "
            "left alone. Give this one another name, or archive that preset in the OpenRouter web UI.",
        )
    if remote is not None and remote.system_prompt:
        return _blocked(
            plan,
            "The preset has a system prompt, and OpenRouter's API cannot carry one over to a new version. Clear it "
            "in the OpenRouter web UI, or use another name.",
        )
    if not inputs.candidates:
        return _blocked(plan, "No OpenRouter model passes the filters. Loosen the minimum score or the other filters.")

    before = current_models(remote.config) if remote else []
    plan.picks, plan.kept_current = select_models(inputs.candidates, before, cfg.max_models, cfg.switch_margin_pct)
    pick = plan.pick = plan.picks[0]
    plan.runners_up = [c for c in inputs.candidates if c not in plan.picks][:RUNNERS_UP]

    base_config = remote.config if remote else None
    if remote is None and cfg.template_slug:
        try:
            template = await fetch_remote(inputs.base_url, inputs.api_key, cfg.template_slug)
        except FetchError as exc:
            return _blocked(plan, f"Could not read the template preset {cfg.template_slug}: {exc}")
        if template is None:
            return _blocked(plan, f"The template preset {cfg.template_slug} does not exist on this account.")
        base_config = template.config
        if template.system_prompt:
            plan.warnings.append(
                "The template has a system prompt, which a new preset cannot take over; it is left out."
            )

    provider_block = (base_config or {}).get("provider")
    provider_block = provider_block if isinstance(provider_block, dict) else {}
    plan.providers = merge_endpoints(
        [
            (c.model_id, choose_endpoints(c.endpoints, c.cache_read, inputs.task, provider_block, cfg.max_providers))
            for c in plan.picks
        ],
        cfg.max_providers,
    )
    if not plan.providers:
        plan.warnings.append(
            "No provider data for these models (or none that passes the preset's quantization and ignore filters), "
            "so the preset names no provider and OpenRouter routes as it decides. Provider data comes from the "
            "'Check OpenRouter endpoint discounts' refresh option."
        )
    unseen = [k for k in UNSEEN_FILTERS if k in provider_block]
    if unseen:
        plan.warnings.append(
            f"OpenRouter applies {', '.join(unseen)} itself and this ranking cannot see it, so a listed provider "
            "may be filtered out at request time."
        )

    ids = [c.model_id for c in plan.picks]
    plan.config = build_config(base_config, ids, [p["tag"] for p in plan.providers])
    plan.url = f"{inputs.base_url}/presets/{quote(cfg.slug, safe='')}/chat/completions"
    plan.body = {**plan.config, "messages": PLACEHOLDER_MESSAGES}
    plan.digest = digest_of(cfg.slug, plan.config)
    plan.changes = diff_config(remote.config if remote else None, plan.config)

    if remote is None:
        plan.action = "create"
        plan.reason = (
            f"Creates {cfg.slug} pointing at {pick.name}."
            if len(ids) == 1
            else f"Creates {cfg.slug} with {len(ids)} models, led by {pick.name}."
        )
    elif plan.config == remote.config:
        plan.action = "unchanged"
        best = inputs.candidates[0]
        plan.reason = "Already up to date." + (
            f" {best.name} scores higher on value, but by less than {cfg.switch_margin_pct:g}%, so the lineup stays."
            if plan.kept_current
            else ""
        )
    elif before != ids:
        plan.action, plan.reason = "update", _model_change(before, plan.picks, inputs.candidates)
    else:
        plan.action = "update"
        plan.reason = f"Same {'model' if len(ids) == 1 else 'models'}, {pick.name}; the provider list changed."
    return plan


def _model_change(before: list[str], picks: list[Candidate], candidates: list[Candidate]) -> str:
    """One sentence on how the model lineup changes."""
    ids = [c.model_id for c in picks]
    if len(ids) == 1 and len(before) == 1:
        old = next((c for c in candidates if c.model_id == before[0]), None)
        gain = f", {_percent(picks[0].value, old.value)}% more value" if old is not None else ""
        return f"Switches {before[0]} to {picks[0].name}{gain}."
    names = {c.model_id: c.name for c in candidates}
    parts = []
    if added := [names.get(i, i) for i in ids if i not in before]:
        parts.append("adds " + ", ".join(added))
    if dropped := [i for i in before if i not in ids]:
        parts.append("drops " + ", ".join(dropped))
    if parts:
        return f"Model list: {'; '.join(parts)}. Leads with {picks[0].name}."
    return f"Same models in a new order, leading with {picks[0].name}."


# ---- applying ------------------------------------------------------------------------------------------------


async def apply(digest: str | None, trigger: str) -> tuple[Plan, Applied | None]:
    """Write the plan if there is something to write. ``digest`` pins it to a plan the operator reviewed."""
    async with _lock:
        inputs = await asyncio.to_thread(load_inputs)
        cfg = inputs.settings.presets
        if not cfg.enabled:
            raise PresetDisabled("Turn on 'Let Vibonomics write the preset' first.")
        plan = await make_plan(inputs)
        if digest is not None and plan.digest != digest:
            raise PlanChanged("The plan changed since you looked at it. Preview it again.")
        if plan.action not in ("create", "update") or plan.body is None or plan.url is None or plan.config is None:
            await asyncio.to_thread(_record_run, cfg.slug, plan.action, plan.reason, trigger)
            return plan, None
        assert inputs.api_key is not None
        try:
            resp = await request(
                "POST",
                plan.url,
                headers={**_auth(inputs.api_key), "Content-Type": "application/json"},
                json_body=plan.body,
                timeout=30,
                retries=0,  # a retried create would leave a second version behind
            )
            data = (resp.json() or {}).get("data") or {}
        except (FetchError, ValueError) as exc:
            message = f"OpenRouter did not take the update: {exc}"
            await asyncio.to_thread(_record_run, cfg.slug, "failed", message, trigger)
            raise PresetWriteError(message) from exc
        live = data.get("designated_version") or {}
        stored = live.get("config") if isinstance(live.get("config"), dict) else {}
        unsaved = unsaved_keys(plan.config, stored)
        version = live.get("version")
        verb = "Created" if plan.action == "create" else "Updated"
        message = f"{verb} {cfg.slug} as version {version}: {plan.reason}"
        if unsaved:
            message += f" OpenRouter did not keep: {', '.join(unsaved)}."
        applied = {
            "slug": cfg.slug,
            "model_id": plan.config.get("model"),
            "version": version,
            "at": datetime.now(UTC).isoformat(),
        }
        # The slug becomes ours the moment OpenRouter has created it, even if recording the run then fails.
        await asyncio.to_thread(_update_state, {"applied": applied}, cfg.slug)
        await asyncio.to_thread(_record_run, cfg.slug, "applied", message, trigger)
        return plan, Applied(version=version, unsaved=unsaved, message=message)


async def sync(trigger: str = "schedule") -> None:
    """The scheduled run: apply, and keep going whatever happens."""
    try:
        plan, applied = await apply(None, trigger)
    except PresetDisabled:
        return
    except PresetWriteError as exc:
        log.warning("Preset update failed: %s", exc)
        return
    except Exception as exc:  # the scheduler must survive any single failure
        log.exception("Preset sync failed")
        await asyncio.to_thread(_record_run, "", "failed", f"{type(exc).__name__}: {failure_text(exc)}", trigger)
        return
    log.info("Preset %s: %s", plan.slug, applied.message if applied else plan.reason)
