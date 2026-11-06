"""Operator-editable settings, stored as one JSON document in the ``settings`` table."""

from __future__ import annotations

import copy
import logging
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from . import crypto
from .orm import Setting

log = logging.getLogger(__name__)

_KEY = "app"
# One year: far past any useful schedule, and well inside what a timer can hold (1e12 hours overflows it).
_YEAR_HOURS = 24 * 365
_YEAR_MINUTES = _YEAR_HOURS * 60
# Weights only matter relative to each other, so this is generous.
_MAX_WEIGHT = 1000


class RefreshSettings(BaseModel):
    models_interval_hours: float = Field(
        6, ge=0, le=_YEAR_HOURS, allow_inf_nan=False, description="0 disables scheduled model/price refresh"
    )
    balances_interval_minutes: float = Field(30, ge=0, le=_YEAR_MINUTES, allow_inf_nan=False)
    benchmarks_interval_hours: float = Field(24, ge=0, le=_YEAR_HOURS, allow_inf_nan=False)
    usage_interval_hours: float = Field(
        12,
        ge=0,
        le=_YEAR_HOURS,
        allow_inf_nan=False,
        description="Spend history from OpenRouter; needs a management key",
    )
    refresh_on_startup: bool = True
    openrouter_endpoint_discounts: bool = Field(
        True, description="Query OpenRouter's per-provider endpoints to detect discounts (one request per model)"
    )


class HighlightSettings(BaseModel):
    new_model_days: int = Field(7, ge=0, le=365)
    new_discount_days: int = Field(7, ge=0, le=365)


class UsageProfile(BaseModel):
    """How input/output/cache prices are folded into one blended $/1M figure and an estimated task cost."""

    id: str
    name: str
    input_weight: float = Field(ge=0, le=_MAX_WEIGHT, allow_inf_nan=False)
    output_weight: float = Field(ge=0, le=_MAX_WEIGHT, allow_inf_nan=False)
    cache_hit_rate: float = Field(0, ge=0, le=1)
    task_input_tokens: int = Field(ge=0)
    task_output_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _not_empty(self) -> UsageProfile:
        # With no tokens every task costs nothing, so every value is undefined and every score ranks first.
        if self.task_input_tokens + self.task_output_tokens == 0:
            raise ValueError("a task needs some input or output tokens")
        return self


DEFAULT_PROFILES = [
    UsageProfile(
        id="agentic",
        name="Agentic coding",
        input_weight=20,
        output_weight=1,
        cache_hit_rate=0.8,
        task_input_tokens=600_000,
        task_output_tokens=20_000,
    ),
    UsageProfile(
        id="balanced",
        name="Balanced 3:1",
        input_weight=3,
        output_weight=1,
        cache_hit_rate=0,
        task_input_tokens=30_000,
        task_output_tokens=10_000,
    ),
    UsageProfile(
        id="output",
        name="Output heavy 1:2",
        input_weight=1,
        output_weight=2,
        cache_hit_rate=0,
        task_input_tokens=10_000,
        task_output_tokens=20_000,
    ),
]


class ScoringSettings(BaseModel):
    intelligence_weight: float = Field(0.3, ge=0, le=_MAX_WEIGHT, allow_inf_nan=False)
    coding_weight: float = Field(0.35, ge=0, le=_MAX_WEIGHT, allow_inf_nan=False)
    agentic_weight: float = Field(0.35, ge=0, le=_MAX_WEIGHT, allow_inf_nan=False)
    own_tests_weight: float = Field(
        1.0,
        ge=0,
        le=_MAX_WEIGHT,
        allow_inf_nan=False,
        description="Weight of Vibonomics's own test results vs third-party data. Has no effect on a model whose own "
        "test score is preferred (see prefer_own_tests)",
    )
    prefer_own_tests: bool = Field(
        False,
        description="When a model has enough graded own-test results, its own test score is its coding and agentic "
        "score instead of being blended with third-party percentiles",
    )
    prefer_own_tests_min_tasks: int = Field(
        5,
        ge=1,
        le=200,
        description="The fewest graded task results a model must have, summed over providers, before its own test "
        "score replaces the computed one; a model tested on a handful of tasks is too noisy to override a benchmark",
    )
    free_model_cost: float = Field(
        0.01,
        ge=0,
        le=_MAX_WEIGHT,
        allow_inf_nan=False,
        description="Assumed blended USD per 1M tokens for free models when ranking value (they would otherwise "
        "have infinite value)",
    )
    profiles: list[UsageProfile] = Field(default_factory=lambda: [p.model_copy() for p in DEFAULT_PROFILES])
    default_profile: str = "agentic"


class BenchmarkSourceSettings(BaseModel):
    enabled: bool = False
    api_key_enc: str | None = None


class BenchmarkSettings(BaseModel):
    sources: dict[str, BenchmarkSourceSettings] = Field(default_factory=dict)
    aa_terms_acknowledged: bool = False


class EvalSettings(BaseModel):
    judge_provider_config_id: int | None = None
    judge_model_id: str | None = None
    max_steps: int = Field(14, ge=2, le=60)
    max_output_tokens: int = Field(4096, ge=256, le=65536)
    concurrency: int = Field(2, ge=1, le=8)
    request_timeout_seconds: int = Field(180, ge=10, le=1800)
    sandbox_mode: Literal["auto", "sidecar", "local", "disabled"] = Field(
        "auto",
        description="auto = sidecar when VIBONOMICS_SANDBOX_URL is set, otherwise disabled",
    )


class SubscriptionSettings(BaseModel):
    claude_ingest_token_enc: str | None = None


class GeneralSettings(BaseModel):
    instance_name: str = "Vibonomics"
    hide_inactive_models: bool = True


PRESET_SLUG_PREFIX = "vibonomics-"
PRESET_SLUG_MAX = 64
# OpenRouter answers HTTP 400 "A preset can have at most 3 models" to a longer list.
PRESET_MAX_MODELS = 3
PRESET_MIN_INTERVAL_HOURS = 0.5
PRESET_MAX_ITEMS = 20
PRESET_ADOPTED_SLUG_MAX = 100
BLOCK_ENTRY_MAX = 100
BLOCK_LIST_MAX = 200
_TEMPLATE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# A blocklist entry is an fnmatch pattern, matched case-insensitively: `openai/*`, `*:free`, `deepinfra`.
BlockEntry = Annotated[str, StringConstraints(strip_whitespace=True, max_length=BLOCK_ENTRY_MAX)]


def preset_slug(name: str) -> str:
    """The OpenRouter slug for a name the operator typed: lower case, hyphenated, and always ``vibonomics-`` first.

    The prefix is how Vibonomics knows a preset is one it may write, so the operator does not type it and cannot
    leave it off. A name that already starts with it is not given a second one.
    """
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not base:
        raise ValueError("Give the preset a name")
    slug = base if base.startswith(PRESET_SLUG_PREFIX) else PRESET_SLUG_PREFIX + base
    if len(slug) > PRESET_SLUG_MAX:
        raise ValueError(
            f"The name is too long: the slug, with {PRESET_SLUG_PREFIX}, is at most {PRESET_SLUG_MAX} long"
        )
    return slug


def adopted_slug(value: str) -> str:
    """The slug of a preset the operator made by hand and chose to hand over, kept exactly as OpenRouter has it."""
    slug = value.strip()
    if not _TEMPLATE_SLUG.match(slug):
        raise ValueError("A preset slug has letters, digits, dots, hyphens and underscores only")
    if len(slug) > PRESET_ADOPTED_SLUG_MAX:
        raise ValueError(f"A preset slug is at most {PRESET_ADOPTED_SLUG_MAX} long")
    return slug


def _clean_patterns(value: list[str]) -> list[str]:
    """Blank entries go, and so does a repeat that differs only in case."""
    seen: dict[str, str] = {}
    for entry in value:
        if entry:
            seen.setdefault(entry.lower(), entry)
    return list(seen.values())


class PresetSettings(BaseModel):
    """One OpenRouter preset Vibonomics keeps pointed at the best-value models, and how it picks them.

    Off by default: nothing is written to the OpenRouter account until ``enabled`` is set. The slug always starts
    with ``vibonomics-`` (it is added to whatever name is typed), so a preset the operator made by hand can never be
    the one that is rewritten, unless the operator adopted it: ``adopted`` keeps the slug as typed, and grants
    nothing by itself, because the engine also wants the adoption to be on record.
    """

    enabled: bool = Field(False, description="Let Vibonomics create and update the preset below on OpenRouter")
    auto_apply: bool = Field(False, description="Also update the preset on a schedule, with no one watching")
    interval_hours: float = Field(
        24,
        ge=PRESET_MIN_INTERVAL_HOURS,
        le=168,
        description="Hours between scheduled updates; 0.5 is every thirty minutes",
    )
    # Before `slug`, which reads it: a field validator only sees the fields declared above it.
    adopted: bool = Field(
        False,
        description="A preset made by hand that Vibonomics now manages. Its name is kept as it is, and it counts "
        "only if the adoption was recorded when it was adopted.",
    )
    slug: str = Field(
        f"{PRESET_SLUG_PREFIX}best-value",
        description=f"The preset's name. {PRESET_SLUG_PREFIX} is put in front of it if it is not already there.",
    )
    template_slug: str | None = Field(
        None,
        max_length=100,
        description="An existing preset whose other settings (tools, reasoning, provider filters) the new preset "
        "starts from. Read, never changed.",
    )
    profile: str | None = Field(None, description="Usage profile that sets the task the models are costed for")
    dimension: Literal["composite", "intelligence", "coding", "agentic"] = "composite"
    min_score: float = Field(60, ge=0, le=100)
    require_tool_call: bool = True
    min_context: int = Field(0, ge=0, description="Smallest context window, in tokens; 0 for any")
    max_task_cost: float | None = Field(
        None,
        ge=0,
        description="Most a model may cost for one task of the chosen usage profile, in USD; none for no limit",
    )
    min_value: float | None = Field(
        None,
        ge=0,
        description="Least value a model may give, in score points per dollar of one task; none for no limit",
    )
    include_free: bool = False
    switch_margin_pct: float = Field(
        10,
        ge=0,
        le=100,
        description="A new model replaces one already in the preset only when it gives this much more value",
    )
    max_models: int = Field(
        1,
        ge=1,
        le=PRESET_MAX_MODELS,
        description=f"Models listed, best value first; the rest are fallbacks. OpenRouter allows at most "
        f"{PRESET_MAX_MODELS} in a preset.",
    )
    max_providers: int = Field(
        3,
        ge=1,
        le=10,
        description="Providers listed, cheapest first, shared across the models. Every model that has provider data "
        "keeps at least one of its own, so the list can run past this when there are more models than providers.",
    )

    blocked_models: list[BlockEntry] = Field(
        default_factory=list,
        max_length=BLOCK_LIST_MAX,
        description="Models this preset never considers, as patterns: `openai/*`, `*:free`, an exact id. Added to "
        "the list that applies to every preset.",
    )
    blocked_providers: list[BlockEntry] = Field(
        default_factory=list,
        max_length=BLOCK_LIST_MAX,
        description="Providers this preset never pins, as patterns matched against the endpoint tag, its provider "
        "part and the display name. Added to the list that applies to every preset.",
    )
    route: Literal["fallback", "sort"] = Field(
        "fallback",
        description="`fallback` writes no `route` and takes the models in order; `sort` writes `route: sort` and "
        "`provider.sort`. OpenRouter does not document `route`, so the page reports it when it is not kept.",
    )
    sort: Literal["price", "throughput", "latency"] | None = Field(
        None, description="What `provider.sort` is when `route` is `sort`; price when not set"
    )

    @field_validator("slug")
    @classmethod
    def _own_slug(cls, value: str, info: ValidationInfo) -> str:
        return adopted_slug(value) if info.data.get("adopted") else preset_slug(value)

    @field_validator("blocked_models", "blocked_providers")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        return _clean_patterns(value)

    @field_validator("template_slug")
    @classmethod
    def _template(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not _TEMPLATE_SLUG.match(value):
            raise ValueError("A preset slug has letters, digits, dots, hyphens and underscores only")
        return value


class PresetsSettings(BaseModel):
    """Every OpenRouter preset Vibonomics manages, and the blocklists that apply to all of them."""

    items: list[PresetSettings] = Field(default_factory=list, max_length=PRESET_MAX_ITEMS)
    blocked_models: list[BlockEntry] = Field(
        default_factory=list,
        max_length=BLOCK_LIST_MAX,
        description="Models no preset considers, as patterns: `openai/*`, `*:free`, an exact id",
    )
    blocked_providers: list[BlockEntry] = Field(
        default_factory=list,
        max_length=BLOCK_LIST_MAX,
        description="Providers no preset pins, as patterns matched against the endpoint tag, its provider part and "
        "the display name",
    )

    @field_validator("blocked_models", "blocked_providers")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        return _clean_patterns(value)

    @model_validator(mode="after")
    def _unique_slugs(self) -> PresetsSettings:
        slugs = [item.slug for item in self.items]
        repeated = sorted({s for s in slugs if slugs.count(s) > 1})
        if repeated:
            raise ValueError(f"Two presets share the slug {repeated[0]}")
        return self

    def item(self, slug: str) -> PresetSettings | None:
        return next((i for i in self.items if i.slug == slug), None)


class AppSettings(BaseModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    refresh: RefreshSettings = Field(default_factory=RefreshSettings)
    highlight: HighlightSettings = Field(default_factory=HighlightSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    benchmarks: BenchmarkSettings = Field(default_factory=BenchmarkSettings)
    evals: EvalSettings = Field(default_factory=EvalSettings)
    subscriptions: SubscriptionSettings = Field(default_factory=SubscriptionSettings)
    presets: PresetsSettings = Field(default_factory=PresetsSettings)

    def benchmark_source(self, source_id: str, default_enabled: bool) -> BenchmarkSourceSettings:
        existing = self.benchmarks.sources.get(source_id)
        if existing is None:
            return BenchmarkSourceSettings(enabled=default_enabled)
        return existing

    def benchmark_key(self, source_id: str) -> str | None:
        src = self.benchmarks.sources.get(source_id)
        return crypto.decrypt(src.api_key_enc) if src else None


# What names a preset: an item whose slug or adoption is invalid cannot be kept with a default in its place.
_ITEM_IDENTITY = ("slug", "adopted")


def _drop(data: Any, loc: tuple[int | str, ...]) -> bool:
    """Remove what ``loc`` points at, so its field falls back to the default, or the whole list item it sits in.

    A preset keeps its other fields when one of them is bad: the item is a whole configuration, and losing it for a
    single value would be a bigger reset than the value deserves.
    """
    node = data
    for i, part in enumerate(loc):
        if isinstance(node, list) and isinstance(part, int) and part < len(node):
            rest = loc[i + 1 :]
            if loc[:2] == ("presets", "items") and i == 2 and rest and rest[0] not in _ITEM_IDENTITY:
                return _drop(node[part], rest)
            del node[part]
            return True
        if not isinstance(node, dict) or part not in node:
            return False
        if i == len(loc) - 1:
            del node[part]
            return True
        node = node[part]
    return False


# What a stored document held before several presets: the section was one preset's own fields.
_SINGLE_PRESET_FIELDS = frozenset(PresetSettings.model_fields) - {"blocked_models", "blocked_providers", "adopted"}


def _slug_of(item: Any) -> str | None:
    """The slug an item will have once validated, or None when it has none worth comparing."""
    if not isinstance(item, dict):
        return None
    raw = item.get("slug", PresetSettings.model_fields["slug"].default)
    if not isinstance(raw, str):
        return None
    try:
        return adopted_slug(raw) if item.get("adopted") is True else preset_slug(raw)
    except ValueError:
        return None


def _untouched(old: dict[str, Any]) -> bool:
    """Whether a single stored preset is only what a new instance starts with, so it is not worth keeping."""
    defaults = PresetSettings().model_dump(mode="json")
    return all(old.get(name, default) == default for name, default in defaults.items() if name in _SINGLE_PRESET_FIELDS)


def _migrate_presets(data: Any) -> None:
    """Bring the ``presets`` section to the shape with a list of items, without resetting any of it.

    A document written for one preset becomes a list of that one, unless it never left the defaults. A list that
    this release would refuse as a whole (a repeated slug, more than the limit) is cut down instead, because a
    failure in the section would reset every preset in it.
    """
    section = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return
    if "items" not in section and _SINGLE_PRESET_FIELDS & section.keys():
        log.warning("Stored presets are in the single-preset shape; moving them into the list of presets")
        section = data["presets"] = {"items": [] if _untouched(section) else [section]}
    items = section.get("items")
    if not isinstance(items, list):
        return
    kept: list[Any] = []
    seen: set[str] = set()
    for item in items:
        slug = _slug_of(item)
        if slug is not None and slug in seen:
            log.error("Stored presets name %s twice; keeping the first", slug)
            continue
        if slug is not None:
            seen.add(slug)
        kept.append(item)
    if len(kept) > PRESET_MAX_ITEMS:
        log.error("Stored presets hold %s items; keeping the first %s", len(kept), PRESET_MAX_ITEMS)
        kept = kept[:PRESET_MAX_ITEMS]
    section["items"] = kept


def _clamp_preset_models(data: Any) -> None:
    """A lineup saved when the limit was ten becomes three, not the default of one a rejected value would get."""
    section = data.get("presets") if isinstance(data, dict) else None
    items = section.get("items") if isinstance(section, dict) else None
    for item in items if isinstance(items, list) else []:
        listed = item.get("max_models") if isinstance(item, dict) else None
        if isinstance(listed, int) and not isinstance(listed, bool) and listed > PRESET_MAX_MODELS:
            log.warning("Stored max_models is %s; OpenRouter allows %s, using that", listed, PRESET_MAX_MODELS)
            item["max_models"] = PRESET_MAX_MODELS


def _validate_stored(raw: Any) -> AppSettings:
    """The stored settings, minus any value that this release no longer accepts.

    Validation gets stricter between releases, and a value saved under the old rules must not stop the app from
    starting, nor take the keys and token stored beside it down with it. Each one is logged and reset to its default.
    """
    data = copy.deepcopy(raw)
    _migrate_presets(data)
    _clamp_preset_models(data)
    for _ in range(100):
        try:
            return AppSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(part) for part in error["loc"])
            log.error("Stored setting %s is no longer valid (%s); using its default", where or "(all)", error["msg"])
            if not _drop(data, error["loc"]):
                break
    return AppSettings()


def load_settings(session: Session) -> AppSettings:
    row = session.get(Setting, _KEY)
    if row is None or not row.value:
        return AppSettings()
    return _validate_stored(row.value)


def save_settings(session: Session, settings: AppSettings) -> None:
    row = session.get(Setting, _KEY)
    data = settings.model_dump(mode="json")
    if row is None:
        session.add(Setting(key=_KEY, value=data))
    else:
        row.value = data
