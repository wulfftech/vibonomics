"""Any OpenAI-compatible endpoint (LiteLLM proxy, vLLM, Ollama, LM Studio, a company gateway...)."""

from __future__ import annotations

from ..http import get_json
from .base import ModelOffer, OptionField, Price, ProviderAdapter, ProviderSpec
from .common import _f, overlay_live_ids


class CustomOpenAIAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="custom-openai",
        name="Custom (OpenAI-compatible)",
        description="Any endpoint that speaks the OpenAI chat completions API. Prices come from models.dev "
        "when the model id matches a known model, or from the endpoint if it reports them.",
        homepage="https://platform.openai.com/docs/api-reference/models/list",
        default_base_url="http://localhost:4000/v1",
        custom=True,
        models_require_key=False,
        options=(
            OptionField(
                key="price_source",
                label="Price lookup (models.dev provider id)",
                description="Optional: models.dev provider whose prices apply to these models, e.g. 'openai'.",
                placeholder="openai",
            ),
        ),
    )

    async def fetch_models(self) -> list[ModelOffer]:
        data = await get_json(f"{self.base_url}/models", headers=self.auth_headers())
        items = data.get("data", []) if isinstance(data, dict) else []
        price_pid = (self.cfg.options or {}).get("price_source")
        offers: list[ModelOffer] = []
        known = self.ctx.catalog.models(price_pid) if price_pid else {}
        for item in items:
            mid = item.get("id") if isinstance(item, dict) else None
            if not mid:
                continue
            offer = overlay_live_ids([], [mid], self.ctx.catalog)[0]
            md = known.get(mid)
            if md:
                from .common import offer_from_modelsdev

                offer = offer_from_modelsdev(md, price_pid)
                offer.pricing_source = f"models.dev ({price_pid})"
            # LiteLLM-style proxies sometimes expose per-token pricing on the model object.
            pricing = item.get("pricing") if isinstance(item, dict) else None
            if isinstance(pricing, dict) and pricing.get("prompt") is not None:
                p_in, p_out = _f(pricing.get("prompt")), _f(pricing.get("completion"))
                if p_in is not None and p_out is not None:
                    offer.price = Price(input=p_in * 1e6, output=p_out * 1e6)
                    offer.pricing_source = "endpoint"
            offer.extra["live"] = True
            offers.append(offer)
        return offers
