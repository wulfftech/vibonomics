"""Cerebras: public model list with live per-token prices; no balance API."""

from __future__ import annotations

import logging
from typing import Any

from ..catalog.modelsdev import creator_of
from ..http import FetchError, get_json
from .base import ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import _f, modelsdev_offers

log = logging.getLogger(__name__)

API = "https://api.cerebras.ai/v1"
PUBLIC_MODELS = "https://api.cerebras.ai/public/v1/models"
_MD = "cerebras"


def _per_million(value: Any) -> float | None:
    v = _f(value)
    if v is None or v < 0:
        return None
    return round(v * 1_000_000, 6)


class CerebrasAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="cerebras",
        name="Cerebras",
        description="Wafer-scale inference with very high output speed. The model list and prices are public; "
        "a key is only needed to run evals.",
        homepage="https://www.cerebras.ai/inference",
        default_base_url=API,
        modelsdev_ids=(_MD,),
        keys_url="https://cloud.cerebras.ai",
        billing_url="https://cloud.cerebras.ai",
        balance="none",
        balance_note="Cerebras shows the credit balance only in the Cloud console; there is no API for it.",
        aider_prefix="cerebras",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        try:
            data = await get_json(PUBLIC_MODELS, timeout=30)
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                raise FetchError(f"{PUBLIC_MODELS}: unexpected response shape")
        except FetchError as exc:
            log.warning("Cerebras public model list failed, using models.dev: %s", exc)
            offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
            for o in offers:
                o.extra["pricing_page_error"] = str(exc)[:300]
            return offers
        offers = [o for m in items if isinstance(m, dict) and (o := self._offer(m))]
        return offers

    def _offer(self, m: dict[str, Any]) -> ModelOffer | None:
        mid = m.get("id")
        if not mid:
            return None
        catalog = self.ctx.catalog
        md = catalog.model(_MD, mid) or {}
        hf = m.get("hugging_face_id")
        canonical = catalog.canonical_for(_MD, mid) or (catalog.guess_canonical(hf) if hf else None)
        canonical = canonical or catalog.guess_canonical(mid, m.get("name"))
        pricing = m.get("pricing") or {}
        md_cost = md.get("cost") or {}
        price = Price(
            input=_per_million(pricing.get("prompt")),
            output=_per_million(pricing.get("completion")),
            # The public API has no cache rates; keep the catalogue's when the base rate matches.
            cache_read=None,
            cache_write=None,
        )
        if price.input is not None and _f(md_cost.get("input")) == price.input:
            price.cache_read = _f(md_cost.get("cache_read"))
            price.cache_write = _f(md_cost.get("cache_write"))
        caps = m.get("capabilities") or {}
        limits = m.get("limits") or {}
        modalities = (md.get("modalities") or {}).get("input") or ["text"]
        if caps.get("vision") and "image" not in modalities:
            modalities = [*modalities, "image"]
        known = price.known()
        return ModelOffer(
            model_id=mid,
            name=md.get("name") or m.get("name") or mid,
            creator=creator_of(canonical) or (hf.split("/", 1)[0].lower() if hf and "/" in hf else None),
            canonical_id=canonical,
            family=md.get("family"),
            context_length=limits.get("max_context_length") or (md.get("limit") or {}).get("context"),
            max_output=limits.get("max_completion_tokens") or (md.get("limit") or {}).get("output"),
            input_modalities=list(modalities),
            output_modalities=list((md.get("modalities") or {}).get("output") or ["text"]),
            reasoning=caps.get("reasoning", md.get("reasoning")),
            tool_call=caps.get("tools", caps.get("function_calling", md.get("tool_call"))),
            open_weights=md.get("open_weights", True if hf else None),
            release_date=md.get("release_date"),
            price=price,
            is_free=price.is_zero(),
            pricing_source="cerebras api" if known else "cerebras api (no price)",
            deprecated=bool(m.get("deprecated")),
            extra={
                "hugging_face_id": hf,
                "quantization": m.get("quantization"),
                "preview": m.get("preview"),
                "description": m.get("description"),
                "live": True,
            },
        )
