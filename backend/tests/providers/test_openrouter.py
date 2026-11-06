"""OpenRouter: which endpoint's discount a model row shows.

The numbers echo what the live API returned on 2026-11-03 for DeepSeek V4.1 Flash, trimmed to the few
endpoints that matter. Prices here are USD per 1M tokens; the API sends them per token.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.presets import choose_endpoints
from vibonomics.pricing import Task
from vibonomics.providers.base import FetchContext, Price, ResolvedProvider
from vibonomics.providers.openrouter import API, OpenRouterAdapter, pick_discounted_endpoint
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


async def offer_for(
    model: dict, endpoints: list[dict], *, lookup: httpx.Response | None = None, settings: AppSettings | None = None
):
    ctx = FetchContext(catalog=ModelsDevCatalog({}), settings=settings or AppSettings())
    cfg = ResolvedProvider(
        id=1, type="openrouter", name="OpenRouter", api_key=None, mgmt_key=None, base_url=None, options={}
    )
    with respx.mock:
        respx.get(f"{API}/models").mock(return_value=httpx.Response(200, json={"data": [model]}))
        respx.get(f"{API}/models/{model['id']}/endpoints").mock(
            return_value=lookup or httpx.Response(200, json={"data": {"endpoints": endpoints}})
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


@pytest.mark.asyncio
async def test_the_offer_says_nothing_about_endpoints_unless_the_lookup_answered():
    # persist_offers keeps the stored endpoints only when the offer has no "endpoints" key, so the adapter has to
    # tell "the lookup failed or was off" (no key) from "it answered with none" (an empty list).
    model = listing("vendor/model", 0.10, 0.40)
    failed = await offer_for(model, [], lookup=httpx.Response(500, text="down"))
    assert "endpoints" not in failed.extra

    off = AppSettings()
    off.refresh.openrouter_endpoint_discounts = False
    skipped = await offer_for(model, [endpoint("Plain", 0.10, 0.40)], settings=off)
    assert "endpoints" not in skipped.extra

    answered = await offer_for(model, [])
    assert answered.extra["endpoints"] == []


# ---- free and stealth models ------------------------------------------------------------------------------------
# The public API lists endpoints for them too (2026-11-06): stealth/crouton-alpha has one, tag "stealth", provider
# "Stealth", quantization "unknown"; dots-studio/dots-3-note-preview:free has one, tag "atlas-cloud/fp8", provider
# "AtlasCloud". Both are priced "0". The preset needs them to pin `provider.only`.

TASK = Task(600_000, 20_000, 0.0)


def stealth_endpoint() -> dict:
    return {**endpoint("Stealth", 0.0, 0.0), "quantization": "unknown"}


def atlas_endpoint(discount: float = 0.0) -> dict:
    return {**endpoint("AtlasCloud", 0.0, 0.0, discount=discount), "tag": "atlas-cloud/fp8"}


async def offers_for(models: dict[str, list[dict]], *, failing: frozenset[str] = frozenset()):
    """Run fetch_models over several listed models; returns the offers by id and the /endpoints route per id."""
    listed = [
        listing(mid, 0.0, 0.0) if mid.endswith(":free") or "stealth" in mid else listing(mid, 0.1, 0.4)
        for mid in models
    ]
    ctx = FetchContext(catalog=ModelsDevCatalog({}), settings=AppSettings())
    cfg = ResolvedProvider(
        id=1, type="openrouter", name="OpenRouter", api_key=None, mgmt_key=None, base_url=None, options={}
    )
    with respx.mock:
        respx.get(f"{API}/models").mock(return_value=httpx.Response(200, json={"data": listed}))
        routes = {}
        for mid, endpoints in models.items():
            response = (
                httpx.Response(500, text="down")
                if mid in failing
                else httpx.Response(200, json={"data": {"endpoints": endpoints}})
            )
            routes[mid] = respx.get(f"{API}/models/{mid}/endpoints").mock(return_value=response)
        offers = await OpenRouterAdapter(cfg, ctx).fetch_models()
    return {o.model_id: o for o in offers}, routes


@pytest.mark.asyncio
async def test_a_stealth_model_gets_its_endpoint_with_zero_prices_kept_as_zero():
    offer = await offer_for(listing("stealth/crouton-alpha", 0.0, 0.0), [stealth_endpoint()])
    assert offer.is_free
    (ep,) = offer.extra["endpoints"]
    assert (ep["tag"], ep["provider"], ep["quantization"]) == ("stealth", "Stealth", "unknown")
    # Zero is a price here, not a gap: choose_endpoints skips an endpoint whose input or output is None.
    assert ep["input"] == 0.0 and ep["input"] is not None
    assert ep["output"] == 0.0 and ep["output"] is not None
    assert "cache_read" in ep and ep["cache_read"] is None
    assert ep["discount"] == 0.0


@pytest.mark.asyncio
async def test_a_model_with_free_in_its_id_gets_its_endpoint_too():
    offer = await offer_for(listing("dots-studio/dots-3-note-preview:free", 0.0, 0.0), [atlas_endpoint()])
    (ep,) = offer.extra["endpoints"]
    assert (ep["tag"], ep["provider"], ep["quantization"]) == ("atlas-cloud/fp8", "AtlasCloud", "fp8")
    assert (ep["input"], ep["output"]) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_looking_up_a_free_models_endpoints_does_not_change_its_price_or_give_it_a_discount():
    # A zero price discounted by a percentage is still zero, and a model that is free must not come out priced.
    offer = await offer_for(listing("dots-studio/dots-3-note-preview:free", 0.0, 0.0), [atlas_endpoint(0.3)])
    assert offer.is_free
    assert (offer.price.input, offer.price.output) == (0.0, 0.0)
    assert offer.discount is None and offer.list_price is None and offer.discount_note is None
    assert len(offer.extra["endpoints"]) == 1

    # Same for a model that is free by its id while an endpoint of it is cheaper than its listed price and discounted:
    # the lookup must not turn it into a priced model.
    priced = {**endpoint("Paid", 0.05, 0.2, discount=0.3), "tag": "paid/fp8"}
    offer = await offer_for(listing("vendor/odd:free", 0.1, 0.4), [priced])
    assert offer.is_free
    assert (offer.price.input, offer.price.output) == (0.1, 0.4)
    assert offer.discount is None and offer.list_price is None and offer.discount_note is None
    assert offer.extra["endpoints"][0]["input"] == 0.05  # the data is kept; only the listed price is untouched


def test_a_zero_listed_price_has_nothing_to_discount():
    zero = {"provider": "Free", "tag": "free", "input": 0.0, "output": 0.0, "cache_read": None, "discount": 0.3}
    priced = {"provider": "Paid", "tag": "paid", "input": 0.2, "output": 0.8, "cache_read": None, "discount": 0.3}
    assert pick_discounted_endpoint(Price(input=0.0, output=0.0), [zero], TASK) is None
    assert pick_discounted_endpoint(Price(input=0.0, output=0.0), [zero, priced], TASK) is None
    # An endpoint at no cost is not a discount off anything, on a priced model either.
    assert pick_discounted_endpoint(Price(input=0.2, output=0.8), [zero], TASK) is None


@pytest.mark.asyncio
async def test_every_model_is_looked_up_once_free_ones_included_and_a_failed_free_lookup_is_counted(caplog):
    caplog.set_level(logging.INFO, logger="vibonomics.providers.openrouter")
    offers, routes = await offers_for(
        {
            "vendor/paid": [endpoint("Plain", 0.1, 0.4)],
            "vendor/other:free": [atlas_endpoint()],
            "stealth/crouton-alpha": [stealth_endpoint()],
            "vendor/down:free": [],
        },
        failing={"vendor/down:free"},
    )
    assert {mid: route.call_count for mid, route in routes.items()} == {
        "vendor/paid": 1,
        "vendor/other:free": 1,
        "stealth/crouton-alpha": 1,
        "vendor/down:free": 2,  # one try and the one retry
    }
    assert offers["vendor/other:free"].extra["endpoints"][0]["tag"] == "atlas-cloud/fp8"
    assert offers["stealth/crouton-alpha"].extra["endpoints"][0]["tag"] == "stealth"
    assert "endpoints" not in offers["vendor/down:free"].extra
    assert "1 endpoint lookups failed" in caplog.text


@pytest.mark.asyncio
async def test_a_stealth_endpoint_with_zero_prices_is_one_the_preset_can_pin():
    offer = await offer_for(listing("stealth/crouton-alpha", 0.0, 0.0), [stealth_endpoint()])
    chosen = choose_endpoints(offer.extra["endpoints"], TASK, {}, 3)
    assert [(e["tag"], e["provider"], e["input"], e["output"], e["task_cost"]) for e in chosen] == [
        ("stealth", "Stealth", 0.0, 0.0, 0.0)
    ]
    # ... and a preset that ignores or allows providers still sees it by tag, slug and name.
    assert choose_endpoints(offer.extra["endpoints"], TASK, {"ignore": ["Stealth"]}, 3) == []
    assert len(choose_endpoints(offer.extra["endpoints"], TASK, {}, 3, allow=["stealth"])) == 1
