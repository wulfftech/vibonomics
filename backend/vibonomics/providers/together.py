"""Together AI: serverless catalogue from the docs (public) or the pricing API (key), month-to-date spend."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import _f, apply_price, modelsdev_offers, overlay_live_ids, parse_markdown_tables, parse_money

log = logging.getLogger(__name__)

API = "https://api.together.ai/v1"
API_ROOT = "https://api.together.ai"
DOCS_MODELS = "https://docs.together.ai/docs/serverless/models.md"
_MIN_DOC_ROWS = 5

_STRIKE = re.compile(r"~~(.*?)~~")
_SEPARATOR = re.compile(r"^\s*\|(\s*:?-+:?\s*\|)+\s*$")


def _normalise(markdown: str) -> str:
    """Widen Mintlify's ``| :- | -: |`` separators to ``| --- |`` so the shared table parser sees them."""
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if _SEPARATOR.match(line):
            lines[i] = "|" + "|".join("---" for _ in line.strip().strip("|").split("|")) + "|"
    return "\n".join(lines)


def _cell(text: str | None) -> tuple[float | None, float | None]:
    """Price cell → (current, struck-through list price). '-' → None, 'Free' → 0."""
    t = (text or "").replace("\\", "").strip()
    struck = _STRIKE.findall(t)
    current = parse_money(_STRIKE.sub(" ", t).strip()) if t else None
    return current, (parse_money(struck[0]) if struck else None)


@dataclass
class DocRow:
    model_id: str
    name: str
    input: float | None
    output: float | None
    cache_read: float | None
    list_input: float | None = None
    list_output: float | None = None
    context: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def parse_docs(markdown: str) -> list[DocRow]:
    """Rows from the token-priced tables (chat, vision) of the serverless models page."""
    rows: dict[str, DocRow] = {}
    for table in parse_markdown_tables(_normalise(markdown)):
        if not table:
            continue
        headers = list(table[0].keys())
        low = {h: h.lower() for h in headers}
        id_h = next((h for h in headers if "model string" in low[h]), None)
        in_h = next((h for h in headers if low[h].startswith("input")), None)
        out_h = next((h for h in headers if low[h].startswith("output")), None)
        if not (id_h and in_h and out_h):
            continue
        cache_h = next((h for h in headers if low[h].startswith("cached")), None)
        name_h = next((h for h in headers if low[h] == "model name"), None)
        ctx_h = next((h for h in headers if "context" in low[h]), None)
        quant_h = next((h for h in headers if "quantization" in low[h]), None)
        for r in table:
            mid = r.get(id_h, "").strip()
            if not mid or mid in rows:
                continue
            p_in, l_in = _cell(r.get(in_h))
            p_out, l_out = _cell(r.get(out_h))
            cache, _ = _cell(r.get(cache_h)) if cache_h else (None, None)
            ctx = r.get(ctx_h, "") if ctx_h else ""
            row = DocRow(
                model_id=mid,
                name=(r.get(name_h) or mid).strip() if name_h else mid,
                input=p_in,
                output=p_out,
                cache_read=cache,
                list_input=l_in,
                list_output=l_out,
                context=int(ctx) if ctx.isdigit() else None,
            )
            quant = r.get(quant_h, "").strip() if quant_h else ""
            if quant and quant != "-":
                row.extra["quantization"] = quant
            rows[mid] = row
    return list(rows.values())


class TogetherAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="together",
        name="Together AI",
        description="Serverless open models. The catalogue and prices come from Together's public docs; a key "
        "switches to the pricing API and adds month-to-date spend.",
        homepage="https://www.together.ai",
        default_base_url=API,
        modelsdev_ids=("togetherai",),
        keys_url="https://api.together.ai/settings/api-keys",
        billing_url="https://api.together.ai/settings/billing",
        balance="key",
        balance_note="No balance API. Month-to-date spend comes from Together's beta billing-usage API, which "
        "Together enables per organisation.",
        aider_prefix="together_ai",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        catalog = self.ctx.catalog
        # Deprecated catalogue entries are kept only while the API or the docs still list them.
        offers = modelsdev_offers(catalog, self.spec.modelsdev_ids, include_deprecated=True)
        api_rows: list[dict[str, Any]] | None = None
        if self.cfg.api_key:
            try:
                api_rows = await self._supported_models()
            except FetchError as exc:
                log.warning("Together supported-models API failed, trying /v1/models: %s", exc)
            if api_rows is None:
                v1 = await get_json(f"{self.base_url}/models", headers=self.auth_headers())
                api_rows = _v1_rows(v1)

        if api_rows:
            known = {o.model_id for o in offers}
            for r in api_rows:
                r["model_id"] = next((c for c in r["candidates"] if c in known), r["candidates"][0])
            offers = overlay_live_ids(offers, [r["model_id"] for r in api_rows], catalog)
            by_id = {o.model_id: o for o in offers}
            for r in api_rows:
                o = by_id.get(r["model_id"])
                if o is None:
                    continue
                o.context_length = r.get("context") or o.context_length
                o.deprecated = bool(r.get("deprecated"))
                if r.get("tool_call") is not None:
                    o.tool_call = r["tool_call"]
                if r.get("reasoning") is not None:
                    o.reasoning = r["reasoning"]
                if r.get("vision") and "image" not in o.input_modalities:
                    o.input_modalities = [*(o.input_modalities or ["text"]), "image"]
                p: Price | None = r.get("price")
                if p is not None and p.known():
                    if p.cache_read is None and o.price.input == p.input:
                        p.cache_read = o.price.cache_read
                    apply_price(o, p, r["source"])
            offers = [o for o in offers if not o.deprecated or o.extra.get("live")]
            if all(o.pricing_source.startswith("together api") for o in offers):
                return offers

        rows: list[DocRow] = []
        page_error: str | None = None
        try:
            rows = parse_docs(await get_text(DOCS_MODELS, timeout=30))
            if not any(r.input is not None for r in rows):
                raise ValueError("no priced rows found in the docs model tables")
        except (FetchError, ValueError) as exc:
            page_error = str(exc)[:300]
            log.warning("Together docs page unusable, keeping catalogue prices: %s", page_error)
            rows = []

        if not api_rows and len(rows) >= _MIN_DOC_ROWS:
            # Without a key the docs table is the public serverless catalogue.
            offers = overlay_live_ids(offers, [r.model_id for r in rows], catalog)
            for o in offers:
                o.extra.pop("live", None)
                o.extra["listed_in_docs"] = True
                o.deprecated = False
        offers = [o for o in offers if not o.deprecated or o.extra.get("live") or o.extra.get("listed_in_docs")]
        by_row = {r.model_id: r for r in rows}
        for o in offers:
            if o.pricing_source.startswith("together api"):
                continue
            if page_error:
                o.extra["pricing_page_error"] = page_error
            row = by_row.get(o.model_id)
            if row is None:
                continue
            if o.name == o.model_id.split("/")[-1] and row.name:
                o.name = row.name
            o.context_length = o.context_length or row.context
            o.extra.update(row.extra)
            if row.input is None or row.output is None:
                continue
            cache = row.cache_read
            if cache is None and o.price.input == row.input:
                cache = o.price.cache_read
            list_price = None
            if row.list_input is not None or row.list_output is not None:
                list_price = Price(
                    input=row.list_input if row.list_input is not None else row.input,
                    output=row.list_output if row.list_output is not None else row.output,
                )
            apply_price(
                o,
                Price(input=row.input, output=row.output, cache_read=cache, cache_write=o.price.cache_write),
                "together docs",
                list_price=list_price,
                discount_note="Discounted on Together's model page" if list_price else None,
            )
        return offers

    async def _supported_models(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"product": "PRODUCT_SERVERLESS", "limit": 200}
            if after:
                params["after"] = after
            data = await get_json(f"{API_ROOT}/v2/supported-models", headers=self.auth_headers(), params=params)
            for m in data.get("data") or []:
                row = _v2_row(m)
                if row:
                    rows.append(row)
            after = data.get("next_cursor")
            if not after:
                break
        return rows

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key:
            return BalanceInfo(status="needs_key", message="Add an API key to see month-to-date spend.")
        month = datetime.now(UTC).strftime("%Y-%m")
        total = 0.0
        after: str | None = None
        data: dict[str, Any] = {}
        for _ in range(10):
            params: dict[str, Any] = {"month": month, "granularity": "day", "limit": 1000}
            if after:
                params["after"] = after
            try:
                data = await get_json(f"{API_ROOT}/v1/billing/usage", headers=self.auth_headers(), params=params)
            except FetchError as exc:
                if "HTTP 404" in str(exc):
                    return BalanceInfo(
                        status="unsupported",
                        message="Together has no balance API, and its beta billing-usage API isn't enabled for "
                        "this organisation. Ask Together support to enable it to see month-to-date spend.",
                    )
                return BalanceInfo(status="error", message=str(exc)[:300])
            total += _usage_cost(data)
            after = data.get("next_cursor")
            if not after:
                break
        return BalanceInfo(
            status="ok",
            account_used=round(total, 4),
            currency=str(data.get("currency") or "USD"),
            message="Month-to-date spend; Together has no balance API (current-month data lags up to an hour).",
            details={"billing_period": data.get("billing_period") or month},
        )


def _usage_cost(report: dict[str, Any]) -> float:
    total = 0.0
    for window in report.get("data") or []:
        for item in window.get("line_items") or []:
            total += _f(item.get("cost")) or 0.0
    return total


def _v2_row(m: dict[str, Any]) -> dict[str, Any] | None:
    status = str(m.get("status") or "")
    if status.endswith("HIDDEN"):
        return None
    caps = m.get("capabilities") or []
    pricing = m.get("pricing") or {}
    if caps and "CAPABILITY_CHAT" not in caps and not pricing:
        return None
    candidates = [c for c in (m.get("serverlessEndpoint"), m.get("name"), m.get("id")) if c]
    if not candidates:
        return None
    features = m.get("features") or []
    ctx = str(m.get("contextLength") or "")
    price = Price(
        input=_f(pricing.get("input")), output=_f(pricing.get("output")), cache_read=_f(pricing.get("cachedInput"))
    )
    return {
        "candidates": candidates,
        "context": int(ctx) if ctx.isdigit() else None,
        "deprecated": status.endswith("DEPRECATED"),
        "tool_call": ("FEATURE_TOOL_CALLING" in features) if features else None,
        "reasoning": ("FEATURE_REASONING" in features) if features else None,
        "vision": "MODALITY_IMAGE" in (m.get("inputModalities") or []),
        "price": price if price.known() else None,
        "source": "together api",
    }


def _v1_rows(data: Any) -> list[dict[str, Any]]:
    items = data if isinstance(data, list) else (data.get("data") or []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for m in items:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        pricing = m.get("pricing") or {}
        # Legacy /v1/models reports USD per 1M tokens; 0/0 there often means "not serverless", so skip it.
        price = Price(input=_f(pricing.get("input")), output=_f(pricing.get("output")))
        rows.append(
            {
                "candidates": [m["id"]],
                "context": m.get("context_length"),
                "price": price if price.known() and not price.is_zero() else None,
                "source": "together api (v1)",
            }
        )
    return rows
