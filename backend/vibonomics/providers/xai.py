"""xAI: prices from the docs pricing page, live model list, prepaid balance via a management key."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ChatTarget, ModelOffer, OptionField, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers, parse_markdown_tables
from .pricing_pages import PagePrice, apply_page_prices, clean_model_name, find_column, parse_price_cell

log = logging.getLogger(__name__)

API = "https://api.x.ai/v1"
MGMT_API = "https://management-api.x.ai"
PRICING_URL = "https://docs.x.ai/developers/pricing.md"
_MODEL_NAME = re.compile(r"^grok", re.I)


def _price(text: str | None) -> float | None:
    current, listed, _until, _note = parse_price_cell(text)
    return current if current is not None else listed


def _size(text: str | None) -> int | None:
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KM])", text or "", re.I)
    if not m:
        return None
    return int(float(m.group(1)) * (1_000_000 if m.group(2).upper() == "M" else 1_000))


def parse_pricing(markdown: str) -> list[PagePrice]:
    rows: dict[str, PagePrice] = {}
    for table in parse_markdown_tables(markdown):
        if not table:
            continue
        headers = list(table[0])
        i_model = find_column(headers, "model")
        i_in = find_column(headers, "input", exclude=("cached",))
        i_out = find_column(headers, "output")
        i_cache = find_column(headers, "cached")
        i_ctx = find_column(headers, "context")
        if i_model is None or i_in is None or i_out is None:
            continue
        for row in table:
            name = row.get(headers[i_model], "")
            if not _MODEL_NAME.match(clean_model_name(name)):
                continue
            key = clean_model_name(name)
            long_context = "≥" in name or "long context" in name.lower()
            price = PagePrice(
                name=name,
                input=_price(row.get(headers[i_in])),
                output=_price(row.get(headers[i_out])),
                cache_read=_price(row.get(headers[i_cache])) if i_cache is not None else None,
                extra={"context_length": _size(row.get(headers[i_ctx])) if i_ctx is not None else None},
            )
            if long_context:
                base = rows.get(key)
                if base is not None:
                    base.extra["long_context"] = {"input": price.input, "output": price.output}
                continue
            rows.setdefault(key, price)
    return list(rows.values())


class XaiAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="xai",
        name="xAI",
        description="Grok models direct from xAI. Prices come from the pricing page (with long-context rates kept "
        "alongside); a key adds the live model list, and a management key adds the prepaid balance.",
        homepage="https://x.ai",
        default_base_url=API,
        modelsdev_ids=("xai",),
        keys_url="https://console.x.ai/team/default/api-keys",
        billing_url="https://console.x.ai/team/default/billing",
        balance="management",
        balance_note="Prepaid balance needs a management key; a normal key can still read its own team id.",
        mgmt_key_label="Management key (optional)",
        mgmt_key_help="Create one in the xAI console to read the team's prepaid credit balance.",
        options=(
            OptionField(
                key="team_id",
                label="Team ID (optional)",
                description="Only needed for billing if it can't be read from the API key.",
            ),
        ),
        aider_prefix="xai",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        if self.cfg.api_key:
            try:
                data = await get_json(f"{API}/models", headers=self.auth_headers(), timeout=30)
                items = data.get("data") or data.get("models") or []
                live = {item.get("id") for item in items if isinstance(item, dict) and item.get("id")}
                for offer in offers:
                    if offer.model_id in live:
                        offer.extra["live"] = True
            except FetchError as exc:
                log.info("xAI model list failed: %s", exc)
        try:
            applied = apply_page_prices(
                offers, parse_pricing(await get_text(PRICING_URL, timeout=60)), "xai pricing page"
            )
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("xAI pricing page failed: %s", exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.mgmt_key:
            return BalanceInfo(status="needs_key", message="Add a management key to see the prepaid balance.")
        team_id = str((self.cfg.options or {}).get("team_id") or "").strip()
        details: dict[str, Any] = {}
        if not team_id and self.cfg.api_key:
            try:
                key_info = await get_json(f"{API}/api-key", headers=self.auth_headers(), timeout=20, retries=1)
                team_id = str(key_info.get("team_id") or "")
                details["key"] = {k: key_info.get(k) for k in ("name", "api_key_id", "team_blocked") if k in key_info}
            except FetchError as exc:
                details["key_error"] = str(exc)[:200]
        if not team_id:
            return BalanceInfo(
                status="needs_key",
                message="Set the Team ID option (or add a normal API key so Vibonomics can read it).",
                details=details,
            )
        data = await get_json(
            f"{MGMT_API}/v1/billing/teams/{team_id}/prepaid/balance",
            headers={"Authorization": f"Bearer {self.cfg.mgmt_key}"},
            timeout=30,
            retries=1,
        )
        total = (data.get("total") or {}).get("val")
        remaining = None
        try:
            # xAI reports USD cents with purchases negative: -1000 = $10 of credit left.
            remaining = round(-float(total) / 100, 2)
        except (TypeError, ValueError):
            pass
        return BalanceInfo(
            status="ok",
            account_balance=remaining,
            message="Prepaid credit (management API)." if remaining is not None else "No prepaid credit recorded.",
            details={**details, "team_id": team_id, "raw_total_cents": total},
        )

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; prices still load from xAI's pricing page."
        data = await get_json(f"{API}/api-key", headers=self.auth_headers(), timeout=20, retries=0)
        return f"Key accepted ({data.get('name') or data.get('api_key_id') or 'team key'})."

    def chat_target(self, model_id: str) -> ChatTarget:
        return ChatTarget(wire="openai", base_url=self.base_url, model=model_id, headers=self.auth_headers())
