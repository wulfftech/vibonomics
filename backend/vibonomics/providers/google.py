"""Google Gemini (AI Studio): prices from the pricing page, live model list, no balance API."""

from __future__ import annotations

import logging
import re

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ModelOffer, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers, parse_markdown_tables
from .pricing_pages import PagePrice, apply_page_prices, is_free_text, is_unavailable, parse_price_cell

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_BASE = f"{API}/openai"
PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing.md.txt"

_MODEL_ID = re.compile(r"\[`([a-z0-9][a-z0-9.\-]*)`\]")
_SECTION = re.compile(r"^## (.+)$", re.MULTILINE)

_ROW_KEYS = {
    "input price": "input",
    "output price": "output",
    "context caching price": "cache_read",
    # Not "context caching storage": that is priced per token-hour, which is no price to write a token to the cache.
    "cached input price": "cache_read",
}


def _paid(cell_text: str | None) -> tuple[float | None, float | None, str | None]:
    current, listed, until, note = parse_price_cell(cell_text)
    return current, listed, until or note  # note doubles as "no date" carrier when it stems from prose


def parse_pricing(markdown: str) -> list[PagePrice]:
    rows: list[PagePrice] = []
    parts = _SECTION.split(markdown)
    # split() gives [intro, title1, body1, title2, body2, ...]
    for title, body in zip(parts[1::2], parts[2::2], strict=False):
        ids = _MODEL_ID.findall(body[:800])
        if not ids:
            continue
        standard = body.split("### Standard", 1)
        if len(standard) < 2:
            continue
        table_text = re.split(r"\n### ", standard[1], maxsplit=1)[0]
        for table in parse_markdown_tables(table_text):
            if not table:
                continue
            header = list(table[0])
            if len(header) < 2:
                continue
            free_col, paid_col = header[1], header[-1]
            values: dict[str, PagePrice] = {}
            free_tier = False
            for row in table:
                label = row.get(header[0], "").strip().lower()
                field = next((v for k, v in _ROW_KEYS.items() if label.startswith(k)), None)
                if field is None:
                    continue
                free_cell = row.get(free_col)
                if not is_unavailable(free_cell) and (is_free_text(free_cell) or parse_price_cell(free_cell)[0] == 0):
                    free_tier = True
                current, listed, until, note = parse_price_cell(row.get(paid_col))
                price = values.setdefault(field, PagePrice(name=title))
                setattr(price, field, current)
                if field in ("input", "output") and listed is not None:
                    setattr(price, "list_" + field, listed)
                if note and not price.discount_note:
                    price.discount_note = note
                if until and len(until) == 10 and not price.discount_until:
                    price.discount_until = until
            for model_id in ids:
                price = values.get("input")
                if price is None or not price.known():
                    continue
                merged = PagePrice(
                    name=title,
                    model_id=model_id,
                    input=values["input"].input,
                    output=values.get("output").output if values.get("output") else None,
                    cache_read=values.get("cache_read").cache_read if values.get("cache_read") else None,
                    cache_write=values.get("cache_write").cache_write if values.get("cache_write") else None,
                    list_input=values["input"].list_input,
                    list_output=values["output"].list_output if values.get("output") else None,
                    discount_note=values["input"].discount_note,
                    discount_until=values["input"].discount_until,
                    extra={"free_tier": free_tier},
                )
                rows.append(merged)
    return rows


class GoogleAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="google",
        name="Google Gemini",
        description="Gemini models from Google AI Studio. Prices come from the official pricing page (including "
        "promotional dates and free-tier availability); a key adds the live model list.",
        homepage="https://ai.google.dev",
        default_base_url=OPENAI_BASE,
        modelsdev_ids=("google",),
        keys_url="https://aistudio.google.com/api-keys",
        billing_url="https://aistudio.google.com/billing",
        balance="none",
        balance_note="Google doesn't expose AI Studio prepaid credits through the Gemini API; the balance is only "
        "visible in AI Studio.",
        aider_prefix="gemini",
    )

    def auth_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.cfg.api_key} if self.cfg.api_key else {}

    async def fetch_models(self) -> list[ModelOffer]:
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        if self.cfg.api_key:
            try:
                ids: list[str] = []
                page_token: str | None = None
                for _ in range(10):
                    params: dict[str, str | int] = {"pageSize": 1000}
                    if page_token:
                        params["pageToken"] = page_token
                    data = await get_json(f"{API}/models", headers=self.auth_headers(), params=params, timeout=30)
                    for model in data.get("models", []) or []:
                        methods = model.get("supportedGenerationMethods") or []
                        if methods and "generateContent" not in methods:
                            continue
                        name = str(model.get("name") or "").removeprefix("models/")
                        if name:
                            ids.append(name)
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break
                live = set(ids)
                for offer in offers:
                    if offer.model_id in live:
                        offer.extra["live"] = True
            except FetchError as exc:
                log.info("Gemini model list failed: %s", exc)
        try:
            applied = apply_page_prices(
                offers, parse_pricing(await get_text(PRICING_URL, timeout=60)), "google pricing page"
            )
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("Gemini pricing page failed: %s", exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        return BalanceInfo(status="unsupported", message=self.spec.balance_note)

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; prices still load from Google's pricing page."
        data = await get_json(
            f"{API}/models", headers=self.auth_headers(), params={"pageSize": 1}, timeout=20, retries=0
        )
        return f"Key accepted ({len(data.get('models', []) or [])} model(s) visible)."
