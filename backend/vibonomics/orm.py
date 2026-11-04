"""Database tables (SQLAlchemy 2.x declarative)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC in SQLite and always hands back timezone-aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    mgmt_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_refresh_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_refresh_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_refresh_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    models: Mapped[list[Model]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class Model(Base):
    """One model as offered by one configured provider (a provider-specific price point)."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_config_id", "model_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_config_id: Mapped[int] = mapped_column(ForeignKey("provider_configs.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(300))
    creator: Mapped[str | None] = mapped_column(String(100), nullable=True)
    canonical_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    family: Mapped[str | None] = mapped_column(String(100), nullable=True)
    context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_modalities: Mapped[list[Any]] = mapped_column(JSON, default=list)
    output_modalities: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reasoning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tool_call: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    open_weights: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Prices in USD per 1M tokens, after any discount.
    price_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_output: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_cache_read: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_cache_write: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Pre-discount list prices, only set while a discount applies.
    list_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    list_output: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    discount_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    price_drop_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)
    pricing_source: Mapped[str] = mapped_column(String(100), default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # True for models present in the provider's first refresh, so they are not flagged as new.
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    provider: Mapped[ProviderConfig] = relationship(back_populates="models")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    price_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_output: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_cache_read: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount: Mapped[float | None] = mapped_column(Float, nullable=True)


class BenchmarkScore(Base):
    __tablename__ = "benchmark_scores"
    __table_args__ = (UniqueConstraint("source", "metric", "source_model", "effort"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    metric: Mapped[str] = mapped_column(String(100))
    dimension: Mapped[str] = mapped_column(String(20))  # intelligence | coding | agentic
    source_model: Mapped[str] = mapped_column(String(300))
    effort: Mapped[str] = mapped_column(String(30), default="")
    canonical_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    match_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    value: Mapped[float] = mapped_column(Float)
    higher_is_better: Mapped[bool] = mapped_column(Boolean, default=True)
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_task: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_per_task: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModelAlias(Base):
    """Operator overrides mapping a benchmark source's model name to a canonical model id."""

    __tablename__ = "model_aliases"
    __table_args__ = (UniqueConstraint("source", "source_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    source_model: Mapped[str] = mapped_column(String(300))
    canonical_id: Mapped[str | None] = mapped_column(String(300), nullable=True)  # None = ignore


class Balance(Base):
    __tablename__ = "balances"

    provider_config_id: Mapped[int] = mapped_column(
        ForeignKey("provider_configs.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20))  # ok | unsupported | needs_key | error
    account_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    account_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    account_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SubscriptionSnapshot(Base):
    """Latest usage snapshot pushed by a client (for example Claude Code's status line)."""

    __tablename__ = "subscription_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class UsageDay(Base):
    """One day of an OpenRouter account's spend on one model through one upstream provider.

    OpenRouter's activity API reaches back 30 days, so each day it reports is kept here and the history outgrows
    that window. ``key_hash`` is empty for the whole account, and an API key's hash for the rows of that key alone:
    sum one kind or the other, never both.
    """

    __tablename__ = "usage_days"
    __table_args__ = (
        UniqueConstraint("provider_config_id", "key_hash", "date", "model", "upstream"),
        Index("ix_usage_days_scope", "provider_config_id", "key_hash", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_config_id: Mapped[int] = mapped_column(ForeignKey("provider_configs.id", ondelete="CASCADE"))
    key_hash: Mapped[str] = mapped_column(String(100), default="")
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, UTC
    model: Mapped[str] = mapped_column(String(300))
    upstream: Mapped[str] = mapped_column(String(200), default="")
    requests: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)  # reasoning tokens are part of these
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)  # part of the prompt tokens
    usage: Mapped[float] = mapped_column(Float, default=0.0)  # USD of OpenRouter credits
    byok_usage: Mapped[float] = mapped_column(Float, default=0.0)  # USD spent on the operator's own provider keys
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UsageKey(Base):
    """An API key of an OpenRouter account, as its management key lists it."""

    __tablename__ = "usage_keys"
    __table_args__ = (UniqueConstraint("provider_config_id", "key_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_config_id: Mapped[int] = mapped_column(ForeignKey("provider_configs.id", ondelete="CASCADE"), index=True)
    key_hash: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), default="")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    usage_total: Mapped[float] = mapped_column(Float, default=0.0)
    usage_today: Mapped[float] = mapped_column(Float, default=0.0)  # the current UTC day, which the activity API omits
    usage_month: Mapped[float] = mapped_column(Float, default=0.0)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RefreshRun(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # models | benchmarks | balances | usage
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | partial | failed
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed|cancelled
    suite: Mapped[str] = mapped_column(String(50))
    judge_provider_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_model_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    targets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    results: Mapped[list[EvalResult]] = relationship(back_populates="run", cascade="all, delete-orphan")


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    provider_config_id: Mapped[int] = mapped_column(Integer)
    model_id: Mapped[str] = mapped_column(String(300))
    canonical_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    task_id: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))  # passed | partial | failed | error | ungraded | skipped
    score: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100
    check_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    transcript: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    run: Mapped[EvalRun] = relationship(back_populates="results")
