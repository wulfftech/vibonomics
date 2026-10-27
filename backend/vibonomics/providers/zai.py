"""Z.ai (GLM): prices from the public pricing page, live model list with a key, no balance API."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..http import FetchError, get_text, request
from .base import ModelOffer, OptionField, Price, ProviderAdapter, ProviderSpec
from .common import (
    apply_price,
    find_offer,
    modelsdev_offers,
    openai_model_ids,
    overlay_live_ids,
    parse_markdown_tables,
)
from .common import parse_money as _money

log = logging.getLogger(__name__)

API = "https://api.z.ai/api/paas/v4"
API_CODING = "https://api.z.ai/api/coding/paas/v4"
PRICING_PAGE = "https://docs.z.ai/guides/overview/pricing.md"
_PROBE_MODEL = "glm-4.7-flash"  # free model, used to validate a key if /models is unavailable

_STRIKE = re.compile(r"~~(.*?)~~")
_SEPARATOR = re.compile(r"^\s*\|(\s*:?-+:?\s*\|)+\s*$")


@dataclass
class Cell:
    current: float | None = None
    list_price: float | None = None
    note: str | None = None


@dataclass
class PriceRow:
    name: str
    input: Cell
    output: Cell
    cache_read: Cell
    extra: dict[str, str] = field(default_factory=dict)


def parse_cell(text: str | None) -> Cell:
    """'$1.4' → 1.4; 'Free' → 0; 'Limited-time Free' → 0 + note; '~~$1~~ $0.5' → 0.5 with list 1."""
    t = (text or "").replace("\\", "").strip()
    if not t or t in {"-", "—", "–"}:
        return Cell()
    struck = _STRIKE.findall(t)
    list_price = _money(struck[0]) if struck else None
    rest = _STRIKE.sub(" ", t).strip()
    low = rest.lower()
    if "limited-time free" in low or "limited time free" in low:
        return Cell(0.0, list_price, "Limited-time free")
    if low.startswith("free"):
        return Cell(0.0, list_price, "Free" if list_price else None)
    current = _money(rest)
    return Cell(current, list_price if current is not None else None, None)


def _normalise(markdown: str) -> str:
    """Unquote blockquoted tables and widen Mintlify's ``| :- | :- |`` separators to ``| --- |``."""
    lines = []
    for line in markdown.splitlines():
        line = re.sub(r"^\s*>\s?", "", line)
        if _SEPARATOR.match(line):
            line = "|" + "|".join("---" for _ in line.strip().strip("|").split("|")) + "|"
        lines.append(line)
    return "\n".join(lines)


def parse_pricing(markdown: str) -> list[PriceRow]:
    """Token-priced rows from every pricing table, including the ones nested in blockquotes."""
    rows: list[PriceRow] = []
    for table in parse_markdown_tables(_normalise(markdown)):
        if not table:
            continue
        headers = list(table[0].keys())
        low = {h: h.lower().strip() for h in headers}
        model_h = next((h for h in headers if low[h] in {"model", "models"}), None)
        in_h = next((h for h in headers if low[h] == "input"), None)
        out_h = next((h for h in headers if low[h] == "output"), None)
        cache_h = next((h for h in headers if low[h] == "cached input"), None)
        storage_h = next((h for h in headers if "storage" in low[h]), None)
        if not (model_h and in_h and out_h):
            continue  # images, video, tools, agents: not per-token
        for r in table:
            name = r.get(model_h, "").strip()
            if not name:
                continue
            row = PriceRow(
                name=name,
                input=parse_cell(r.get(in_h)),
                output=parse_cell(r.get(out_h)),
                cache_read=parse_cell(r.get(cache_h)) if cache_h else Cell(),
            )
            if storage_h and r.get(storage_h, "").replace("\\", "").strip() not in {"", "-"}:
                row.extra["cache_storage_price"] = r[storage_h].replace("\\", "").strip()
            rows.append(row)
    return rows


class ZaiAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="zai",
        name="Z.ai (GLM)",
        description="GLM models direct from Z.ai. Prices come from the public pricing page; a key adds the live "
        "model list. Since 2026-10-16 GLM Coding Plan keys are separate from standard API keys, so set the "
        "endpoint option to match your key.",
        homepage="https://z.ai/model-api",
        default_base_url=API,
        anthropic_base_url="https://api.z.ai/api/anthropic",
        modelsdev_ids=("zai",),
        keys_url="https://z.ai/manage-apikey/apikey-list",
        billing_url="https://z.ai/manage-apikey/billing",
        balance="none",
        balance_note="Z.ai has no balance or quota API; check the billing page.",
        options=(
            OptionField(
                key="endpoint",
                label="Key type",
                description="'standard' (default): pay-as-you-go key on /api/paas/v4. 'coding': GLM Coding Plan "
                "key on /api/coding/paas/v4 (uses plan quota instead of per-token billing).",
                placeholder="standard",
            ),
        ),
        aider_prefix="zai",
    )

    @property
    def coding_plan(self) -> bool:
        endpoint = str((self.cfg.options or {}).get("endpoint") or "").strip().lower()
        return endpoint in {"coding", "coding-plan", "coding_plan"} or "/coding/" in (self.cfg.base_url or "")

    @property
    def base_url(self) -> str:
        if self.cfg.base_url:
            return self.cfg.base_url.rstrip("/")
        return API_CODING if self.coding_plan else API

    async def fetch_models(self) -> list[ModelOffer]:
        catalog = self.ctx.catalog
        pids = ("zai-coding-plan",) if self.coding_plan else self.spec.modelsdev_ids
        # Deprecated catalogue entries are kept only while the pricing page or the live list still shows them.
        offers = modelsdev_offers(catalog, pids, include_deprecated=True)
        rows: list[PriceRow] = []
        page_error: str | None = None
        if not self.coding_plan:
            try:
                rows = parse_pricing(await get_text(PRICING_PAGE, timeout=30))
                if not any(r.input.current is not None for r in rows):
                    raise ValueError("no token prices found on the pricing page")
            except (FetchError, ValueError) as exc:
                page_error = str(exc)[:300]
                log.warning("Z.ai pricing page unusable, keeping models.dev prices: %s", page_error)
                rows = []
            for row in rows:
                offer = find_offer(offers, row.name)
                if offer is None and row.input.current is not None:
                    # A model the catalogue doesn't know yet; Z.ai model ids are the lowercased names.
                    mid = row.name.lower()
                    offer = ModelOffer(
                        model_id=mid,
                        name=row.name,
                        creator="zhipuai",
                        canonical_id=catalog.guess_canonical(mid, row.name),
                        pricing_source="z.ai docs",
                        extra={"id_from_pricing_page": True},
                    )
                    offers.append(offer)
                if offer is not None:
                    offer.extra["listed_in_docs"] = True

        if self.cfg.api_key:
            try:
                live = await openai_model_ids(f"{self.base_url}/models", self.auth_headers())
            except FetchError as exc:
                if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                    raise
                # /models isn't documented; fall back to the catalogue rather than failing.
                log.info("Z.ai model list unavailable: %s", exc)
                live = []
            if live:
                offers = overlay_live_ids(offers, live, catalog)
        offers = [o for o in offers if not o.deprecated or o.extra.get("live") or o.extra.get("listed_in_docs")]

        for o in offers:
            if page_error:
                o.extra["pricing_page_error"] = page_error
            if self.coding_plan:
                o.is_free = False
                o.pricing_source = "models.dev (GLM Coding Plan)"
                o.extra["billing"] = "GLM Coding Plan subscription quota, not per-token"
        for row in rows:
            offer = find_offer(offers, row.name)
            if offer is not None:
                _apply_row(offer, row)
        return offers

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; public pricing still works."
        try:
            ids = await openai_model_ids(f"{self.base_url}/models", self.auth_headers())
            return f"Key accepted ({len(ids)} models visible)."
        except FetchError as exc:
            if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                raise
        model = "glm-5.3-flash" if self.coding_plan else _PROBE_MODEL
        await request(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self.auth_headers(),
            json_body={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=60,
            retries=0,
        )
        return f"Key accepted (1-token test call to {model})."


def _apply_row(offer: ModelOffer, row: PriceRow) -> None:
    offer.extra.update(row.extra)
    if row.input.current is None or row.output.current is None:
        return
    price = Price(
        input=row.input.current,
        output=row.output.current,
        cache_read=row.cache_read.current if row.cache_read.current is not None else offer.price.cache_read,
        cache_write=offer.price.cache_write,
    )
    note = row.input.note or row.output.note
    struck = row.input.list_price is not None or row.output.list_price is not None
    list_price = None
    if note == "Limited-time free" or struck:
        # Undiscounted rate: struck-through value if shown, else the catalogue price we already had.
        base_in = row.input.list_price if row.input.list_price is not None else offer.price.input
        base_out = row.output.list_price if row.output.list_price is not None else offer.price.output
        if row.input.note != "Limited-time free" and row.input.list_price is None:
            base_in = row.input.current
        if row.output.note != "Limited-time free" and row.output.list_price is None:
            base_out = row.output.current
        list_price = Price(input=base_in, output=base_out)
    apply_price(
        offer,
        price,
        "z.ai docs",
        list_price=list_price,
        discount_note=note
        if note == "Limited-time free"
        else ("Discounted on Z.ai's pricing page" if struck else None),
    )
