"""Parsing for provider pricing pages.

Most providers publish prices only on a docs page. Several now serve markdown (a ``.md`` URL or an
``Accept: text/markdown`` header), which makes their tables easy to read; DeepSeek still serves an
HTML table. Promotions show up either as a strikethrough list price (``~~$4.00~~ $2.00``), a
"50% off" note, or prose with dates ("$2.00 through November 30, 2026. $4.00 starting December 1,
2026."). All three are understood here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..catalog.modelsdev import normalize_model_key
from .base import ModelOffer, Price
from .common import parse_money, strip_markdown

_PROMO = re.compile(
    r"\$\s*(?P<now>\d+(?:\.\d+)?)\s+(?:through|until)\s+(?P<date>[A-Z][a-z]+ \d{1,2},? \d{4})"
    r".*?\$\s*(?P<later>\d+(?:\.\d+)?)\s+starting",
    re.I | re.S,
)
_PROMO_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*off", re.I)
_STRIKE = re.compile(r"~~\s*\$?\s*(?P<before>\d+(?:\.\d+)?)\s*~~\s*\$?\s*(?P<after>\d+(?:\.\d+)?)")
_PARENS = re.compile(r"\s*\([^()]*\)")
_FREE_WORDS = {"free", "free of charge", "$0", "0", "0.00"}
_UNAVAILABLE = {"not available", "-", "—", "–", "", "n/a", "contact sales", "contact us", "enterprise"}


def parse_date(text: str) -> str | None:
    cleaned = text.replace(",", "").replace("  ", " ").strip()
    for fmt in ("%B %d %Y", "%b %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_promo(text: str | None) -> tuple[float | None, float | None, str | None, str | None]:
    """Return (current, list, until, note) for a promo cell/prose, or (price, None, None, None)."""
    if text is None:
        return None, None, None, None
    m = _PROMO.search(text)
    if m:
        current, later = float(m.group("now")), float(m.group("later"))
        until = parse_date(m.group("date"))
        pct = round((1 - current / later) * 100) if later else None
        note = f"{pct}% off" if pct else "Promotional price"
        if until:
            note += f" until {until}"
        return current, later, until, note
    return parse_money(text), None, None, None


def parse_price_cell(cell: str | None) -> tuple[float | None, float | None, str | None, str | None]:
    """Return (current, list, until, note) for a cell like '~~$4.00~~ $2.00 (50% off)'."""
    if cell is None:
        return None, None, None, None
    text = strip_markdown(cell)
    strike = _STRIKE.search(text)
    if strike:
        before, after = float(strike.group("before")), float(strike.group("after"))
        pct = _PROMO_PCT.search(text)
        note = (
            f"{float(pct.group(1)):g}% off"
            if pct
            else (f"{round((1 - after / before) * 100)}% off" if before else None)
        )
        return after, before, None, note
    current, listed, until, note = parse_promo(text)
    if current is not None and note is None:
        pct = _PROMO_PCT.search(text)
        if pct and current > 0:
            note = f"{float(pct.group(1)):g}% off"
    return current, listed, until, note


def clean_model_name(name: str) -> str:
    s = strip_markdown(name)
    s = _PARENS.sub("", s)
    s = _PROMO_PCT.sub("", s)
    return " ".join(s.split()).strip(" -–—")


def name_variants(name: str) -> list[str]:
    """Match candidates for a pricing-table row label, most specific first."""
    base = clean_model_name(name)
    variants = [base, strip_markdown(name)]
    for suffix in (" free", " (free)", " off-peak", " peak"):
        if base.lower().endswith(suffix):
            variants.append(base[: -len(suffix)].strip())
    if " / " in base:
        variants.append(base.split(" / ")[0].strip())
    return [v for i, v in enumerate(variants) if v and v not in variants[:i]]


@dataclass
class PagePrice:
    """One row of a provider pricing page."""

    name: str
    model_id: str | None = None
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    list_input: float | None = None
    list_output: float | None = None
    discount_note: str | None = None
    discount_until: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def known(self) -> bool:
        return self.input is not None or self.output is not None


def _offer_index(offers: list[ModelOffer]) -> dict[str, ModelOffer]:
    index: dict[str, ModelOffer] = {}
    for offer in offers:
        for key in (offer.model_id, offer.name):
            if key:
                index.setdefault(normalize_model_key(key), offer)
        if offer.canonical_id:
            index.setdefault(normalize_model_key(offer.canonical_id.split("/")[-1]), offer)
    return index


def _exact_key(value: str) -> str:
    """A model id as written, with case and separators folded; unlike normalize_model_key, dates count."""
    return re.sub(r"[\s._-]+", "-", value.lower().strip().split("/")[-1]).strip("-")


def _exact_offer(offers: list[ModelOffer], row: PagePrice) -> ModelOffer | None:
    wanted = {_exact_key(value) for value in (row.model_id, row.name) if value}
    return next((offer for offer in offers if _exact_key(offer.model_id) in wanted), None)


def match_offer(offers: list[ModelOffer], row: PagePrice) -> ModelOffer | None:
    return _exact_offer(offers, row) or _loose_offer(offers, row)


def _loose_offer(offers: list[ModelOffer], row: PagePrice) -> ModelOffer | None:
    if row.model_id:
        key = normalize_model_key(row.model_id)
        for offer in offers:
            if normalize_model_key(offer.model_id) == key:
                return offer
    index = _offer_index(offers)
    for variant in name_variants(row.name):
        offer = index.get(normalize_model_key(variant))
        if offer is not None:
            return offer
    return None


def apply_page_prices(offers: list[ModelOffer], rows: list[PagePrice], source: str) -> int:
    """Overlay page prices onto matching offers. Returns how many offers were updated."""
    applied = 0
    matches = [(row, _exact_offer(offers, row)) for row in rows if row.known()]
    named = {id(offer) for _row, offer in matches if offer is not None}
    for row, exact in matches:
        offer = exact or _loose_offer(offers, row)
        # A loose match drops dates and versions, so "gpt-4o-2024-05-13" finds "gpt-4o". It must never
        # replace the price of an offer the page also lists under its own name.
        if offer is None or (exact is None and id(offer) in named):
            continue
        offer.price = Price(input=row.input, output=row.output, cache_read=row.cache_read, cache_write=row.cache_write)
        offer.pricing_source = source
        offer.is_free = row.input == 0 and row.output == 0
        if row.extra.get("context_length"):
            offer.context_length = int(row.extra["context_length"])
        if row.extra.get("max_output"):
            offer.max_output = int(row.extra["max_output"])
        if row.list_input is not None or row.list_output is not None:
            list_price = Price(
                input=row.list_input if row.list_input is not None else row.input,
                output=row.list_output if row.list_output is not None else row.output,
            )
            base = (list_price.input or 0) + (list_price.output or 0)
            now = (row.input or 0) + (row.output or 0)
            if base > 0 and now < base:
                offer.list_price = list_price
                offer.discount = round(1 - now / base, 4)
                offer.discount_note = row.discount_note or f"{round((1 - now / base) * 100)}% off"
                offer.discount_until = row.discount_until
        elif row.discount_note:
            offer.discount_note = row.discount_note
            offer.discount_until = row.discount_until
        if row.extra:
            offer.extra.update({k: v for k, v in row.extra.items() if v is not None})
        applied += 1
    return applied


# -- table helpers ---------------------------------------------------------------------------


def find_column(headers: list[str], *needles: str, exclude: tuple[str, ...] = ()) -> int | None:
    for i, header in enumerate(headers):
        h = header.lower()
        if any(n in h for n in needles) and not any(x in h for x in exclude):
            return i
    return None


def cell(row: list[str], index: int | None) -> str | None:
    if index is None or index >= len(row):
        return None
    return row[index]


def price_from_cell(text: str | None) -> PagePrice | None:
    current, listed, until, note = parse_price_cell(text)
    if current is None and listed is None:
        return None
    return PagePrice(name="", input=current, list_input=listed, discount_note=note, discount_until=until)


def is_unavailable(text: str | None) -> bool:
    if text is None:
        return True
    return strip_markdown(text).strip().lower() in _UNAVAILABLE


def is_free_text(text: str | None) -> bool:
    if text is None:
        return False
    return strip_markdown(text).strip().lower() in _FREE_WORDS
