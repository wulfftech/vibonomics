"""OpenAI: models.dev + live model list, prices from the pricing page, Admin API spend."""

from __future__ import annotations

import logging
import time

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ModelOffer, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers, parse_markdown_tables
from .pricing_pages import PagePrice, apply_page_prices, find_column, parse_price_cell

log = logging.getLogger(__name__)

API = "https://api.openai.com/v1"
PRICING_URL = "https://developers.openai.com/api/docs/pricing.md"
_MIN_ROWS = 3


def _price(text: str | None) -> float | None:
    current, listed, _until, _note = parse_price_cell(text)
    return current if current is not None else listed


def parse_pricing(markdown: str) -> list[PagePrice]:
    """Parse the Standard pricing tables (the first table per model group wins)."""
    rows: list[PagePrice] = []
    seen: set[str] = set()
    for table in parse_markdown_tables(markdown):
        if len(table) < _MIN_ROWS:
            continue
        headers = list(table[0])
        i_model = find_column(headers, "model")
        # "Short context input" on the flagship table, plain "Input" on the others; never long context.
        i_in = find_column(headers, "input", exclude=("cached", "write", "long"))
        i_out = find_column(headers, "output", exclude=("long",))
        i_cache = find_column(headers, "cached input")
        if i_model is None or i_in is None or i_out is None:
            continue
        for row in table:
            name = row.get(headers[i_model], "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                PagePrice(
                    name=name,
                    input=_price(row.get(headers[i_in])),
                    output=_price(row.get(headers[i_out])),
                    cache_read=_price(row.get(headers[i_cache])) if i_cache is not None else None,
                    extra={
                        "batch_discount": 0.5,
                        "long_context": {
                            "input": _price(row.get(headers[find_column(headers, "long context input")], None))
                            if find_column(headers, "long context input") is not None
                            else None,
                            "output": _price(row.get(headers[find_column(headers, "long context output")], None))
                            if find_column(headers, "long context output") is not None
                            else None,
                        },
                    },
                )
            )
    return rows


class OpenAIAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="openai",
        name="OpenAI",
        description="OpenAI models direct from OpenAI. Prices come from the official pricing page; a key adds "
        "the live model list, and an org Admin key adds spend reporting.",
        homepage="https://openai.com",
        default_base_url=API,
        modelsdev_ids=("openai",),
        keys_url="https://platform.openai.com/api-keys",
        billing_url="https://platform.openai.com/settings/organization/billing",
        balance="admin",
        balance_note="No balance API. An organisation Admin key shows spend from the Costs API.",
        mgmt_key_label="Admin API key (optional)",
        mgmt_key_help="Organisation admin keys (sk-admin-…) unlock cost reporting. Requires an organisation.",
        aider_prefix="openai",
    )

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}

    async def fetch_models(self) -> list[ModelOffer]:
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        if self.cfg.api_key:
            try:
                data = await get_json(f"{API}/models", headers=self.auth_headers(), timeout=30)
                live = {m.get("id") for m in data.get("data", []) if m.get("id")}
                for offer in offers:
                    if offer.model_id in live:
                        offer.extra["live"] = True
            except FetchError as exc:
                log.info("OpenAI model list failed: %s", exc)
        try:
            applied = apply_page_prices(
                offers, parse_pricing(await get_text(PRICING_URL, timeout=60)), "openai pricing page"
            )
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("OpenAI pricing page failed: %s", exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.mgmt_key:
            return BalanceInfo(
                status="needs_key" if self.cfg.api_key else "unsupported",
                message="Add an organisation Admin API key to see spend. OpenAI has no balance API.",
            )
        start = int(time.time()) - 30 * 86400
        data = await get_json(
            f"{API}/organization/costs",
            headers={"Authorization": f"Bearer {self.cfg.mgmt_key}"},
            params={"start_time": start, "bucket_width": "1d", "limit": 31},
            timeout=30,
            retries=1,
        )
        total = 0.0
        for bucket in data.get("data", []) or []:
            for result in bucket.get("results", []) or []:
                amount = result.get("amount") or {}
                try:
                    total += float(amount.get("value") or 0)
                except (TypeError, ValueError):
                    continue
        return BalanceInfo(
            status="ok",
            account_used=round(total, 2),
            message="Spend over the last 30 days (Costs API). OpenAI has no remaining-balance endpoint.",
            details={"window_days": 30, "currency": "usd"},
        )

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; prices still load from OpenAI's pricing page."
        data = await get_json(f"{API}/models", headers=self.auth_headers(), timeout=20, retries=0)
        return f"Key accepted ({len(data.get('data', []) or [])} models visible)."
