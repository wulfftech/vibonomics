"""Mistral: prices from the pricing docs page, live model list, no self-serve balance API."""

from __future__ import annotations

import logging
import re

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ModelOffer, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers, parse_markdown_tables
from .pricing_pages import PagePrice, apply_page_prices, find_column

log = logging.getLogger(__name__)

API = "https://api.mistral.ai/v1"
PRICING_URL = "https://docs.mistral.ai/inference/pricing.md"

_IN = re.compile(r"input\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
_OUT = re.compile(r"output\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
_CACHE = re.compile(r"cached\s+input\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)


def parse_pricing(markdown: str) -> list[PagePrice]:
    rows: list[PagePrice] = []
    for table in parse_markdown_tables(markdown):
        if not table:
            continue
        headers = list(table[0])
        i_model = find_column(headers, "model")
        i_name = find_column(headers, "api name")
        i_price = find_column(headers, "price")
        if i_model is None or i_price is None:
            continue
        for row in table:
            price_text = row.get(headers[i_price], "")
            if "/m tokens" not in price_text.lower() and "/1m" not in price_text.lower():
                continue  # OCR/audio/embedding rows are priced differently
            # Cells arrive with markdown stripped, so the API name column holds the bare id.
            api_names = row.get(headers[i_name], "").replace(",", " ").split() if i_name is not None else []
            model_id = api_names[0] if api_names else None
            inp, out = _IN.search(price_text), _OUT.search(price_text)
            cache = _CACHE.search(price_text)
            if not inp or not out:
                continue
            rows.append(
                PagePrice(
                    name=row.get(headers[i_model], ""),
                    model_id=model_id,
                    input=float(inp.group(1)),
                    output=float(out.group(1)),
                    cache_read=float(cache.group(1)) if cache else None,
                )
            )
    return rows


class MistralAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="mistral",
        name="Mistral",
        description="Mistral models direct. Prices come from Mistral's pricing page; a key adds the live model "
        "list. Mistral's spend/limit API is enterprise-only, so no balance is shown.",
        homepage="https://mistral.ai",
        default_base_url=API,
        modelsdev_ids=("mistral",),
        keys_url="https://console.mistral.ai/api-keys",
        billing_url="https://console.mistral.ai/billing",
        balance="none",
        balance_note="No self-serve balance API; spend limits are only available to enterprise accounts.",
        aider_prefix="mistral",
    )

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
                log.info("Mistral model list failed: %s", exc)
        try:
            applied = apply_page_prices(
                offers, parse_pricing(await get_text(PRICING_URL, timeout=60)), "mistral pricing page"
            )
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("Mistral pricing page failed: %s", exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        return BalanceInfo(status="unsupported", message=self.spec.balance_note)

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; prices still load from Mistral's pricing page."
        data = await get_json(f"{API}/models", headers=self.auth_headers(), timeout=20, retries=0)
        return f"Key accepted ({len(data.get('data', []) or [])} models visible)."
