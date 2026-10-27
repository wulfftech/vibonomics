"""NVIDIA NIM on build.nvidia.com: public model list, free rate-limited trial, no billing API."""

from __future__ import annotations

import logging

from ..http import FetchError, request
from .base import ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers, openai_model_ids, overlay_live_ids

log = logging.getLogger(__name__)

API = "https://integrate.api.nvidia.com/v1"
FREE_NOTE = "Free trial on build.nvidia.com, rate-limited; production use needs your own NIM deployment."
# Small, long-lived models used to validate a key with a 1-token completion (the model list is public).
_PROBE_MODELS = ("openai/gpt-oss-20b", "google/gemma-4-31b-it", "nvidia/nemotron-3.5-lightning-30b-a3b")


class NvidiaAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="nvidia",
        name="NVIDIA NIM (build.nvidia.com)",
        description="NVIDIA-hosted NIM endpoints, offered as a free, rate-limited trial. The model list is public; "
        "a key is only needed to run evals.",
        homepage="https://build.nvidia.com",
        default_base_url=API,
        modelsdev_ids=("nvidia",),
        keys_url="https://build.nvidia.com/settings/api-keys",
        balance="none",
        balance_note="The hosted endpoints are a free trial with rate limits; there are no credits or balance.",
        aider_prefix="nvidia_nim",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        # Deprecated catalogue entries survive only while the live list still serves them.
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids, include_deprecated=True)
        try:
            live = await openai_model_ids(f"{self.base_url}/models", {}, None)
        except FetchError as exc:
            log.warning("NVIDIA model list failed, using models.dev only: %s", exc)
            live = []
            for o in offers:
                o.extra["live_list_error"] = str(exc)[:300]
        if live:
            offers = overlay_live_ids(offers, live, self.ctx.catalog)
        offers = [o for o in offers if not o.deprecated or o.extra.get("live")]
        for o in offers:
            o.price = Price(input=0.0, output=0.0, cache_read=0.0 if o.price.cache_read is not None else None)
            o.is_free = True
            o.pricing_source = "nvidia (free trial)"
            o.extra["pricing_note"] = FREE_NOTE
        return offers

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; the public model list still works."
        # /v1/models is public, so it can't validate a key. A 1-token completion can (and is free).
        last = ""
        for model in _PROBE_MODELS:
            try:
                await request(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self.auth_headers(),
                    json_body={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                    timeout=60,
                    retries=0,
                )
                return f"Key accepted (1-token test call to {model})."
            except FetchError as exc:
                msg = str(exc)
                if "HTTP 401" in msg or "HTTP 403" in msg:
                    raise FetchError("NVIDIA rejected the API key (HTTP 401/403).") from exc
                if "HTTP 429" in msg:
                    return "Key accepted (rate-limited right now)."
                last = msg
        raise FetchError(f"Could not validate the key: {last[:200]}")
