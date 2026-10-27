"""Groq: prices from the public docs model table, live model list with a key, no balance API."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..http import FetchError, get_json, get_text
from .base import ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import apply_price, modelsdev_offers, offer_from_modelsdev, overlay_live_ids, strip_markdown

log = logging.getLogger(__name__)

API = "https://api.groq.com/openai/v1"
DOCS_MODELS = "https://console.groq.com/docs/models.md"
_MD = "groq"
_MIN_DOC_ROWS = 5  # below this the docs table probably changed shape; don't treat it as the model list

_LINK_ID = re.compile(r"\]\(/docs/model/([^)\s]+)\)")
_IN = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*input", re.I)
_OUT = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*output", re.I)
_CACHED = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*cached", re.I)
_INT = re.compile(r"[0-9][0-9,]*")


@dataclass
class DocRow:
    model_id: str
    name: str
    section: str
    price_text: str
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    context: int | None = None
    max_output: int | None = None
    flags: dict[str, Any] = field(default_factory=dict)


def _split_raw(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _int(text: str) -> int | None:
    m = _INT.search(text or "")
    return int(m.group(0).replace(",", "")) if m else None


def parse_docs(markdown: str) -> list[DocRow]:
    """Parse the model tables on Groq's models page.

    Cells are mangled by the markdown export ("$0.15 input$0.60 output", "Enterprisellama-3.1-8b-instant"),
    so this works on raw rows and takes the model id from the ``/docs/model/<id>`` link.
    """
    rows: list[DocRow] = []
    section = ""
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            section = strip_markdown(line.lstrip("#"))
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            headers = [strip_markdown(h).upper() for h in _split_raw(line)]
            col = {h: n for n, h in enumerate(headers)}
            id_col = next((n for h, n in col.items() if "MODEL" in h), 0)
            price_col = next((n for h, n in col.items() if "PRICE" in h), None)
            ctx_col = next((n for h, n in col.items() if "CONTEXT" in h), None)
            out_col = next((n for h, n in col.items() if "COMPLETION" in h), None)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_raw(lines[i])
                i += 1
                if price_col is None or len(cells) <= price_col:
                    continue
                raw_id = cells[id_col]
                m = _LINK_ID.search(raw_id)
                model_id = m.group(1) if m else strip_markdown(raw_id).split(")")[-1].removeprefix("Enterprise")
                if not model_id:
                    continue
                name_m = re.search(r"\)([^\[\]]+)\]\(/docs/model/", raw_id)
                row = DocRow(
                    model_id=model_id.strip(),
                    name=(name_m.group(1).strip() if name_m else model_id),
                    section=section,
                    price_text=strip_markdown(cells[price_col]),
                )
                if "Enterprise" in raw_id.rsplit(")", 1)[-1]:
                    row.flags["enterprise"] = True
                pt = row.price_text
                if (mi := _IN.search(pt)) and (mo := _OUT.search(pt)):
                    row.input, row.output = float(mi.group(1)), float(mo.group(1))
                    if mc := _CACHED.search(pt):
                        row.cache_read = float(mc.group(1))
                elif "contact" in pt.lower():
                    row.flags["contact_sales"] = True
                elif pt.lower().startswith("free"):
                    row.input = row.output = 0.0
                elif "$" in pt:
                    row.flags["unit_price"] = pt  # per hour / per 1M characters: not token pricing
                if ctx_col is not None and ctx_col < len(cells):
                    row.context = _int(cells[ctx_col])
                if out_col is not None and out_col < len(cells):
                    row.max_output = _int(cells[out_col])
                rows.append(row)
            continue
        i += 1
    return rows


class GroqAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="groq",
        name="Groq",
        description="Very fast inference on Groq LPUs. Prices come from Groq's public model docs; "
        "a key adds the live model list and context limits.",
        homepage="https://groq.com",
        default_base_url=API,
        modelsdev_ids=(_MD,),
        keys_url="https://console.groq.com/keys",
        billing_url="https://console.groq.com/settings/billing",
        balance="none",
        balance_note="Groq has no balance or spend API; usage is billed in arrears. See the console billing page.",
        aider_prefix="groq",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        catalog = self.ctx.catalog
        # Deprecated catalogue entries are kept only if the docs or the live list still show them.
        offers = modelsdev_offers(catalog, self.spec.modelsdev_ids, include_deprecated=True)
        rows: list[DocRow] = []
        page_error: str | None = None
        try:
            rows = parse_docs(await get_text(DOCS_MODELS, timeout=30))
            if not any(r.input is not None for r in rows):
                raise ValueError("no token prices found in the models table")
        except (FetchError, ValueError) as exc:
            page_error = str(exc)[:300]
            log.warning("Groq pricing page unusable, keeping models.dev prices: %s", page_error)
            rows = []

        # The docs table lists every production/preview model, so it doubles as the public model list.
        by_id = {o.model_id: o for o in offers}
        for row in rows:
            offer = by_id.get(row.model_id)
            if offer is None:
                md = catalog.model(_MD, row.model_id)
                offer = (
                    offer_from_modelsdev(md, _MD)
                    if md
                    else ModelOffer(
                        model_id=row.model_id,
                        name=row.name,
                        creator=row.model_id.split("/", 1)[0] if "/" in row.model_id else None,
                        canonical_id=catalog.guess_canonical(row.model_id, row.name),
                        pricing_source="groq docs (no price)",
                    )
                )
                offers.append(offer)
                by_id[offer.model_id] = offer
            offer.deprecated = "deprecated" in row.section.lower()
            offer.extra["listed_in_docs"] = row.section or True

        live: list[dict[str, Any]] = []
        if self.cfg.api_key:
            data = await get_json(f"{self.base_url}/models", headers=self.auth_headers())
            live = [m for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
            live = [m for m in live if m.get("active", True) is not False]
        if live:
            offers = overlay_live_ids(offers, [m["id"] for m in live], catalog)
            ctx = {m["id"]: m for m in live}
            for o in offers:
                m = ctx.get(o.model_id)
                if m:
                    o.context_length = m.get("context_window") or o.context_length
                    o.max_output = m.get("max_completion_tokens") or o.max_output
        elif len(rows) >= _MIN_DOC_ROWS:
            listed = {r.model_id for r in rows}
            offers = [o for o in offers if o.model_id in listed]
        offers = [o for o in offers if not o.deprecated or o.extra.get("live") or o.extra.get("listed_in_docs")]

        self._apply_docs(offers, rows, page_error)
        return offers

    def _apply_docs(self, offers: list[ModelOffer], rows: list[DocRow], page_error: str | None) -> None:
        by_id = {r.model_id: r for r in rows}
        for o in offers:
            if page_error:
                o.extra["pricing_page_error"] = page_error
            row = by_id.get(o.model_id)
            if not row:
                continue
            o.context_length = o.context_length or row.context
            o.max_output = o.max_output or row.max_output
            o.extra.update(row.flags)
            if row.flags.get("contact_sales"):
                # No public rate any more; a stale catalogue price would be misleading.
                o.extra["pricing_note"] = "Groq lists this model as Enterprise / contact sales."
                o.extra["catalogue_price"] = {"input": o.price.input, "output": o.price.output}
                o.price, o.is_free, o.pricing_source = Price(), False, "groq docs (contact sales)"
            if row.input is None or row.output is None:
                continue
            cache_read = row.cache_read
            if cache_read is None and o.price.input == row.input:
                cache_read = o.price.cache_read  # same base rate: catalogue cache rate still applies
            apply_price(
                o,
                Price(input=row.input, output=row.output, cache_read=cache_read, cache_write=o.price.cache_write),
                "groq docs",
            )
