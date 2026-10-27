"""Helpers shared by provider adapters: models.dev conversion, live overlays, page parsing."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..catalog.modelsdev import ModelsDevCatalog, creator_of, normalize_model_key
from ..http import get_json
from .base import ModelOffer, Price

# -- models.dev ------------------------------------------------------------------------------


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def offer_from_modelsdev(model: dict[str, Any], provider_id: str) -> ModelOffer:
    cost = model.get("cost") or {}
    limit = model.get("limit") or {}
    modalities = model.get("modalities") or {}
    canonical = model.get("canonical_model_id")
    price = Price(
        input=_f(cost.get("input")),
        output=_f(cost.get("output")),
        cache_read=_f(cost.get("cache_read")),
        cache_write=_f(cost.get("cache_write")),
    )
    model_id = model.get("id") or ""
    extra: dict[str, Any] = {}
    if cost.get("tiers"):
        extra["tiers"] = cost["tiers"]
    if model.get("provider"):
        extra["endpoint"] = model["provider"]  # per-model api/npm override (e.g. OpenCode routes)
    if model.get("description"):
        extra["description"] = model["description"]
    if model.get("reasoning_options"):
        extra["reasoning_options"] = model["reasoning_options"]
    return ModelOffer(
        model_id=model_id,
        name=(model.get("name") or model_id).strip(),
        creator=creator_of(canonical) or _creator_from_id(model_id) or provider_id,
        canonical_id=canonical,
        family=model.get("family"),
        context_length=limit.get("context"),
        max_output=limit.get("output"),
        input_modalities=list(modalities.get("input") or []),
        output_modalities=list(modalities.get("output") or []),
        reasoning=model.get("reasoning"),
        tool_call=model.get("tool_call"),
        open_weights=model.get("open_weights"),
        release_date=model.get("release_date"),
        price=price,
        is_free=price.is_zero() or model_id.endswith((":free", "-free")),
        pricing_source="models.dev",
        deprecated=model.get("status") == "deprecated",
        extra=extra,
    )


def _creator_from_id(model_id: str) -> str | None:
    return model_id.split("/", 1)[0] if "/" in model_id else None


def modelsdev_offers(
    catalog: ModelsDevCatalog, provider_ids: Iterable[str], include_deprecated: bool = False
) -> list[ModelOffer]:
    offers: dict[str, ModelOffer] = {}
    for pid in provider_ids:
        for model in catalog.models(pid).values():
            offer = offer_from_modelsdev(model, pid)
            if offer.deprecated and not include_deprecated:
                continue
            if offer.model_id and offer.model_id not in offers:
                offer.extra["modelsdev_provider"] = pid
                offers[offer.model_id] = offer
    return list(offers.values())


# -- live model lists ------------------------------------------------------------------------


async def openai_model_ids(url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> list[str]:
    """GET an OpenAI-style ``/models`` list and return the ids."""
    data = await get_json(url, headers=headers, params=params)
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    ids: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name")
            if mid:
                ids.append(str(mid))
        elif isinstance(item, str):
            ids.append(item)
    return ids


def overlay_live_ids(
    offers: list[ModelOffer],
    live_ids: Iterable[str],
    catalog: ModelsDevCatalog | None = None,
    *,
    drop_missing: bool = True,
    id_transform: Any = None,
) -> list[ModelOffer]:
    """Reconcile catalogue offers with the provider's live model list.

    Catalogue models absent from the live list are dropped (``drop_missing``); live models the
    catalogue doesn't know are added without prices so they still show up (and can be tested).
    """
    live = [id_transform(i) if id_transform else i for i in live_ids]
    live_set = set(live)
    by_id = {o.model_id: o for o in offers}
    result = [o for o in offers if o.model_id in live_set] if drop_missing and live_set else list(offers)
    for mid in live:
        if mid in by_id:
            by_id[mid].extra["live"] = True
            continue
        canonical = catalog.guess_canonical(mid) if catalog else None
        result.append(
            ModelOffer(
                model_id=mid,
                name=mid.split("/")[-1],
                creator=creator_of(canonical) or _creator_from_id(mid),
                canonical_id=canonical,
                pricing_source="api (no price)",
                extra={"live": True},
            )
        )
    return result


def apply_price(
    offer: ModelOffer,
    price: Price,
    source: str,
    *,
    list_price: Price | None = None,
    discount_note: str | None = None,
    discount_until: str | None = None,
) -> None:
    """Set an offer's live price, deriving discount fields when a list price is given."""
    offer.price = price
    offer.pricing_source = source
    offer.is_free = price.is_zero()
    if list_price and list_price.known() and price.known():
        base = (list_price.input or 0) + (list_price.output or 0)
        now = (price.input or 0) + (price.output or 0)
        if base > 0 and now < base * 0.995:
            offer.list_price = list_price
            offer.discount = round(1 - now / base, 4)
            offer.discount_note = discount_note
            offer.discount_until = discount_until


def find_offer(offers: list[ModelOffer], name_or_id: str) -> ModelOffer | None:
    """Match a pricing-page row label to an offer by id or normalised name."""
    key = normalize_model_key(name_or_id)
    for o in offers:
        if o.model_id == name_or_id or normalize_model_key(o.model_id) == key:
            return o
    for o in offers:
        if normalize_model_key(o.name) == key:
            return o
    return None


# -- page parsing ----------------------------------------------------------------------------

_MONEY = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")


def parse_money(text: str | None) -> float | None:
    """Parse '$1.25 / MTok', '$0.15', 'Free', 'Free of charge' → float (USD)."""
    if text is None:
        return None
    t = text.strip()
    if not t or t in {"-", "—", "–", "n/a", "N/A"}:
        return None
    if t.lower().startswith("free"):
        return 0.0
    m = _MONEY.search(t)
    if m:
        return float(m.group(1))
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def parse_markdown_tables(markdown: str) -> list[list[dict[str, str]]]:
    """Return every GitHub-style pipe table as a list of row dicts keyed by header text."""
    tables: list[list[dict[str, str]]] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines) - 1:
        line, nxt = lines[i].strip(), lines[i + 1].strip()
        if line.startswith("|") and re.match(r"^\|?\s*:?-{2,}", nxt):
            headers = _split_row(line)
            rows: list[dict[str, str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i].strip())
                rows.append({h: (cells[j] if j < len(cells) else "") for j, h in enumerate(headers)})
                i += 1
            tables.append(rows)
        else:
            i += 1
    return tables


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", inner)
    return [strip_markdown(c) for c in cells]


def strip_markdown(text: str) -> str:
    t = re.sub(r"<br\s*/?>", " ", text)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # links
    t = re.sub(r"[*_`]+", "", t)
    return " ".join(t.split()).strip()
