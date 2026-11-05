"""Provider adapter interface.

Each provider module defines one ``ProviderAdapter`` subclass with a ``spec`` and registers it in
``registry.py``. Adapters are stateless apart from the resolved config they are built with; the
refresh job persists whatever they return.

Conventions:
- Prices are USD per 1M tokens, *after* any discount. When a discount applies, ``list_price``
  holds the undiscounted price and ``discount`` the fraction (0.5 = 50% off).
- Never raise for "this provider doesn't expose X": return ``BalanceInfo(status="unsupported")``.
  Raise ``FetchError`` (from ``vibonomics.http``) for real failures; the job records the message.
- Never log or return API keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from ..catalog.modelsdev import ModelsDevCatalog
from ..settings_store import AppSettings

Wire = Literal["openai", "anthropic"]
BalanceSupport = Literal["key", "management", "admin", "none"]
BalanceStatus = Literal["ok", "unsupported", "needs_key", "error"]


@dataclass(frozen=True)
class OptionField:
    """An extra per-provider setting shown in the provider form (e.g. xAI team id)."""

    key: str
    label: str
    description: str = ""
    required: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    type: str
    name: str
    description: str
    homepage: str
    default_base_url: str
    """OpenAI-compatible base URL (ending in /v1 or equivalent) used for evals and config snippets."""
    modelsdev_ids: tuple[str, ...] = ()
    """models.dev provider ids used as the base catalogue for this provider."""
    keys_url: str | None = None
    billing_url: str | None = None
    anthropic_base_url: str | None = None
    """Anthropic-compatible base URL if the provider offers one (used for Claude Code snippets)."""
    wire: Wire = "openai"
    models_require_key: bool = False
    balance: BalanceSupport = "none"
    balance_note: str = ""
    mgmt_key_label: str | None = None
    mgmt_key_help: str | None = None
    options: tuple[OptionField, ...] = ()
    custom: bool = False
    """User supplies the base URL (generic OpenAI-compatible endpoint)."""
    default_enabled: bool = False
    """Created automatically (without a key) on first run."""
    aider_prefix: str | None = None
    """LiteLLM/aider provider prefix, e.g. ``openrouter`` → ``openrouter/<model>``."""
    extra_key_label: str | None = None
    """A second optional secret (for example a Claude Code login token for usage tracking)."""
    extra_key_help: str | None = None

    @property
    def env_key(self) -> str:
        return f"VIBONOMICS_{self.type.upper().replace('-', '_')}_API_KEY"

    @property
    def env_mgmt_key(self) -> str:
        return f"VIBONOMICS_{self.type.upper().replace('-', '_')}_MGMT_KEY"

    @property
    def env_extra_key(self) -> str:
        return f"VIBONOMICS_{self.type.upper().replace('-', '_')}_EXTRA_KEY"


@dataclass
class Price:
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None

    def is_zero(self) -> bool:
        return self.input == 0 and self.output == 0

    def known(self) -> bool:
        return self.input is not None and self.output is not None


@dataclass
class ModelOffer:
    model_id: str
    name: str
    creator: str | None = None
    canonical_id: str | None = None
    family: str | None = None
    context_length: int | None = None
    max_output: int | None = None
    input_modalities: list[str] = field(default_factory=list)
    output_modalities: list[str] = field(default_factory=list)
    reasoning: bool | None = None
    tool_call: bool | None = None
    open_weights: bool | None = None
    release_date: str | None = None
    price: Price = field(default_factory=Price)
    list_price: Price | None = None
    discount: float | None = None
    discount_note: str | None = None
    discount_until: str | None = None
    is_free: bool = False
    pricing_source: str = "models.dev"
    deprecated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BalanceInfo:
    status: BalanceStatus
    account_balance: float | None = None
    currency: str = "USD"
    account_total: float | None = None
    account_used: float | None = None
    key_usage: float | None = None
    key_limit: float | None = None
    key_remaining: float | None = None
    key_label: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatTarget:
    """Where and how to send a chat request for one model (used by the eval runner)."""

    wire: Wire
    base_url: str
    model: str
    headers: dict[str, str]
    session_header: str | None = None
    """A header the provider asks every client to fill with a stable id per conversation (OpenCode Go)."""


@dataclass
class ResolvedProvider:
    id: int
    type: str
    name: str
    api_key: str | None
    mgmt_key: str | None
    base_url: str | None
    options: dict[str, Any]
    extra_key: str | None = None


@dataclass
class FetchContext:
    catalog: ModelsDevCatalog
    settings: AppSettings


class ProviderAdapter:
    spec: ClassVar[ProviderSpec]

    def __init__(self, cfg: ResolvedProvider, ctx: FetchContext):
        self.cfg = cfg
        self.ctx = ctx

    @property
    def base_url(self) -> str:
        return (self.cfg.base_url or self.spec.default_base_url).rstrip("/")

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}

    async def fetch_models(self) -> list[ModelOffer]:
        """Return every model offered with current pricing. Default: models.dev + live id list."""
        from .common import modelsdev_offers, openai_model_ids, overlay_live_ids

        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        if self.cfg.api_key:
            live = await openai_model_ids(f"{self.base_url}/models", self.auth_headers())
            offers = overlay_live_ids(offers, live, self.ctx.catalog)
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        return BalanceInfo(status="unsupported", message=self.spec.balance_note or "No balance API")

    async def test_connection(self) -> str:
        """Validate the key; return a short success message or raise ``FetchError``."""
        from .common import openai_model_ids

        if not self.cfg.api_key:
            return "No API key set; using public catalogue data only."
        ids = await openai_model_ids(f"{self.base_url}/models", self.auth_headers())
        return f"Key accepted ({len(ids)} models visible)."

    def chat_target(self, model_id: str) -> ChatTarget:
        return ChatTarget(
            wire=self.spec.wire,
            base_url=self.base_url,
            model=model_id,
            headers=self.auth_headers(),
        )
