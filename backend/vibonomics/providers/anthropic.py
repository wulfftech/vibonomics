"""Anthropic: models.dev + live model list, prices from the pricing page, Admin API spend."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ..http import FetchError, get_json, get_text
from ..subscriptions import normalize_usage
from .base import BalanceInfo, ChatTarget, ModelOffer, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers, parse_markdown_tables
from .pricing_pages import PagePrice, apply_page_prices, find_column, parse_price_cell

log = logging.getLogger(__name__)

API = "https://api.anthropic.com"
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
API_VERSION = "2023-06-01"
_MIN_ROWS = 3


def _amount(text: str | None) -> float | None:
    current, listed, _until, _note = parse_price_cell(text)
    return current if current is not None else listed


def parse_pricing(markdown: str) -> list[PagePrice]:
    rows: list[PagePrice] = []
    for table in parse_markdown_tables(markdown):
        if len(table) < _MIN_ROWS:
            continue
        headers = list(table[0])
        i_model = find_column(headers, "model")
        i_in = find_column(headers, "base input")
        i_out = find_column(headers, "output")
        if i_model is None or i_in is None or i_out is None:
            continue
        i_cache = find_column(headers, "cache hits")
        i_cw5 = find_column(headers, "5m cache")
        i_cw1 = find_column(headers, "1h cache")
        for row in table:
            name = row.get(headers[i_model], "").strip()
            if not name:
                continue
            rows.append(
                PagePrice(
                    name=name,
                    input=_amount(row.get(headers[i_in])),
                    output=_amount(row.get(headers[i_out])),
                    cache_read=_amount(row.get(headers[i_cache])) if i_cache is not None else None,
                    cache_write=_amount(row.get(headers[i_cw5])) if i_cw5 is not None else None,
                    extra={
                        "cache_write_1h": _amount(row.get(headers[i_cw1])) if i_cw1 is not None else None,
                        "batch_discount": 0.5,
                    },
                )
            )
    return rows


class AnthropicAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="anthropic",
        name="Anthropic",
        description="Claude models direct from Anthropic. Prices come from the official pricing page; a key adds "
        "the live model list. An org Admin key adds spend, and a Claude Code login token adds subscription usage.",
        homepage="https://www.anthropic.com",
        default_base_url=API,
        anthropic_base_url=API,
        modelsdev_ids=("anthropic",),
        keys_url="https://console.anthropic.com/settings/keys",
        billing_url="https://console.anthropic.com/settings/billing",
        wire="anthropic",
        balance="admin",
        balance_note="Anthropic has no prepaid balance. Subscription usage comes from Claude Code; an "
        "organisation Admin key adds spend.",
        mgmt_key_label="Admin API key (optional)",
        mgmt_key_help="Organisation admin keys (sk-ant-admin...) unlock cost reports. Individual accounts cannot "
        "create one.",
        extra_key_label="Claude Code login token (optional)",
        extra_key_help="Reads subscription usage windows (and usage-credit spend) from Claude Code's usage "
        "endpoint. Paste the accessToken from ~/.claude/.credentials.json after an interactive claude.ai login; "
        "tokens from `claude setup-token` lack the profile scope. The token expires and is never refreshed here. "
        "For live usage without a token, push Claude Code's status line (Settings -> Claude).",
        aider_prefix="anthropic",
    )

    def _headers(self) -> dict[str, str]:
        headers = {"anthropic-version": API_VERSION}
        if self.cfg.api_key:
            headers["x-api-key"] = self.cfg.api_key
        return headers

    async def fetch_models(self) -> list[ModelOffer]:
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        if self.cfg.api_key:
            try:
                data = await get_json(f"{API}/v1/models?limit=1000", headers=self._headers(), timeout=30)
                live = [m.get("id") for m in data.get("data", []) if m.get("id")]
                _mark_live(offers, live)
            except FetchError as exc:
                log.info("Anthropic model list failed: %s", exc)
        try:
            markdown = await get_text(PRICING_URL, timeout=60)
            applied = apply_page_prices(offers, parse_pricing(markdown), "anthropic pricing page")
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("Anthropic pricing page failed: %s", exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        return offers

    async def _subscription_usage(self) -> tuple[dict[str, Any] | None, str | None]:
        if not self.cfg.extra_key:
            return None, None
        try:
            data = await get_json(
                USAGE_URL,
                headers={
                    "Authorization": f"Bearer {self.cfg.extra_key}",
                    "anthropic-beta": OAUTH_BETA,
                    "Accept": "application/json",
                },
                timeout=30,
                retries=1,
            )
        except FetchError as exc:
            return None, str(exc)[:300]
        if not isinstance(data, dict):
            return None, "The usage endpoint returned an unexpected response."
        return normalize_usage(data), None

    async def _admin_spend(self) -> float:
        now = datetime.now(UTC)
        start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        data = await get_json(
            f"{API}/v1/organizations/cost_report",
            headers={**self._headers(), "x-api-key": self.cfg.mgmt_key, "anthropic-version": API_VERSION},
            params={
                "starting_at": start,
                "ending_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bucket_width": "1d",
                "limit": 31,
            },
            timeout=30,
            retries=1,
        )
        total_cents = 0.0
        for bucket in data.get("data", []) or []:
            for result in bucket.get("results", []) or []:
                try:
                    total_cents += float(result.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
        return round(total_cents / 100, 2)

    async def fetch_balance(self) -> BalanceInfo:
        details: dict[str, Any] = {}
        notes: list[str] = []
        subscription, subscription_error = await self._subscription_usage()
        if subscription is not None:
            details["subscription"] = subscription
            notes.append("Subscription usage from the Claude Code login token.")
        if subscription_error:
            details["subscription_error"] = subscription_error

        admin_spend: float | None = None
        if self.cfg.mgmt_key:
            try:
                admin_spend = await self._admin_spend()
                notes.append(f"Admin API spend over 30 days: ${admin_spend:.2f}.")
            except FetchError as exc:
                details["admin_error"] = str(exc)[:300]

        if subscription is not None or admin_spend is not None:
            return BalanceInfo(
                status="ok",
                account_used=admin_spend,
                message=" ".join(notes) or None,
                details=details,
            )
        if subscription_error:
            return BalanceInfo(status="error", message=subscription_error, details=details)
        return BalanceInfo(
            status="needs_key" if (self.cfg.api_key or self.cfg.extra_key) else "unsupported",
            message="Anthropic has no prepaid balance. Add a Claude Code login token for subscription usage, or an "
            "Admin API key for spend.",
            details=details,
        )

    async def test_connection(self) -> str:
        parts: list[str] = []
        if self.cfg.api_key:
            data = await get_json(f"{API}/v1/models?limit=100", headers=self._headers(), timeout=20, retries=0)
            models = data.get("data", []) or []
            visible = f"{len(models)} model(s)" if len(models) < 100 else "100+ models"
            parts.append(f"API key accepted ({visible} visible).")
        if self.cfg.extra_key:
            subscription, error = await self._subscription_usage()
            if error:
                raise FetchError(f"Login token rejected: {error}")
            windows = len((subscription or {}).get("windows") or [])
            parts.append(f"Login token accepted ({windows} usage window(s)).")
        if not parts:
            return "No key set; prices still load from Anthropic's pricing page."
        return " ".join(parts)

    def chat_target(self, model_id: str) -> ChatTarget:
        return ChatTarget(wire="anthropic", base_url=self.base_url, model=model_id, headers=self._headers())


def _mark_live(offers: list[ModelOffer], live: list[str]) -> None:
    for offer in offers:
        for model_id in live:
            if model_id == offer.model_id or model_id.startswith(offer.model_id + "-"):
                offer.extra["live"] = True
                offer.extra["live_id"] = model_id
                break
