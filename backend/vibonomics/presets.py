"""Keeps OpenRouter presets pointed at the best-value models, and shows what it would write before it writes.

A preset is a named bundle of model and routing settings that a client selects with ``model: "@preset/<slug>"``.
Each write creates a new version and makes it the live one, so a harness that cannot pin a provider can still follow
whichever model is the best value today. Every preset in ``settings.presets.items`` is planned, applied and recorded
by its slug.

What this module touches, and what it leaves alone:

* It owns ``model``, ``models``, ``route`` and the ``provider.only`` / ``provider.order`` / ``provider.sort`` keys.
  Everything else in the preset (tools, reasoning, caching, other provider filters: the passthrough) is read from the
  preset as it stands and sent back as it was, so an edit made in the OpenRouter UI survives. OpenRouter keeps no
  partial update: each write is the whole config.
* It only ever writes a slug that starts with ``vibonomics-`` and that it created itself, or one the operator adopted
  through ``adopt`` after being shown the passthrough. A preset with that name made by hand is refused, never
  overwritten. The ``template_slug`` preset is read and never written.
* Every plan carries the exact request, so the dry run is the same body the apply sends. Apply takes the digest of the
  plan the operator looked at and refuses if the plan has changed since.
* The ranking runs on a stored copy of the model data. Each plan therefore ends with a check against the live listing
  (``preset_validation``), and an apply that finds an error in it writes nothing.

OpenRouter has no way to delete a preset through its API, so this module never does.
"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import hashlib
import json
import logging
import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from .db import session_scope
from .http import FetchError, get_json, public_url, request, scrub_secrets
from .jobs import failure_text
from .orm import Model, ProviderConfig, Setting, utcnow
from .preset_validation import Validation, cap_below, unsupported, validate_live
from .pricing import Task, task_cost, value_of
from .provider_service import resolve
from .providers.openrouter import OpenRouterAdapter
from .scoring import compute_scores, own_test_scores, score_key
from .settings_store import (
    DEFAULT_PROFILES,
    PRESET_MAX_ITEMS,
    AppSettings,
    PresetGuardrails,
    PresetSettings,
    adopted_slug,
    load_settings,
    save_settings,
)

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


class PresetNotConfigured(Exception):
    """A slug that is not among the presets Vibonomics manages was asked for."""


class PresetNotFound(Exception):
    """OpenRouter has no preset with that slug."""


class NoApiKey(Exception):
    """There is no OpenRouter key to read the account with."""


class AdoptRefused(Exception):
    """A preset cannot be handed over to Vibonomics, and the message says why."""


class PresetDisabled(Exception):
    """Applying was asked for while the operator has not allowed Vibonomics to write the preset."""


class PlanChanged(Exception):
    """The plan the operator reviewed is no longer what would be written."""


class PresetWriteError(Exception):
    """OpenRouter refused or failed the write."""


class PresetInvalid(Exception):
    """The live listing, read as the write was asked for, shows the plan is wrong, so nothing was written."""

    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("Live check failed: " + "; ".join(messages))


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
    expires: str | None = None  # the date OpenRouter said, at the last refresh, that the model goes away


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
    preset_id: str | None = None  # whatever stable id the account gave it; None when the response names none
    created_at: str | None = None
    readable: bool = True  # False when the response held no live config to plan from, which is not an empty config
    workspace: str | None = None  # the workspace the response names (slug or id), for linking to the preset's page


@dataclass
class Excluded:
    """What the blocklists took out of one run's ranking."""

    models: int = 0
    providers: list[str] = field(default_factory=list)  # display names of the providers whose endpoints were skipped


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
    draft: bool = False  # made from unsaved settings, so it cannot be applied
    excluded: Excluded = field(default_factory=Excluded)  # what the blocklists took out of this run
    own_tests_preferred: bool = False  # the scores the ranking used: the model's own tests over the computed ones
    # The system prompt on the live preset, the one this plan's write carries (None: no system message is sent), and
    # where that one comes from: managed | cleared | kept (the live one, sent back as it is) | none.
    live_prompt: str | None = None
    prompt: str | None = None
    prompt_source: str = "none"
    passthrough: dict[str, Any] = field(default_factory=dict)  # the keys that are sent back as they were found
    reviewed: str | None = None  # the passthrough digest to record as seen, once an apply the owner watched is done
    fingerprint: str | None = None  # which key and host the plan was made for
    # The listed models whose providers the preset names once this plan stands; None when nothing is to be recorded.
    pinned: list[str] | None = None
    # The live listing's verdict on what this plan would write; None when nothing was checked (a blocked plan). It is
    # not part of the digest: the same config is the same plan whatever the listing says at the moment.
    validation: Validation | None = None
    generated_at: datetime = field(default_factory=utcnow)


@dataclass
class Applied:
    version: int | None
    unsaved: list[str]
    message: str


@dataclass
class Inputs:
    settings: AppSettings
    preset: PresetSettings  # the item this plan is for: the saved one, or the draft standing in for it
    candidates: list[Candidate]
    task: Task
    profile_id: str
    base_url: str
    api_key: str | None
    state: dict[str, Any]
    draft: bool = False  # the settings are unsaved ones, offered for a preview
    ignored_base: str | None = None  # a Base URL override on the provider row, which the preset calls do not use
    excluded: Excluded = field(default_factory=Excluded)


# ---- blocklists ----------------------------------------------------------------------------------------------


def _union(*lists: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(entry for entries in lists for entry in entries))


def blocklists(settings: AppSettings, preset: PresetSettings) -> tuple[list[str], list[str]]:
    """The models and the providers ``preset`` never considers: its own entries and those for every preset."""
    shared = settings.presets
    return (
        _union(shared.blocked_models, preset.blocked_models),
        _union(shared.blocked_providers, preset.blocked_providers),
    )


def _blocked_by(names: Iterable[str | None], patterns: Sequence[str]) -> bool:
    """Whether any of ``names`` matches any pattern, ignoring case; ``*`` and ``?`` are the wildcards."""
    lowered = [n.lower() for n in names if isinstance(n, str) and n]
    return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns for name in lowered)


def _endpoint_names(ep: dict[str, Any]) -> list[str]:
    """The ways a provider is named by an endpoint: its tag, the tag's provider part, and its display name."""
    tag = ep.get("tag")
    tag = tag if isinstance(tag, str) else ""
    return [tag, tag.split("/")[0], str(ep.get("provider") or "")]


def _provider_label(ep: dict[str, Any]) -> str:
    tag = ep.get("tag") if isinstance(ep.get("tag"), str) else ""
    return str(ep.get("provider") or tag.split("/")[0])


# ---- ranking -------------------------------------------------------------------------------------------------


def _task(settings: AppSettings, preset: PresetSettings) -> tuple[Task, str]:
    profiles = settings.scoring.profiles or DEFAULT_PROFILES
    wanted = preset.profile or settings.scoring.default_profile
    profile = next((p for p in profiles if p.id == wanted), profiles[0])
    return Task(profile.task_input_tokens, profile.task_output_tokens, profile.cache_hit_rate), profile.id


def own_tests_preferred(settings: AppSettings, preset: PresetSettings) -> bool:
    """Whether ``preset``'s ranking takes a model's own test score over the computed one: its own choice, else the
    global Scoring switch."""
    return settings.scoring.prefer_own_tests if preset.prefer_own_tests is None else preset.prefer_own_tests


def rank(
    session: Session,
    settings: AppSettings,
    provider_ids: list[int],
    preset: PresetSettings,
    excluded: Excluded | None = None,
) -> tuple[list[Candidate], Task, str]:
    """The OpenRouter models that pass ``preset``'s filters and blocklists, best value first.

    ``excluded`` collects what the blocklists removed from models that passed everything else, so the plan can say
    what they cost it. A model with endpoint data whose every endpoint is blocked is out; one with no endpoint data
    is not, because nothing says its providers are blocked.
    """
    cfg = preset
    task, profile_id = _task(settings, preset)
    if not provider_ids:
        return [], task, profile_id
    blocked_models, blocked_providers = blocklists(settings, preset)
    dropped: set[str] = set()
    skipped: set[str] = set()
    scores = compute_scores(session, settings, own_test_scores(session), own_tests_preferred(settings, preset))
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
        if cfg.max_task_cost is not None and cost > cfg.max_task_cost:
            continue
        value_cost = task_cost(free_cost, free_cost, free_cost, task) if m.is_free else cost
        value = value_of(score, value_cost)
        if value is None or not math.isfinite(value):
            continue
        if cfg.min_value is not None and value < cfg.min_value:
            continue
        endpoints = [e for e in (m.extra or {}).get("endpoints") or [] if isinstance(e, dict)]
        if blocked_models and _blocked_by((m.model_id, m.canonical_id), blocked_models):
            dropped.add(m.model_id)
            continue
        if blocked_providers and endpoints:
            tagged = [e for e in endpoints if e.get("tag")]  # the only ones a preset could pin
            hits = [e for e in tagged if _blocked_by(_endpoint_names(e), blocked_providers)]
            skipped.update(_provider_label(e) for e in hits)
            if hits and len(hits) == len(tagged):
                dropped.add(m.model_id)
                continue
        found = Candidate(
            model_id=m.model_id,
            name=m.name,
            score=score,
            task_cost=cost,
            value=value,
            discount=m.discount,
            cache_read=m.price_cache_read,
            endpoints=endpoints,
            expires=(m.extra or {}).get("expiration_date") or None,
        )
        if m.model_id not in best or found.value > best[m.model_id].value:
            best[m.model_id] = found
    if excluded is not None:
        excluded.models, excluded.providers = len(dropped), sorted(skipped)
    return sorted(best.values(), key=lambda c: (-c.value, -c.score, c.task_cost, c.model_id)), task, profile_id


def choose_endpoints(
    endpoints: list[dict[str, Any]],
    task: Task,
    provider_block: dict[str, Any],
    limit: int,
    *,
    current: Sequence[str] = (),
    allow: Sequence[str] = (),
    blocked: Sequence[str] = (),
    require_params: Sequence[str] = (),
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """The cheapest endpoints for one task that the preset's own provider filters would still let through.

    ``allow`` is a template's own ``only`` list: an endpoint whose provider (by slug, full tag or name) is not on it
    is skipped. An empty one restricts nothing. ``blocked`` is the blocklist's patterns: an endpoint they match is
    skipped, and nothing is written to the preset to say so.

    ``require_params`` skips an endpoint whose stored parameters lack one of them, and ``max_tokens`` one whose output
    cap is below it. An endpoint stored without that data (before it was kept, or not listed) is not skipped: unknown
    is not a failure.

    Each endpoint is costed with its own cache-read price. One that lists none is costed as uncached: the model's
    listed price is the best endpoint's, and an endpoint without a cache would be ranked ahead on a discount it does
    not give.

    Endpoints that cost the same go in the order ``current`` (the tags the live preset lists) already has them, and
    only then by uptime, which changes on every refresh: left to it, equal prices would flip ``order`` and write a
    new version each time.
    """
    quantizations = {q.lower() for q in provider_block.get("quantizations") or [] if isinstance(q, str)}
    ignored = {s.lower() for s in provider_block.get("ignore") or [] if isinstance(s, str)}
    allowed = {s.lower() for s in allow if isinstance(s, str)}
    position: dict[str, int] = {}
    for i, tag in enumerate(current):
        position.setdefault(tag, i)
    usable: list[tuple[float, int, float, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for ep in endpoints:
        tag, price_in, price_out = ep.get("tag"), ep.get("input"), ep.get("output")
        if not isinstance(tag, str) or not tag or tag in seen or price_in is None or price_out is None:
            continue
        if quantizations and str(ep.get("quantization") or "").lower() not in quantizations:
            continue
        names = {tag.lower(), tag.split("/")[0].lower(), str(ep.get("provider") or "").lower()}
        if (
            names & ignored
            or (allowed and not names & allowed)
            or (blocked and _blocked_by(_endpoint_names(ep), blocked))
            or unsupported(ep.get("params"), require_params)
            or cap_below(ep.get("max_completion_tokens"), max_tokens)
        ):
            continue
        cache = ep.get("cache_read")
        cost = task_cost(price_in, price_out, cache if isinstance(cache, int | float) else None, task)
        if cost is None:
            continue
        seen.add(tag)
        uptime = ep.get("uptime_1d")
        usable.append(
            (cost, position.get(tag, len(position)), -(uptime if isinstance(uptime, int | float) else 0), tag, ep)
        )
    usable.sort(key=lambda u: u[:4])
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
        for cost, _position, _uptime, tag, ep in usable[:limit]
    ]


# ---- the config ----------------------------------------------------------------------------------------------


# The guardrails that sit at the top of the config, and those inside `provider`. The system prompt is neither: it is
# not a config key at OpenRouter, but a system message that goes with each write.
GUARDRAIL_TOP = ("temperature", "max_tokens", "repetition_penalty", "frequency_penalty", "presence_penalty", "top_p")
GUARDRAIL_PROVIDER = ("quantizations", "ignore", "require_parameters")
SYSTEM_KEY = "system_prompt"
# The numeric ones: a parameter an endpoint may not support, so setting one narrows which endpoints are worth pinning.
NEEDS_SUPPORT = GUARDRAIL_TOP


def guardrail_path(key: str) -> str:
    """Where a guardrail key sits in a config: ``provider.<key>`` inside the provider block, else the key itself."""
    return f"provider.{key}" if key in GUARDRAIL_PROVIDER else key


def _guard(config: dict[str, Any], provider: dict[str, Any], guardrails: PresetGuardrails | None) -> bool:
    """Set, or take off, each managed guardrail in ``config`` and ``provider`` (both edited in place).

    Returns whether a key was taken off the provider block, which can leave it empty. A key that is not managed is
    not touched: whatever the live preset has stays.
    """
    if guardrails is None:
        return False
    values = guardrails.set_values()
    removed = False
    for key in GUARDRAIL_TOP:
        if key in values:
            config[key] = values[key]
        elif key in guardrails.clear:
            config.pop(key, None)
    for key in GUARDRAIL_PROVIDER:
        if key in values:
            provider[key] = values[key]
        elif key in guardrails.clear and key in provider:
            del provider[key]
            removed = True
    return removed


def apply_guardrails(config: dict[str, Any], guardrails: PresetGuardrails | None) -> dict[str, Any]:
    """A copy of ``config`` with the managed guardrails in force. What a plan filters endpoints by is this, not the
    live config: a value the owner set is what applies, and one they cleared no longer does."""
    out = copy.deepcopy(config)
    raw = out.get("provider")
    provider = raw if isinstance(raw, dict) else {}
    removed = _guard(out, provider, guardrails)
    if provider:
        out["provider"] = provider
    elif removed:
        out.pop("provider", None)
    return out


def build_config(
    base: dict[str, Any] | None,
    model_ids: list[str],
    tags: list[str],
    route: str = "fallback",
    sort: str | None = None,
    guardrails: PresetGuardrails | None = None,
) -> dict[str, Any]:
    """``base`` with the models, the provider lists and the route replaced; every other key is the operator's.

    One model is ``model``. Several are ``model`` (the lead) and ``models`` (all of them, in order, the lead first),
    so a preset that keeps only one of the two is still a working preset. ``route`` ``sort`` writes ``route: "sort"``
    and ``provider.sort`` (price unless ``sort`` says otherwise); ``fallback`` writes neither. A guardrail the owner
    manages is set, or taken off; one they do not is left as ``base`` has it.
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
    if route == "sort":
        config["route"] = "sort"
        provider["sort"] = sort or "price"
    else:
        # Fallback is OpenRouter's own behaviour, so it is written as no key at all, and the two keys a sort route
        # sets are taken off a preset that had them: they are Vibonomics's to decide.
        config.pop("route", None)
        provider.pop("sort", None)
    removed = _guard(config, provider, guardrails)
    # No default is written for the operator (allow_fallbacks is on at OpenRouter already): a key that Vibonomics adds
    # and the account does not keep would make every later plan differ from the live preset.
    if provider:
        config["provider"] = provider
    elif removed:
        # A block that only a guardrail taken off emptied is not worth writing: OpenRouter may not keep an empty one.
        config.pop("provider", None)
    elif "provider" in config:
        config["provider"] = provider
    return config


def _prompt_text(value: str | None) -> str | None:
    """A system prompt as it is sent: nothing and an empty one are the same."""
    return value if isinstance(value, str) and value else None


def _norm_prompt(value: str | None) -> str | None:
    """A system prompt as two of them are compared: space around the text is not a difference, since a store is free
    to trim it, and a version made for that alone would be one nobody asked for."""
    text = (value or "").strip() if isinstance(value, str) else ""
    return text or None


def prompt_in_force(guardrails: PresetGuardrails, live: str | None) -> tuple[str | None, str]:
    """The system prompt a write carries, and whose it is.

    OpenRouter takes the prompt from the system message of each write, and a write with none sets it to null: the
    version before it is not carried over. So a prompt the owner does not manage is the live one, sent back as it is.
    """
    if guardrails.system_prompt is not None:
        return guardrails.system_prompt, "managed"
    if SYSTEM_KEY in guardrails.clear:
        return None, "cleared"
    live = _prompt_text(live)
    return live, "kept" if live is not None else "none"


def write_body(config: dict[str, Any], prompt: str | None) -> dict[str, Any]:
    """The request that makes a version: the config, and the messages the create endpoint insists on.

    A system message is the only way to give the version a system prompt, so it is there whenever one is in force.
    """
    messages = [dict(m) for m in PLACEHOLDER_MESSAGES]
    if prompt is not None:
        messages.insert(0, {"role": "system", "content": prompt})
    return {**config, "messages": messages}


def owned_view(
    config: dict[str, Any], guardrails: PresetGuardrails | None = None, system_prompt: str | None = None
) -> dict[str, Any]:
    """The only keys Vibonomics decides: what a plan is compared on, to tell an update from nothing to do.

    A guardrail key is in the view exactly when the owner manages it (sets it or takes it off); one they leave alone is
    not Vibonomics's to compare. The system prompt is the pseudo-key ``system_prompt``.
    """
    provider = config.get("provider") if isinstance(config.get("provider"), dict) else {}
    view = {
        "model": config.get("model"),
        "models": config.get("models"),
        "route": config.get("route"),
        "only": provider.get("only"),
        "order": provider.get("order"),
        "sort": provider.get("sort"),
    }
    for key in guardrails.owned_keys() if guardrails else []:
        if key == SYSTEM_KEY:
            view[key] = _norm_prompt(system_prompt)
        elif key in GUARDRAIL_PROVIDER:
            view[key] = provider.get(key)
        else:
            view[key] = config.get(key)
    return view


# What Vibonomics decides in a preset. Every other key is sent back as it was found.
MANAGED_KEYS = ["model", "models", "route", "provider.only", "provider.order", "provider.sort"]
_MANAGED_PROVIDER_KEYS = ("only", "order", "sort")


def managed_keys(guardrails: PresetGuardrails | None = None) -> list[str]:
    """``MANAGED_KEYS`` and the guardrail keys the owner manages in this preset, as config paths."""
    owned = [guardrail_path(key) for key in guardrails.owned_keys()] if guardrails else []
    return [*MANAGED_KEYS, *owned]


def passthrough_view(config: dict[str, Any], guardrails: PresetGuardrails | None = None) -> dict[str, Any]:
    """The keys of ``config`` that Vibonomics does not decide and sends back unchanged.

    That is every top-level key but the managed ones, and the other keys inside ``provider``. A guardrail the owner
    manages is Vibonomics's, so it is not in here; one they leave alone is. It is what the operator is shown before
    handing a preset over, as raw JSON.
    """
    own_top = {k for k in (guardrails.owned_keys() if guardrails else []) if k in GUARDRAIL_TOP}
    own_provider = {k for k in (guardrails.owned_keys() if guardrails else []) if k in GUARDRAIL_PROVIDER}
    hidden = {"model", "models", "route", "provider", *own_top}
    out = {k: copy.deepcopy(v) for k, v in config.items() if k not in hidden}
    provider = config.get("provider")
    if isinstance(provider, dict):
        rest = {
            k: copy.deepcopy(v)
            for k, v in provider.items()
            if k not in _MANAGED_PROVIDER_KEYS and k not in own_provider
        }
        if rest:
            out["provider"] = rest
    elif "provider" in config:
        out["provider"] = copy.deepcopy(provider)
    return out


def passthrough_digest(
    config: dict[str, Any], guardrails: PresetGuardrails | None = None, system_prompt: str | None = None
) -> str:
    """Names the passthrough of ``config``, to tell whether it changed since the operator looked at it.

    The live system prompt is part of it unless the owner manages the prompt: then what is there gets replaced and no
    longer matters. A preset with no prompt hashes as it always did, so a digest recorded earlier still matches.
    """
    view: Any = passthrough_view(config, guardrails)
    prompt = _prompt_text(system_prompt)
    if prompt is not None and SYSTEM_KEY not in (guardrails.owned_keys() if guardrails else []):
        view = [view, prompt]
    raw = json.dumps(view, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# Keys whose storage OpenRouter does not document, so a write may come back without them. The system prompt is
# kept (measured), but a write whose read-back showed it was not is not sent again for the same result.
UNDOCUMENTED = {"route": "route", "provider.sort": "sort", SYSTEM_KEY: SYSTEM_KEY}


def _unkept(state: dict[str, Any], slug: str) -> set[str]:
    """The owned keys of ``slug`` that OpenRouter did not keep when last sent, by their name in ``owned_view``."""
    applied = (state.get("applied") or {}).get(slug) or {}
    return {UNDOCUMENTED[k] for k in applied.get("unsaved") or [] if k in UNDOCUMENTED}


def _owned_differs(
    want: dict[str, Any],
    have: dict[str, Any],
    unkept: Collection[str] = (),
    guardrails: PresetGuardrails | None = None,
    want_prompt: str | None = None,
    have_prompt: str | None = None,
) -> bool:
    """Whether the keys Vibonomics decides differ, leaving out the ones the live preset lacks and cannot be given."""
    a, b = owned_view(want, guardrails, want_prompt), owned_view(have, guardrails, have_prompt)
    for name in unkept:
        if b.get(name) is None:
            a.pop(name, None)
            b.pop(name, None)
    return a != b


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
    ``only`` is one list for every model in the preset, so a model with endpoints always keeps one of its own, even
    where that takes the list past ``limit``: a model with none listed could never be served.
    """
    merged: dict[str, dict[str, Any]] = {}
    served: set[str] = set()
    for rank in range(max((len(eps) for _model, eps in per_model), default=0)):
        for model_id, eps in per_model:
            if rank >= len(eps):
                continue
            tag = eps[rank]["tag"]
            if tag in merged:
                if model_id not in merged[tag]["models"]:
                    merged[tag]["models"].append(model_id)
                served.add(model_id)
            elif len(merged) < limit or model_id not in served:
                merged[tag] = {**eps[rank], "models": [model_id]}
                served.add(model_id)
    return list(merged.values())


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True)


def diff_config(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    before_prompt: str | None = None,
    after_prompt: str | None = None,
) -> list[Change]:
    """What differs, key by key, with the provider block opened up one level.

    The system prompt is not in a config, so it is compared apart and shows as the pseudo-key ``system_prompt``, with
    its text (not JSON) as the before and the after.
    """
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
    if _norm_prompt(before_prompt) != _norm_prompt(after_prompt):
        changes.append(Change(SYSTEM_KEY, _prompt_text(before_prompt), _prompt_text(after_prompt)))
    return changes


def unsaved_keys(desired: dict[str, Any], stored: dict[str, Any], cleared: Iterable[str] = ()) -> list[str]:
    """The keys OpenRouter did not keep as sent: it persists only what overlaps its own preset config.

    ``cleared`` are the paths the write took off (``temperature``, ``provider.ignore``); one that is still on the
    stored preset was not removed.
    """
    out: list[str] = []
    for key, want in desired.items():
        have = stored.get(key)
        if key == "provider" and isinstance(want, dict) and isinstance(have, dict):
            out += [f"provider.{sub}" for sub, value in want.items() if have.get(sub) != value]
        elif have != want:
            out.append(key)
    provider = stored.get("provider") if isinstance(stored.get("provider"), dict) else {}
    for path in cleared:
        key, _, sub = path.partition(".")
        if (provider.get(sub) if sub else stored.get(key)) is not None:
            out.append(path)
    return out


def digest_of(
    slug: str,
    config: dict[str, Any],
    fingerprint: str | None = None,
    version: int | None = None,
    system_prompt: str | None = None,
) -> str:
    """Names a plan. The account and the live version are in it, so a plan reviewed against one account, or before
    someone made another version, is refused by apply. So is the system prompt that would be sent, when there is one."""
    named: dict[str, Any] = {"slug": slug, "config": config, "account": fingerprint, "version": version}
    if _prompt_text(system_prompt) is not None:
        named["system_prompt"] = system_prompt
    raw = json.dumps(named, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def owner_fingerprint(base_url: str, api_key: str) -> str:
    """Stands for the account a key reaches, for telling one account's preset from another's.

    A hash of the host and the key, cut short: it can say two keys differ without being a way back to either.
    """
    host = urlsplit(base_url).netloc.rpartition("@")[2].lower()
    return hashlib.sha256(f"vibonomics-preset-owner\0{host}\0{api_key}".encode()).hexdigest()[:16]


# ---- state ---------------------------------------------------------------------------------------------------


# Kept per slug, so a write to one preset leaves what another's record says alone.
_PER_SLUG = ("owners", "pins", "applied", "last", "reviewed", "system_backups")
BACKUP_MAX_CHARS = 20_000


def _normalise_state(raw: dict[str, Any]) -> dict[str, Any]:
    """The stored record in today's shape.

    Before several presets, ``applied`` and ``last`` each held the one preset's dict, with its slug inside it. They
    are read as ``{slug: that}``, and the record is rewritten that way by the next write.
    """
    state = dict(raw)
    for key in ("applied", "last"):
        value = state.get(key)
        if isinstance(value, dict) and isinstance(value.get("slug"), str):
            state[key] = {value["slug"]: value} if value["slug"] else {}
    return state


def _load_state(session: Session) -> dict[str, Any]:
    row = session.get(Setting, STATE_KEY)
    return _normalise_state(row.value) if row is not None and row.value else {}


def read_state() -> dict[str, Any]:
    """The local record of what was written; reads no network."""
    with session_scope() as s:
        return _load_state(s)


def _merge_state(
    state: dict[str, Any],
    patch: dict[str, Any],
    own: str | None = None,
    owner: dict[str, Any] | None = None,
    keep_owner: bool = False,
) -> None:
    """Merge ``patch`` into ``state`` in place, slug by slug. ``own`` adds a slug to ``owned`` and ``owner`` is its
    account record; ``keep_owner`` leaves a record that is already there alone."""
    for key, value in patch.items():
        state[key] = {**(state.get(key) or {}), **value} if key in _PER_SLUG else value
    if own and own not in state.get("owned", []):
        state["owned"] = [*state.get("owned", []), own]
    if own and owner is not None and not (keep_owner and own in (state.get("owners") or {})):
        state["owners"] = {**(state.get("owners") or {}), own: owner}


def _store_state(session: Session, state: dict[str, Any]) -> None:
    row = session.get(Setting, STATE_KEY)
    if row is None:
        session.add(Setting(key=STATE_KEY, value=state))
    else:
        row.value = state


def _update_state(
    patch: dict[str, Any], own: str | None = None, owner: dict[str, Any] | None = None, keep_owner: bool = False
) -> None:
    with session_scope() as s:
        state = _load_state(s)
        _merge_state(state, patch, own, owner, keep_owner)
        _store_state(s, state)


def _record_run(slug: str, status: str, message: str, trigger: str, patch: dict[str, Any] | None = None) -> None:
    last = {"at": utcnow().isoformat(), "status": status, "message": message[:500], "trigger": trigger, "slug": slug}
    _update_state({**(patch or {}), "last": {slug: last}})
    events.publish("presets.changed", {"status": status})


def is_owned(state: dict[str, Any], slug: str) -> bool:
    return slug in (state.get("owned") or [])


def is_adopted(state: dict[str, Any], slug: str) -> bool:
    """Whether ``slug`` was handed over by the adopt route: owned, with a record that says so."""
    record = (state.get("owners") or {}).get(slug)
    return is_owned(state, slug) and isinstance(record, dict) and record.get("adopted") is True


def _same_instant(a: str, b: str) -> bool:
    try:
        return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return a == b


def ownership(state: dict[str, Any], slug: str, fingerprint: str, remote: Remote) -> str:
    """Whether the preset the key reaches under ``slug`` is the one Vibonomics created.

    ``ours``; ``legacy`` (recorded as owned before accounts were tracked, so trusted this once); ``unknown`` (never
    ours); ``other_account`` (made under another key or host); or ``replaced`` (the same account, but the live preset
    carries another id or creation time than the one that was created, so it was deleted and made again).
    """
    if not is_owned(state, slug):
        return "unknown"
    record = (state.get("owners") or {}).get(slug)
    if not isinstance(record, dict) or not record.get("fingerprint"):
        return "legacy"
    if record["fingerprint"] != fingerprint:
        return "other_account"
    if record.get("preset_id") and remote.preset_id and str(record["preset_id"]) != remote.preset_id:
        return "replaced"
    if (
        record.get("created_at")
        and remote.created_at
        and not _same_instant(str(record["created_at"]), remote.created_at)
    ):
        return "replaced"
    return "ours"


def _owner_record(
    fingerprint: str, version_at_create: int | None, preset_id: str | None, created_at: str | None
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "version_at_create": version_at_create,
        "preset_id": preset_id,
        "created_at": created_at,
        "recorded_at": utcnow().isoformat(),
    }


def _claim_record(
    plan: Plan, state: dict[str, Any], version: int | None, preset_id: str | None, created_at: str | None
) -> dict[str, Any]:
    """The account record to keep once a write has gone through; an update keeps what the create recorded."""
    if plan.action == "create":
        return _owner_record(plan.fingerprint or "", version, preset_id, created_at)
    old = (state.get("owners") or {}).get(plan.slug)
    if isinstance(old, dict) and old.get("fingerprint") == plan.fingerprint:
        return {
            **old,
            "preset_id": old.get("preset_id") or preset_id,
            "created_at": old.get("created_at") or created_at,
        }
    return _owner_record(plan.fingerprint or "", None, preset_id, created_at)


def _backup_of(plan: Plan) -> dict[str, Any] | None:
    """What to keep of the live system prompt before a write that replaces or removes it; None when none is lost.

    Resending the prompt unchanged loses nothing, so it records nothing and an earlier backup is not overwritten.
    """
    if plan.live_prompt is None or plan.current is None:
        return None
    if _norm_prompt(plan.live_prompt) == _norm_prompt(plan.prompt):
        return None
    return {
        "text": plan.live_prompt[:BACKUP_MAX_CHARS],
        "version": plan.current.version,
        "at": utcnow().isoformat(),
    }


def _stable_id(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """The id and the creation time a preset response carries, if it carries them."""
    raw_id, created = data.get("id"), data.get("created_at")
    return (
        str(raw_id) if isinstance(raw_id, str | int) and not isinstance(raw_id, bool) and str(raw_id) else None,
        created if isinstance(created, str) and created else None,
    )


def _workspace(data: dict[str, Any]) -> str | None:
    """The workspace a preset response names, by slug, then name, then id; None when it names none.

    OpenRouter's docs do not say which key carries it, so each is tried, and a nested object is read for its slug or id.
    """
    for key in ("workspace_slug", "workspace", "workspace_id"):
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("slug") or value.get("id")
        if isinstance(value, str | int) and not isinstance(value, bool) and str(value):
            return str(value)
    return None


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
    designated = data.get("designated_version")
    live = designated if isinstance(designated, dict) else {}
    preset_id, created_at = _stable_id(data)
    remote = Remote(
        slug=slug,
        version=live.get("version"),
        config=live.get("config") if isinstance(live.get("config"), dict) else {},
        system_prompt=live.get("system_prompt"),
        updated_at=data.get("updated_at"),
        status=str(data.get("status") or "active"),
        readable=isinstance(live.get("config"), dict),
        preset_id=preset_id,
        created_at=created_at,
        workspace=_workspace(data),
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


def _access(resolved: list[Any]) -> tuple[str, str | None, str | None]:
    """OpenRouter's own host, the first key, and the row's Base URL override if it has one.

    The override is never used: it is the operator's gateway for chat traffic, and the account key would go to
    whatever host it names. Every call here is OpenRouter's, so it goes to OpenRouter.
    """
    keyed = next((r for r in resolved if r.api_key), None)
    base = OpenRouterAdapter.spec.default_base_url.rstrip("/")
    ignored = keyed.base_url if keyed and keyed.base_url and keyed.base_url.rstrip("/") != base else None
    return base, keyed.api_key if keyed else None, ignored


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
        base, key, _ignored = _access([resolve(p) for p in _openrouter_providers(s)])
        return base, key, _load_state(s)


def load_inputs(slug: str, draft: PresetSettings | None = None) -> Inputs:
    """What a plan for ``slug`` needs from this instance.

    ``draft`` stands in for the saved item, to try settings out; it is the only way to plan a preset that is not saved.
    """
    with session_scope() as s:
        settings = load_settings(s)
        preset = draft if draft is not None else settings.presets.item(slug)
        if preset is None:
            raise PresetNotConfigured(f"{slug} is not one of the presets Vibonomics manages.")
        providers = _openrouter_providers(s)
        resolved = [resolve(p) for p in providers]
        excluded = Excluded()
        candidates, task, profile_id = rank(s, settings, [p.id for p in providers], preset, excluded)
        state = _load_state(s)
    base, key, ignored = _access(resolved)
    return Inputs(
        settings,
        preset,
        candidates,
        task,
        profile_id,
        base,
        key,
        state,
        draft=draft is not None,
        ignored_base=ignored,
        excluded=excluded,
    )


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


async def make_plan(slug: str, inputs: Inputs | None = None, draft: PresetSettings | None = None) -> Plan:
    """What the next update of ``slug`` would do. Reads from OpenRouter, writes nothing.

    ``draft`` previews settings that are not saved. Applying never uses one: it always plans from what is saved.
    """
    inputs = inputs or await asyncio.to_thread(load_inputs, slug, draft)
    cfg = inputs.preset
    plan = Plan(
        slug=cfg.slug,
        action="blocked",
        reason="",
        settings=cfg,
        profile=inputs.profile_id,
        task=inputs.task,
        considered=len(inputs.candidates),
        state=inputs.state,
        draft=inputs.draft,
        excluded=inputs.excluded,
        own_tests_preferred=own_tests_preferred(inputs.settings, cfg),
    )
    blocked_providers = blocklists(inputs.settings, cfg)[1]
    if cfg.adopted and not is_adopted(inputs.state, cfg.slug):
        # `adopted` in the settings is only a claim. A preset made by hand is written only after the owner adopted it
        # through the route that looks at it first, and that left a record.
        return _blocked(
            plan,
            f"{cfg.slug} is marked as adopted, but no adoption of it was recorded, so it is left alone. Adopt it from "
            "the list of presets on the account, which shows what Vibonomics would send back.",
        )
    if not inputs.api_key:
        return _blocked(plan, "Add an OpenRouter API key under Settings, Providers first.")
    plan.fingerprint = owner_fingerprint(inputs.base_url, inputs.api_key)
    if inputs.ignored_base:
        plan.warnings.append(
            f"The OpenRouter provider has a Base URL override ({public_url(inputs.ignored_base)}). Presets are always "
            f"read and written at {public_url(inputs.base_url)}, so the key is never sent to the override."
        )
    try:
        remote = await fetch_remote(inputs.base_url, inputs.api_key, cfg.slug)
    except FetchError as exc:
        return _blocked(plan, f"Could not read the preset from OpenRouter: {exc}")
    plan.current = remote
    plan.live_prompt = _prompt_text(remote.system_prompt) if remote is not None else None
    plan.prompt, plan.prompt_source = prompt_in_force(cfg.guardrails, plan.live_prompt)
    if remote is None and cfg.adopted:
        return _blocked(
            plan,
            f"The adopted preset {cfg.slug} is gone from OpenRouter. Vibonomics does not create a preset under a name "
            "it was only handed. Take it out of the list, or adopt the preset again if it was made anew.",
        )
    if remote is not None and remote.readable:
        plan.passthrough = passthrough_view(remote.config, cfg.guardrails)
    if remote is not None and remote.status == "archived":
        return _blocked(
            plan,
            f"The preset {cfg.slug} is archived on OpenRouter, and an archived preset takes no new versions. "
            "Restore it in the OpenRouter web UI, or give this one another name.",
        )
    if remote is not None:
        owner = ownership(inputs.state, cfg.slug, plan.fingerprint, remote)
        if owner == "unknown":
            return _blocked(
                plan,
                f"A preset called {cfg.slug} already exists on this account and Vibonomics did not create it, so it "
                "is left alone. Give this one another name, or archive that preset in the OpenRouter web UI.",
            )
        if owner == "other_account":
            return _blocked(
                plan,
                f"A preset called {cfg.slug} exists on this account, but Vibonomics created that name under a "
                "different OpenRouter key or account than the one in use now, so it is left alone. Give this one "
                "another name, or archive that preset in the OpenRouter web UI.",
            )
        if owner == "replaced":
            return _blocked(
                plan,
                f"The preset {cfg.slug} is no longer the one Vibonomics created: it has another id or creation time, "
                "so it was deleted and made again. It is left alone. Give this one another name, or archive that "
                "preset in the OpenRouter web UI.",
            )
        if not remote.readable:
            # Planning from {} would send a config with every field the operator set dropped, and diff against {}.
            return _blocked(
                plan,
                f"The preset {cfg.slug} exists, but OpenRouter returned no live version for it, so Vibonomics "
                "could not read what it holds and will not write over it. Try again later.",
            )
        if owner == "legacy":
            # Older state named the slug with no account. Trust it this once, and tie it to the key in use now.
            log.info("Preset %s was recorded as ours before accounts were tracked; adopting it for this key", cfg.slug)
            record = _owner_record(plan.fingerprint, None, remote.preset_id, remote.created_at)
            await asyncio.to_thread(_update_state, {}, cfg.slug, record, True)
    if not inputs.candidates:
        return _blocked(
            plan,
            "No OpenRouter model passes the filters. Loosen the minimum score, the value floor, the cost limit or the "
            "other filters."
            + (" The blocklists took out models that would otherwise pass." if inputs.excluded.models else ""),
        )

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
        if not template.readable:
            return _blocked(
                plan,
                f"The template preset {cfg.template_slug} exists, but OpenRouter returned no live version for it, so "
                "Vibonomics could not read what to start from. Try again later.",
            )
        base_config = template.config
        if template.system_prompt and cfg.guardrails.system_prompt is None:
            # A template lends its settings, not its text: the prompt is the owner's to give a new preset.
            plan.warnings.append(
                "The template has a system prompt, which a new preset does not copy. Set a system prompt in this "
                "preset's guardrails to give it one."
            )

    # What the endpoints are filtered by is the config this plan will write, so a guardrail the owner manages is what
    # applies (and one they took off no longer does), whatever the live preset says.
    in_force = apply_guardrails(base_config or {}, cfg.guardrails)
    provider_block = in_force.get("provider")
    provider_block = provider_block if isinstance(provider_block, dict) else {}
    live_provider = remote.config.get("provider") if remote else None
    live_provider = live_provider if isinstance(live_provider, dict) else {}
    live_tags = [t for key in ("order", "only") for t in live_provider.get(key) or [] if isinstance(t, str)]
    # A template's `only` is the operator's allow-list. Once the preset exists its own `only` is what the last plan
    # wrote, and filtering by that would stop it ever changing.
    allow = (
        [s for s in provider_block.get("only") or [] if isinstance(s, str)]
        if remote is None and cfg.template_slug
        else []
    )
    if allow:
        plan.warnings.append(
            f"The template's provider list (only: {', '.join(allow)}) is applied: only those providers are "
            "considered for this preset."
        )
    per_pick = [
        (
            c.model_id,
            choose_endpoints(
                c.endpoints,
                inputs.task,
                provider_block,
                cfg.max_providers,
                current=live_tags,
                allow=allow,
                blocked=blocked_providers,
            ),
        )
        for c in plan.picks
    ]
    bare = [c.name for c, (_id, eps) in zip(plan.picks, per_pick, strict=True) if not eps]
    if len(bare) == len(plan.picks):
        plan.warnings.append(
            "No provider data for these models (or none that passes the preset's quantization, ignore and provider "
            "list filters), so the preset names no provider and OpenRouter routes as it decides. Provider data comes "
            "from the 'Check OpenRouter endpoint discounts' refresh option."
        )
    elif bare:
        # `only` is shared by every listed model, so one model with no provider in it could never be served.
        plan.warnings.append(
            f"{', '.join(bare)} {'has' if len(bare) == 1 else 'have'} no provider data (or none that passes the "
            "preset's quantization, ignore and provider list filters), and a provider list would keep "
            f"{'it' if len(bare) == 1 else 'them'} from being served, so the preset names no provider and OpenRouter "
            "routes as it decides. Provider data comes from the 'Check OpenRouter endpoint discounts' refresh option."
        )
    else:
        plan.providers = merge_endpoints(per_pick, cfg.max_providers)
        if len(plan.providers) > cfg.max_providers:
            plan.warnings.append(
                f"Each of the {len(plan.picks)} models keeps a provider of its own, so the preset lists "
                f"{len(plan.providers)} providers, more than the limit of {cfg.max_providers}."
            )
    # What the owner manages is handled, not unseen: it is set or taken off here.
    owned = cfg.guardrails.owned_keys()
    raw_block = (base_config or {}).get("provider")
    unseen = [k for k in UNSEEN_FILTERS if isinstance(raw_block, dict) and k in raw_block and k not in owned]
    if unseen:
        plan.warnings.append(
            f"OpenRouter applies {', '.join(unseen)} itself and this ranking cannot see it, so a listed provider "
            "may be filtered out at request time."
        )

    ids = [c.model_id for c in plan.picks]
    plan.config = build_config(
        base_config, ids, [p["tag"] for p in plan.providers], cfg.route, cfg.sort, cfg.guardrails
    )
    plan.passthrough = passthrough_view(plan.config, cfg.guardrails)
    plan.url = f"{inputs.base_url}/presets/{quote(cfg.slug, safe='')}/chat/completions"
    plan.body = write_body(plan.config, plan.prompt)
    plan.digest = digest_of(cfg.slug, plan.config, plan.fingerprint, remote.version if remote else None, plan.prompt)
    plan.changes = diff_config(remote.config if remote else None, plan.config, plan.live_prompt, plan.prompt)
    plan.pinned = ids if plan.providers else []
    # The models whose providers the live preset named when it was last in step with a plan, and that have none now.
    known = (inputs.state.get("pins") or {}).get(cfg.slug) or []
    lost = [c.name for c in plan.picks if c.model_id in known and not c.endpoints]

    if remote is None:
        plan.action = "create"
        plan.reason = (
            f"Creates {cfg.slug} pointing at {pick.name}."
            if len(ids) == 1
            else f"Creates {cfg.slug} with {len(ids)} models, led by {pick.name}."
        )
    elif not _owned_differs(
        plan.config,
        remote.config,
        unkept := _unkept(inputs.state, cfg.slug),
        cfg.guardrails,
        plan.prompt,
        plan.live_prompt,
    ):
        plan.action = "unchanged"
        best = inputs.candidates[0]
        plan.reason = "Already up to date." + (
            f" {best.name} scores higher on value, but by less than {cfg.switch_margin_pct:g}%, so the lineup stays."
            if plan.kept_current
            else ""
        )
        if plan.changes:
            # What still differs is a key OpenRouter did not keep when it was last sent. Sending it again would make a
            # new version on every run for the same result.
            plan.warnings.append(
                f"OpenRouter did not keep {', '.join(sorted(unkept))} the last time it was sent, and the preset has "
                "it no more now, so it is not sent again. Nothing is written until something else changes."
            )
            plan.config = copy.deepcopy(remote.config)
            plan.body = write_body(plan.config, plan.prompt)
            plan.digest = digest_of(cfg.slug, plan.config, plan.fingerprint, remote.version, plan.prompt)
            plan.changes = []
    elif lost and (live_provider.get("only") or live_provider.get("order")):
        # A failed endpoint lookup, or the option switched off, leaves a model with no data for a while. Writing now
        # would unpin the preset, and the next good refresh would pin it again: two versions for nothing.
        names = ", ".join(lost)
        plan.action = "unchanged"
        plan.reason = f"Kept as it is: provider data for {names} is missing right now."
        plan.warnings.append(
            f"{names} had provider data when the preset was last written and has none now (the endpoint lookup may "
            "have failed, or 'Check OpenRouter endpoint discounts' is off), so the preset keeps its current providers "
            "instead of being unpinned. Nothing is written until the data is back."
        )
        plan.config = copy.deepcopy(remote.config)
        plan.body = write_body(plan.config, plan.prompt)
        plan.digest = digest_of(cfg.slug, plan.config, plan.fingerprint, remote.version, plan.prompt)
        plan.changes, plan.pinned = [], None
    elif before != ids:
        plan.action, plan.reason = "update", _model_change(before, plan.picks, inputs.candidates)
    else:
        plan.action = "update"
        paths = {c.path for c in plan.changes}
        mine = {guardrail_path(k) for k in cfg.guardrails.owned_keys()}
        if paths and paths <= {"route", "provider.sort"}:
            what = "route"
        elif paths and paths <= mine:
            what = "system prompt" if paths == {SYSTEM_KEY} else "guardrails"
        else:
            what = "provider list" + (" and guardrails" if paths & mine else "")
        plan.reason = f"Same {'model' if len(ids) == 1 else 'models'}, {pick.name}; the {what} changed."
    if remote is not None and cfg.route == "fallback":
        gone = [k for k in ("route", "provider.sort") if k in {c.path for c in plan.changes}]
        if gone:
            plan.warnings.append(
                f"The preset sets {' and '.join(gone)}, which Vibonomics decides: on the fallback route it takes "
                f"{'it' if len(gone) == 1 else 'them'} off. Choose the sort route to keep sorting."
            )
    # Last, so the check is as fresh as it can be when an apply goes on to write: it reads the live listing, not the
    # stored copy the ranking used. Read-only, so a draft is checked too.
    plan.validation = await validate_live(inputs.base_url, plan.config, plan.picks, plan.providers)
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


class _Unconfirmed(Exception):
    """OpenRouter answered a write with a success status but did not report the version it made."""


# What a write's answer says of the system prompt when it does not say: not the same as saying there is none.
_UNREPORTED: Any = object()


def _confirmed(body: Any, slug: str) -> tuple[dict[str, Any], Any, dict[str, Any], Any]:
    """The preset, its live version, the config and the system prompt OpenRouter stored, from a write's response.

    A 2xx alone proves nothing: an error can ride a 200, and a body with no version means no version was made.
    """
    data = body.get("data") if isinstance(body, dict) else None
    live = data.get("designated_version") if isinstance(data, dict) else None
    if (
        isinstance(data, dict)
        and isinstance(live, dict)
        and live.get("version") is not None
        and isinstance(live.get("config"), dict)
        and data.get("slug", slug) == slug
    ):
        return data, live["version"], live["config"], live.get("system_prompt", _UNREPORTED)
    error = body.get("error") if isinstance(body, dict) else None
    detail = error.get("message") if isinstance(error, dict) else error
    raise _Unconfirmed(f"the answer named no version of {slug}" + (f" ({str(detail)[:200]})" if detail else ""))


async def _settle(
    plan: Plan,
    inputs: Inputs,
    trigger: str,
    version: Any,
    stored: dict[str, Any],
    preset_id: str | None,
    created_at: str | None,
    read_back: bool = False,
    stored_prompt: Any = _UNREPORTED,
) -> Applied:
    """Record a write that went through: the slug is ours from here on, and the run is logged."""
    assert plan.config is not None
    slug = plan.slug
    cleared = [guardrail_path(k) for k in plan.settings.guardrails.clear if k != SYSTEM_KEY]
    unsaved = unsaved_keys(plan.config, stored, cleared)
    unverified = False
    if plan.prompt is not None or plan.live_prompt is not None:
        # Whatever was sent, the prompt the version holds is compared with it: a version that lost it has lost the
        # owner's text, and must not pass for a write that went well.
        seen = stored_prompt if stored_prompt is not _UNREPORTED else await _stored_prompt(inputs, slug)
        if seen is _UNREPORTED:
            unverified = True
        elif _norm_prompt(seen) != _norm_prompt(plan.prompt):
            unsaved.append(SYSTEM_KEY)
    verb = "Created" if plan.action == "create" else "Updated"
    message = f"{verb} {slug} as version {version}: {plan.reason}"
    if read_back:
        message += " OpenRouter's answer did not arrive, so this was read back from the preset."
    if unsaved:
        message += f" OpenRouter did not keep: {', '.join(unsaved)}."
    if unverified:
        message += " It could not be checked whether OpenRouter kept the system prompt."
    applied = {
        "slug": slug,
        "model_id": plan.config.get("model"),
        "version": version,
        "at": datetime.now(UTC).isoformat(),
        # So a later plan does not send again what OpenRouter has shown it will not keep.
        "unsaved": unsaved,
    }
    # The slug becomes ours the moment OpenRouter has created it, even if recording the run then fails.
    owner = _claim_record(plan, inputs.state, version, preset_id, created_at)
    patch: dict[str, Any] = {"applied": {slug: applied}, "pins": {slug: plan.pinned or []}}
    if plan.reviewed:
        patch["reviewed"] = {slug: plan.reviewed}
    await asyncio.to_thread(_update_state, patch, slug, owner)
    await asyncio.to_thread(_record_run, slug, "applied", message, trigger)
    return Applied(version=version, unsaved=unsaved, message=message)


async def _stored_prompt(inputs: Inputs, slug: str) -> Any:
    """The system prompt the live version holds, by a plain read, for a write whose answer did not carry it."""
    assert inputs.api_key is not None
    try:
        seen = await fetch_remote(inputs.base_url, inputs.api_key, slug)
    except FetchError:
        return _UNREPORTED
    return seen.system_prompt if seen is not None and seen.readable else _UNREPORTED


async def _identity(
    inputs: Inputs, slug: str, fallback: tuple[str | None, str | None]
) -> tuple[str | None, str | None]:
    """The id and creation time of a new preset as a plain read reports them, which is what later plans compare.

    The create answer may word them another way. A record that no read can match would block our own preset on
    every plan from then on.
    """
    assert inputs.api_key is not None
    try:
        seen = await fetch_remote(inputs.base_url, inputs.api_key, slug)
    except FetchError:
        return fallback
    if seen is None or not seen.readable:
        return fallback
    return seen.preset_id or fallback[0], seen.created_at or fallback[1]


async def _read_back(plan: Plan, inputs: Inputs, trigger: str) -> Applied | None:
    """After a write whose outcome is unknown, claim it if the preset now holds exactly what the plan sent.

    OpenRouter may have made the version and lost the answer (a timeout, a body that is not JSON, a gateway error).
    Without this the preset exists, is not recorded as ours, and every later plan is blocked.
    """
    assert inputs.api_key is not None
    try:
        remote = await fetch_remote(inputs.base_url, inputs.api_key, plan.slug)
    except FetchError:
        return None
    if (
        remote is None
        or not remote.readable
        or remote.config != plan.config
        or _norm_prompt(remote.system_prompt) != _norm_prompt(plan.prompt)
    ):
        return None
    return await _settle(
        plan,
        inputs,
        trigger,
        remote.version,
        remote.config,
        remote.preset_id,
        remote.created_at,
        True,
        remote.system_prompt,
    )


async def apply(slug: str, digest: str | None, trigger: str) -> tuple[Plan, Applied | None]:
    """Write the plan for ``slug`` if there is something to write.

    ``digest`` pins it to a plan the operator reviewed. The plan is made from the saved item, never from a draft, and
    one lock covers every preset, so two writes never overlap. A create or update whose plan has an error in its live
    check raises ``PresetInvalid`` before anything is sent; a preset that is already up to date is not refused.
    """
    async with _lock:
        inputs = await asyncio.to_thread(load_inputs, slug)
        cfg = inputs.preset
        if not cfg.enabled:
            raise PresetDisabled("Turn on 'Let Vibonomics write the preset' first.")
        plan = await make_plan(slug, inputs)
        if digest is not None and plan.digest != digest:
            raise PlanChanged("The plan changed since you looked at it. Preview it again.")
        if is_adopted(inputs.state, cfg.slug) and plan.current is not None and plan.current.readable:
            seen = passthrough_digest(plan.current.config, cfg.guardrails, plan.current.system_prompt)
            if trigger == "manual":
                if plan.action in ("create", "update", "unchanged"):
                    plan.reviewed = seen  # the owner has just been shown the plan, the keys it sends back included
            elif plan.action in ("create", "update") and seen != (inputs.state.get("reviewed") or {}).get(cfg.slug):
                # No one is watching, and the keys the owner agreed to hand over are no longer the keys there.
                _blocked(
                    plan,
                    "Keys changed on OpenRouter since you reviewed them, so nothing was written. Preview the "
                    "preset and apply it yourself to accept them.",
                )
                await asyncio.to_thread(_record_run, cfg.slug, "blocked", plan.reason, trigger)
                return plan, None
        if plan.action not in ("create", "update") or plan.body is None or plan.url is None or plan.config is None:
            # An unchanged preset is in step with this plan, so what it pins is known now.
            known: dict[str, Any] = {"pins": {cfg.slug: plan.pinned}} if plan.pinned is not None else {}
            if plan.reviewed:
                known["reviewed"] = {cfg.slug: plan.reviewed}
            await asyncio.to_thread(_record_run, cfg.slug, plan.action, plan.reason, trigger, known or None)
            return plan, None
        if plan.validation is not None and not plan.validation.ok:
            # make_plan, a moment ago and from this same call, read the live listing again, so this is the double
            # check at the time of writing and not the preview's answer, which can be hours old. Blocked, not failed:
            # OpenRouter took no write and refused none.
            invalid = PresetInvalid([c.message for c in plan.validation.items if c.level == "error"])
            await asyncio.to_thread(_record_run, cfg.slug, "blocked", str(invalid), trigger)
            raise invalid
        assert inputs.api_key is not None
        if (backup := _backup_of(plan)) is not None:
            # The write replaces the live system prompt, and the version it held stays only in OpenRouter's history,
            # which no call here can read it from. Kept first, so the owner can get the text back whatever follows.
            await asyncio.to_thread(_update_state, {"system_backups": {cfg.slug: backup}})
        headers = {**_auth(inputs.api_key), "Content-Type": "application/json"}
        try:
            resp = await request(
                "POST",
                plan.url,
                headers=headers,
                json_body=plan.body,
                timeout=30,
                retries=0,  # a retried create would leave a second version behind
            )
            data, version, stored, stored_prompt = _confirmed(resp.json(), cfg.slug)
        except asyncio.CancelledError:
            # Whoever cancelled has stopped waiting, but OpenRouter may have taken the write. Claim it if so, or the
            # preset it made would be blocked as someone else's.
            try:
                await asyncio.wait_for(_read_back(plan, inputs, trigger), 15)
            except Exception:  # best effort: the cancellation goes on whatever happens here
                log.warning("Could not check whether the cancelled preset write for %s went through", cfg.slug)
            raise
        except (FetchError, ValueError, _Unconfirmed) as exc:
            # A refusal (4xx) is final. Anything else may have been written with the answer lost on the way back.
            final = isinstance(exc, FetchError) and exc.status is not None and exc.status < 500
            if not final and (recovered := await _read_back(plan, inputs, trigger)) is not None:
                return plan, recovered
            message = scrub_secrets(f"OpenRouter did not take the update: {exc}", headers)
            await asyncio.to_thread(_record_run, cfg.slug, "failed", message, trigger)
            raise PresetWriteError(message) from exc
        ident = _stable_id(data)
        if plan.action == "create":
            ident = await _identity(inputs, cfg.slug, ident)
        return plan, await _settle(plan, inputs, trigger, version, stored, *ident, stored_prompt=stored_prompt)


async def sync(slug: str, trigger: str = "schedule") -> None:
    """The scheduled run for one preset: apply, and keep going whatever happens."""
    try:
        plan, applied = await apply(slug, None, trigger)
    except (PresetDisabled, PresetNotConfigured):
        return
    except PresetWriteError as exc:
        log.warning("Preset %s update failed: %s", slug, exc)
        return
    except PresetInvalid as exc:
        log.warning("Preset %s was not written: %s", slug, exc)
        return
    except Exception as exc:  # the scheduler must survive any single failure
        log.exception("Preset sync of %s failed", slug)
        await asyncio.to_thread(_record_run, slug, "failed", f"{type(exc).__name__}: {failure_text(exc)}", trigger)
        return
    log.info("Preset %s: %s", plan.slug, applied.message if applied else plan.reason)


# ---- looking at a preset made by hand, and adopting it ---------------------------------------------------------


@dataclass
class Inspection:
    """A preset on the account as it stands, with whether Vibonomics could take it over and why not."""

    remote: Remote
    managed: bool
    adoptable: bool
    blockers: list[str]
    digest: str
    passthrough: dict[str, Any]


def inspection_digest(remote: Remote) -> str:
    """Names the preset as shown: its slug, its live version, its config and its system prompt. Adopt refuses if any
    has moved."""
    return digest_of(remote.slug, remote.config, None, remote.version, remote.system_prompt)


def _managed_slugs() -> list[str]:
    with session_scope() as s:
        return [item.slug for item in load_settings(s).presets.items]


def _blockers(slug: str, remote: Remote, state: dict[str, Any], managed_slugs: list[str]) -> list[str]:
    """Why ``remote`` cannot be adopted; empty when it can."""
    out: list[str] = []
    try:
        adopted_slug(slug)
    except ValueError as exc:
        out.append(f"Its name cannot be kept as it is: {exc}.")
    if remote.status == "archived":
        out.append("The preset is archived on OpenRouter, and an archived preset takes no new versions.")
    if not remote.readable:
        out.append("OpenRouter returned no live version for it, so there is nothing to read what it holds from.")
    if is_owned(state, slug) or slug in managed_slugs:
        out.append("Vibonomics already manages a preset with this name.")
    elif len(managed_slugs) >= PRESET_MAX_ITEMS:
        out.append(f"Vibonomics already manages {PRESET_MAX_ITEMS} presets, the most it will.")
    return out


async def _read_for_adoption(slug: str) -> tuple[str, Remote, dict[str, Any], list[str]]:
    base, key, state = await asyncio.to_thread(load_access)
    if not key:
        raise NoApiKey("Add an OpenRouter API key under Settings, Providers first.")
    remote = await fetch_remote(base, key, slug)
    if remote is None:
        raise PresetNotFound(f"OpenRouter has no preset called {slug}.")
    return owner_fingerprint(base, key), remote, state, await asyncio.to_thread(_managed_slugs)


async def inspect(slug: str) -> Inspection:
    """Read any preset on the account, managed or not, and say whether it could be adopted. Writes nothing."""
    _fingerprint, remote, state, managed_slugs = await _read_for_adoption(slug)
    blockers = _blockers(slug, remote, state, managed_slugs)
    return Inspection(
        remote=remote,
        managed=is_owned(state, slug) or slug in managed_slugs,
        adoptable=not blockers,
        blockers=blockers,
        digest=inspection_digest(remote),
        passthrough=passthrough_view(remote.config) if remote.readable else {},
    )


def _record_adoption(slug: str, fingerprint: str, remote: Remote) -> PresetSettings:
    """In one transaction: note the adoption in the state, and add the preset to the list, switched off."""
    with session_scope() as s:
        settings = load_settings(s)
        state = _load_state(s)
        # Looked at again here, under the transaction: the checks before it were made on a read a moment ago.
        refused = _blockers(slug, remote, state, [item.slug for item in settings.presets.items])
        if refused:
            raise AdoptRefused(refused[0])
        item = PresetSettings(slug=slug, adopted=True)
        settings.presets.items.append(item)
        save_settings(s, settings)
        record = {
            "fingerprint": fingerprint,
            "adopted": True,
            "adopted_at": utcnow().isoformat(),
            "preset_id": remote.preset_id,
            "created_at": remote.created_at,
        }
        patch = {"reviewed": {slug: passthrough_digest(remote.config, item.guardrails, remote.system_prompt)}}
        _merge_state(state, patch, slug, record)
        _store_state(s, state)
        return item


async def adopt(slug: str, digest: str) -> PresetSettings:
    """Hand a preset made by hand over to Vibonomics, if it is still what the operator was shown.

    This writes nothing to OpenRouter: it records that the operator agreed, and the keys they saw (``reviewed``), and
    adds the preset to the list switched off. The first write is a later, separate step.
    """
    async with _lock:
        fingerprint, remote, state, managed_slugs = await _read_for_adoption(slug)
        if inspection_digest(remote) != digest:
            raise PlanChanged("The preset changed since you looked at it. Inspect it again.")
        refused = _blockers(slug, remote, state, managed_slugs)
        if refused:
            raise AdoptRefused(refused[0])
        return await asyncio.to_thread(_record_adoption, slug, fingerprint, remote)


def release_adopted(kept: Iterable[str]) -> list[str]:
    """End the adoption of any adopted preset that is no longer in the list ``kept``, and return their slugs.

    Nothing is changed on OpenRouter. The preset is simply no longer Vibonomics's to write, until it is adopted again.
    """
    keep = set(kept)
    with session_scope() as s:
        state = _load_state(s)
        gone = [
            slug
            for slug, record in (state.get("owners") or {}).items()
            if isinstance(record, dict) and record.get("adopted") is True and slug not in keep
        ]
        if not gone:
            return []
        state["owned"] = [slug for slug in state.get("owned", []) if slug not in gone]
        for key in _PER_SLUG:
            if isinstance(state.get(key), dict):
                state[key] = {slug: value for slug, value in state[key].items() if slug not in gone}
        _store_state(s, state)
    events.publish("presets.changed", {"status": "released"})
    return gone
