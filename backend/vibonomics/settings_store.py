"""Operator-editable settings, stored as one JSON document in the ``settings`` table."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import crypto
from .orm import Setting

_KEY = "app"


class RefreshSettings(BaseModel):
    models_interval_hours: float = Field(6, ge=0, description="0 disables scheduled model/price refresh")
    balances_interval_minutes: float = Field(30, ge=0)
    benchmarks_interval_hours: float = Field(24, ge=0)
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
    input_weight: float = Field(ge=0)
    output_weight: float = Field(ge=0)
    cache_hit_rate: float = Field(0, ge=0, le=1)
    task_input_tokens: int = Field(ge=0)
    task_output_tokens: int = Field(ge=0)


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
    intelligence_weight: float = Field(0.3, ge=0)
    coding_weight: float = Field(0.35, ge=0)
    agentic_weight: float = Field(0.35, ge=0)
    own_tests_weight: float = Field(
        1.0, ge=0, description="Weight of Vibonomics's own test results vs third-party data"
    )
    free_model_cost: float = Field(
        0.01,
        ge=0,
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


class AppSettings(BaseModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    refresh: RefreshSettings = Field(default_factory=RefreshSettings)
    highlight: HighlightSettings = Field(default_factory=HighlightSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    benchmarks: BenchmarkSettings = Field(default_factory=BenchmarkSettings)
    evals: EvalSettings = Field(default_factory=EvalSettings)
    subscriptions: SubscriptionSettings = Field(default_factory=SubscriptionSettings)

    def benchmark_source(self, source_id: str, default_enabled: bool) -> BenchmarkSourceSettings:
        existing = self.benchmarks.sources.get(source_id)
        if existing is None:
            return BenchmarkSourceSettings(enabled=default_enabled)
        return existing

    def benchmark_key(self, source_id: str) -> str | None:
        src = self.benchmarks.sources.get(source_id)
        return crypto.decrypt(src.api_key_enc) if src else None


def load_settings(session: Session) -> AppSettings:
    row = session.get(Setting, _KEY)
    if row is None or not row.value:
        return AppSettings()
    return AppSettings.model_validate(row.value)


def save_settings(session: Session, settings: AppSettings) -> None:
    row = session.get(Setting, _KEY)
    data = settings.model_dump(mode="json")
    if row is None:
        session.add(Setting(key=_KEY, value=data))
    else:
        row.value = data
