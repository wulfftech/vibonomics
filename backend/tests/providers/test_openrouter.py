"""OpenRouter: which endpoint's discount a model row shows.

The numbers echo what the live API returned on 2026-11-03 for DeepSeek V4.1 Flash, trimmed to the few
endpoints that matter. Prices here are USD per 1M tokens; the API sends them per token.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.providers.base import FetchContext, ResolvedProvider
from vibonomics.providers.openrouter import API, OpenRouterAdapter
from vibonomics.settings_store import AppSettings


def per_token(per_million: float | None) -> str | None:
    return None if per_million is None else repr(per_million / 1_000_000)


def listing(model_id: str, input_: float, output: float, cache_read: float | None = None) -> dict:
    return {
        "id": model_id,
        "name": f"Vendor: {model_id}",
        "context_length": 128_000,
        "created": 1_760_000_000,
        "pricing": {
            "prompt": per_token(input_),
            "completion": per_token(output),
            **({"input_cache_read": per_token(cache_read)} if cache_read is not None else {}),
        },
    }


def endpoint(
    provider: str, input_: float, output: float, discount: float = 0.0, cache_read: float | None = None
) -> dict:
    return {
        "provider_name": provider,
        "tag": provider.lower(),
        "quantization": "fp8",
        "uptime_last_1d": 99.0,
        "pricing": {
            "prompt": per_token(input_),
            "completion": per_token(output),
            "discount": discount,
            **({"input_cache_read": per_token(cache_read)} if cache_read is not None else {}),
        },
    }


async def offer_for(model: dict, endpoints: list[dict]):
    ctx = FetchContext(catalog=ModelsDevCatalog({}), settings=AppSettings())
    cfg = ResolvedProvider(
        id=1, type="openrouter", name="OpenRouter", api_key=None, mgmt_key=None, base_url=None, options={}
    )
    with respx.mock:
        respx.get(f"{API}/models").mock(return_value=httpx.Response(200, json={"data": [model]}))
        respx.get(f"{API}/models/{model['id']}/endpoints").mock(
            return_value=httpx.Response(200, json={"data": {"endpoints": endpoints}})
        )
        (offer,) = await OpenRouterAdapter(cfg, ctx).fetch_models()
    return offer


@pytest.mark.asyncio
async def test_discounts_that_do_not_beat_the_listed_price_are_left_out():
    # OpenRouter lists Wafer's $0.045 / $0.50. StreamLake is 51% off, but off a price that is still dearer.
    offer = await offer_for(
        listing("deepseek/deepseek-v4.1-flash", 0.045, 0.5, cache_read=0.012),
        [
            endpoint("Wafer", 0.045, 0.5),
            endpoint("DeepInfra", 0.112, 0.336, discount=0.3),
            endpoint("StreamLake", 0.147, 0.588, discount=0.51),
        ],
    )
    assert (offer.price.input, offer.price.output) == (0.045, 0.5)
    assert offer.discount is None
    assert offer.list_price is None
    assert [e["provider"] for e in offer.extra["endpoints"]] == ["Wafer", "DeepInfra", "StreamLake"]


@pytest.mark.asyncio
async def test_the_cheapest_discounted_endpoint_wins_not_the_biggest_percentage():
    offer = await offer_for(
        listing("vendor/model", 0.30, 1.20),
        [
            endpoint("Big", 0.20, 1.00, discount=0.5),
            endpoint("Cheap", 0.14, 0.42, discount=0.3),
            endpoint("Plain", 0.30, 1.20),
        ],
    )
    assert (offer.price.input, offer.price.output) == (0.14, 0.42)
    assert offer.discount == 0.3
    assert (offer.list_price.input, offer.list_price.output) == (0.2, 0.6)
    assert offer.discount_note == "30% off via Cheap; 1 other provider discounted too"


@pytest.mark.asyncio
async def test_the_listed_endpoint_may_be_the_discounted_one():
    offer = await offer_for(
        listing("vendor/model", 0.14, 0.42),
        [endpoint("Cheap", 0.14, 0.42, discount=0.3), endpoint("Plain", 0.30, 1.20)],
    )
    assert (offer.price.input, offer.price.output) == (0.14, 0.42)
    assert offer.discount == 0.3
    assert offer.discount_note == "30% off via Cheap"


@pytest.mark.asyncio
async def test_each_endpoint_keeps_its_own_cache_read_price_in_the_unit_of_its_input_and_output():
    # The preset costs endpoints for a cache-heavy task, so an endpoint that caches must not be costed with the
    # model's one listed cache price, and one that does not cache must not borrow it.
    offer = await offer_for(
        listing("vendor/model", 0.10, 0.40, cache_read=0.01),
        [endpoint("Caches", 0.10, 0.40, cache_read=0.02), endpoint("Plain", 0.08, 0.40)],
    )
    by_provider = {e["provider"]: e for e in offer.extra["endpoints"]}
    assert by_provider["Caches"]["cache_read"] == 0.02
    assert by_provider["Caches"]["input"] == 0.10  # same per-1M unit
    assert by_provider["Plain"]["cache_read"] is None
