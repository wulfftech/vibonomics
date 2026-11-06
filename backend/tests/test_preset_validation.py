"""Checking what a preset is about to write against the listing OpenRouter serves right now."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from vibonomics import http
from vibonomics.preset_validation import FREE_TIER_WARNING, Check, Validation, validate_live
from vibonomics.presets import Candidate

BASE = "https://openrouter.ai/api/v1"
MODEL = "acme/best"
OTHER = "acme/steady"
# (tag, input, output) per million tokens, as the plan stored them
STORED = [("novita/fp4", 0.08, 0.35), ("deepinfra/fp8", 0.10, 0.40)]


def per_token(per_million: float) -> str:
    return repr(per_million / 1_000_000)


def live(tag: str, input_: float = 0.10, output: float = 0.40, **fields: Any) -> dict[str, Any]:
    """An endpoint as the public listing serves it."""
    return {
        "provider_name": tag.split("/")[0].title(),
        "tag": tag,
        "quantization": "fp8",
        "status": 0,
        "uptime_last_30m": 99.5,
        "pricing": {"prompt": per_token(input_), "completion": per_token(output)},
        **fields,
    }


def listed(router: respx.Router, model: str, endpoints: list[dict[str, Any]], **data: Any) -> respx.Route:
    body = {"data": {"id": model, "name": model, "endpoints": endpoints, **data}}
    return router.get(f"{BASE}/models/{model}/endpoints").mock(return_value=httpx.Response(200, json=body))


def live_stored(router: respx.Router, model: str = MODEL, stored=STORED, **data: Any) -> respx.Route:
    """A listing that matches what the plan stored, with one endpoint more that nothing pins."""
    return listed(router, model, [live(t, i, o) for t, i, o in stored] + [live("wafer/bf16", 0.12, 0.45)], **data)


def plan_of(
    models: dict[str, list[tuple[str, float, float]]] | None = None, expires: str | None = None
) -> tuple[dict[str, Any], list[Candidate], list[dict[str, Any]]]:
    """The config, picks and providers of a plan that pins the given tags, each model with the tags it was given."""
    models = models or {MODEL: STORED}
    picks = [
        Candidate(
            model_id=model,
            name=model,
            score=70,
            task_cost=0.1,
            value=700,
            discount=None,
            cache_read=None,
            endpoints=[{"tag": t, "provider": t.split("/")[0], "input": i, "output": o} for t, i, o in eps],
            expires=expires if model == next(iter(models)) else None,
        )
        for model, eps in models.items()
    ]
    providers: dict[str, dict[str, Any]] = {}
    for model, eps in models.items():
        for tag, price_in, price_out in eps:
            row = {"tag": tag, "provider": tag.split("/")[0], "input": price_in, "output": price_out, "models": []}
            providers.setdefault(tag, row)["models"].append(model)
    tags = list(providers)
    config: dict[str, Any] = {"model": next(iter(models)), "provider": {"only": tags, "order": tags}}
    if len(models) > 1:
        config["models"] = list(models)
    return config, picks, list(providers.values())


async def check(*args: Any, **kwargs: Any) -> Validation:
    config, picks, providers = args or plan_of()
    return await validate_live(BASE, config, picks, providers, **kwargs)


def of_level(result: Validation, level: str) -> list[Check]:
    return [item for item in result.items if item.level == level]


@pytest.fixture
def no_waits(monkeypatch):
    """The retry waits of the shared client, which a test of a failing listing would otherwise sit through."""

    real_sleep = asyncio.sleep

    async def instant(_delay: float) -> None:
        await real_sleep(0)  # still a point where other tasks run

    monkeypatch.setattr(http.asyncio, "sleep", instant)


# ---- what is checked --------------------------------------------------------------------------------------------


async def test_a_plan_whose_pins_are_all_live_passes_and_says_what_was_checked():
    with respx.mock(assert_all_called=False) as router:
        route = live_stored(router)
        result = await check()
    assert result.ok is True
    assert [(i.level, i.check, i.model) for i in result.items] == [("ok", "listing", MODEL)]
    assert result.items[0].message == "3 live endpoints, pins verified"
    assert result.checked_at.tzinfo is not None
    assert abs(datetime.now(UTC) - result.checked_at) < timedelta(minutes=1)
    # the listing is public: it is asked for without the key
    assert route.call_count == 1 and "authorization" not in route.calls[0].request.headers


async def test_a_model_the_plan_pins_no_provider_for_is_still_looked_up():
    config = {"model": MODEL}
    picks = plan_of()[1]
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        result = await check(config, picks, [])
    assert result.ok is True
    assert [i.message for i in result.items] == ["3 live endpoints, no providers pinned"]


async def test_every_model_the_config_lists_is_checked_not_only_the_lead():
    config, picks, providers = plan_of({MODEL: STORED, OTHER: [("novita/fp4", 0.08, 0.35), ("slow/fp8", 0.5, 2.0)]})
    assert config["models"] == [MODEL, OTHER]
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        live_stored(router, OTHER, [("novita/fp4", 0.08, 0.35), ("slow/fp8", 0.5, 2.0)])
        result = await check(config, picks, providers)
    assert result.ok is True
    assert [(i.level, i.model) for i in result.items] == [("ok", MODEL), ("ok", OTHER)]


async def test_a_listing_that_does_not_exist_is_an_error():
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models/{MODEL}/endpoints").mock(return_value=httpx.Response(404, json={"error": "no"}))
        result = await check()
    assert result.ok is False
    (error,) = of_level(result, "error")
    assert (error.check, error.model) == ("listing", MODEL)
    assert "no live endpoint" in error.message.lower()


async def test_a_listing_with_no_endpoints_is_an_error():
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [])
        result = await check()
    assert result.ok is False
    assert [i.level for i in result.items] == ["error"]
    assert "no live endpoint" in result.items[0].message.lower()


async def test_one_model_without_a_listing_does_not_hide_the_state_of_the_others():
    config, picks, providers = plan_of({MODEL: STORED, OTHER: [("novita/fp4", 0.08, 0.35)]})
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models/{MODEL}/endpoints").mock(return_value=httpx.Response(404))
        live_stored(router, OTHER, [("novita/fp4", 0.08, 0.35)])
        result = await check(config, picks, providers)
    assert result.ok is False
    assert [(i.level, i.model) for i in result.items] == [("error", MODEL), ("ok", OTHER)]


# ---- the providers the preset pins ------------------------------------------------------------------------------


async def test_a_pinned_provider_that_is_no_longer_listed_is_an_error_that_names_it():
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("deepinfra/fp8"), live("wafer/bf16")])  # novita/fp4 is gone
        result = await check()
    assert result.ok is False
    missing = [i for i in of_level(result, "error") if i.check == "provider"]
    assert [(i.model, i.provider) for i in missing] == [(MODEL, "novita/fp4")]
    assert "novita/fp4" in missing[0].message
    assert not any(i.level == "ok" for i in result.items)  # "pins verified" would not be true


async def test_a_model_whose_pins_are_all_gone_could_not_be_served_and_says_so():
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("wafer/bf16")])
        result = await check()
    assert result.ok is False
    errors = of_level(result, "error")
    assert {i.provider for i in errors if i.check == "provider"} == {"novita/fp4", "deepinfra/fp8"}
    assert [i.model for i in errors if i.check == "pins"] == [MODEL]


async def test_a_pin_is_looked_for_only_where_the_plan_assigned_it():
    # novita/fp4 serves both models in the plan; steady's own pin is slow/fp8, which acme/best does not list
    shared = ("novita/fp4", 0.08, 0.35)
    config, picks, providers = plan_of({MODEL: [shared], OTHER: [shared, ("slow/fp8", 0.5, 2.0)]})
    assert [p["models"] for p in providers] == [[MODEL, OTHER], [OTHER]]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35)])
        listed(router, OTHER, [live("novita/fp4", 0.08, 0.35), live("slow/fp8", 0.5, 2.0)])
        assert (await check(config, picks, providers)).ok is True
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35)])
        listed(router, OTHER, [live("novita/fp4", 0.08, 0.35)])  # slow/fp8 left the one model that was given it
        result = await check(config, picks, providers)
    assert [(i.model, i.provider) for i in of_level(result, "error")] == [(OTHER, "slow/fp8")]


async def test_a_pinned_endpoint_that_is_not_healthy_is_a_warning_and_never_an_error():
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35, status=-2), live("deepinfra/fp8", uptime_last_30m=30.0)])
        result = await check()
    assert result.ok is True
    warnings = of_level(result, "warning")
    assert {(i.check, i.provider) for i in warnings} == {("health", "novita/fp4"), ("health", "deepinfra/fp8")}
    by_tag = {i.provider: i.message for i in warnings}
    assert "-2" in by_tag["novita/fp4"] and "30" in by_tag["deepinfra/fp8"]
    assert not of_level(result, "error")


async def test_health_that_is_unknown_or_good_enough_is_not_a_warning():
    ok = [
        live("novita/fp4", 0.08, 0.35, status=None, uptime_last_30m=None),
        live("deepinfra/fp8", status=0, uptime_last_30m=50.0),
    ]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, ok)
        result = await check()
    assert result.ok is True and not of_level(result, "warning")


async def test_an_unhealthy_endpoint_that_the_preset_does_not_pin_is_not_mentioned():
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35), live("deepinfra/fp8"), live("wafer/bf16", status=-5)])
        result = await check()
    assert [i.level for i in result.items] == ["ok"]


# ---- price, expiry ---------------------------------------------------------------------------------------------


async def test_a_price_that_moved_by_more_than_a_fifth_is_a_warning():
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35 * 1.3), live("deepinfra/fp8", 0.10 * 1.15, 0.40)])
        result = await check()
    assert result.ok is True
    (drift,) = of_level(result, "warning")
    assert (drift.check, drift.provider, drift.model) == ("price", "novita/fp4", MODEL)
    assert "output" in drift.message and "input" not in drift.message  # only the price that moved is named


async def test_a_pinned_endpoint_that_stopped_being_free_counts_as_drift():
    config, picks, providers = plan_of({MODEL: [("free/x", 0.0, 0.0)]})
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("free/x", 0.01, 0.0)])
        result = await check(config, picks, providers)
    assert [(i.level, i.check) for i in result.items if i.level != "ok"] == [("warning", "price")]


async def test_the_price_a_model_was_planned_at_is_its_own_not_the_first_models_on_a_shared_tag():
    # one tag, two models, two prices: the provider row carries only the first model's
    shared = [("novita/fp4", 0.08, 0.35)]
    config, picks, providers = plan_of({MODEL: shared, OTHER: [("novita/fp4", 0.40, 1.0)]})
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35)])
        listed(router, OTHER, [live("novita/fp4", 0.40, 1.0)])
        result = await check(config, picks, providers)
    assert result.ok is True and not of_level(result, "warning")


async def test_a_model_that_expires_within_a_week_is_a_warning_whoever_says_so():
    soon = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    later = (datetime.now(UTC) + timedelta(days=10)).date().isoformat()
    with respx.mock(assert_all_called=False) as router:
        live_stored(router, expiration_date=soon)  # the listing says so
        said_by_listing = await check()
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        said_by_plan = await check(*plan_of(expires=soon))
    with respx.mock(assert_all_called=False) as router:
        live_stored(router, expiration_date=later)
        not_yet = await check(*plan_of(expires=later))
    for result in (said_by_listing, said_by_plan):
        assert result.ok is True
        (warning,) = of_level(result, "warning")
        assert (warning.check, warning.model) == ("expiry", MODEL) and soon in warning.message
    assert not of_level(not_yet, "warning")


async def test_a_model_that_has_already_expired_is_a_warning_too():
    gone = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        result = await check(*plan_of(expires=gone))
    assert result.ok is True
    assert [i.check for i in of_level(result, "warning")] == ["expiry"]


# ---- a listing that cannot be read -------------------------------------------------------------------------------


async def test_a_listing_that_times_out_is_a_warning_after_one_retry(no_waits):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/models/{MODEL}/endpoints").mock(side_effect=httpx.ReadTimeout("slow"))
        result = await check()
    assert route.call_count == 2  # the first try and one retry
    assert result.ok is True
    (warning,) = result.items
    assert (warning.level, warning.check, warning.model) == ("warning", "listing", MODEL)
    assert f"could not check live data for {MODEL}" in warning.message.lower()


async def test_a_listing_that_answers_with_a_server_error_is_a_warning(no_waits):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/models/{MODEL}/endpoints").mock(return_value=httpx.Response(503, text="down"))
        result = await check()
    assert route.call_count == 2 and result.ok is True
    assert [(i.level, i.check) for i in result.items] == [("warning", "listing")]
    assert "503" in result.items[0].message


@pytest.mark.parametrize("body", [{"data": None}, {"error": {"message": "x"}}, ["not", "a", "listing"], {"data": {}}])
async def test_an_answer_with_no_endpoint_list_is_a_warning_not_a_claim_that_there_are_none(body):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/models/{MODEL}/endpoints").mock(return_value=httpx.Response(200, json=body))
        result = await check()
    assert result.ok is True
    assert [(i.level, i.check) for i in result.items] == [("warning", "listing")]


async def test_a_listing_that_fails_for_any_other_reason_is_still_only_a_warning():
    with respx.mock(assert_all_called=False):  # nothing mocked: the mock refuses the request outright
        result = await check()
    assert result.ok is True
    assert [(i.level, i.check) for i in result.items] == [("warning", "listing")]


# ---- parameters and the output cap -----------------------------------------------------------------------------


async def test_a_pinned_endpoint_that_lacks_a_required_parameter_is_an_error():
    endpoints = [
        live("novita/fp4", 0.08, 0.35, supported_parameters=["temperature", "tools"]),
        live("deepinfra/fp8", supported_parameters=["temperature"]),
    ]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        result = await check(require_params=["tools"])
    assert result.ok is False
    (error,) = of_level(result, "error")
    assert (error.check, error.provider, error.model) == ("params", "deepinfra/fp8", MODEL)
    assert "tools" in error.message


async def test_an_endpoint_that_does_not_report_its_parameters_is_not_held_against_the_preset():
    endpoints = [live("novita/fp4", 0.08, 0.35), live("deepinfra/fp8", supported_parameters=None)]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        result = await check(require_params=["tools"])
    assert result.ok is True and not of_level(result, "warning")


async def test_a_pinned_endpoint_whose_output_cap_is_below_max_tokens_is_an_error():
    endpoints = [
        live("novita/fp4", 0.08, 0.35, max_completion_tokens=4_096),
        live("deepinfra/fp8", max_completion_tokens=16_384),
    ]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        result = await check(max_tokens=8_192)
        unlimited = await check()
    assert result.ok is False
    (error,) = of_level(result, "error")
    assert (error.check, error.provider) == ("max_tokens", "novita/fp4")
    assert "4096" in error.message.replace(",", "") and "8192" in error.message.replace(",", "")
    assert unlimited.ok is True  # no limit asked for, none enforced


async def test_a_cap_that_is_not_reported_is_not_held_against_the_preset():
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        result = await check(max_tokens=1_000_000)
    assert result.ok is True


async def test_a_model_with_nothing_pinned_is_warned_when_no_live_endpoint_can_honour_the_requirement():
    config, picks, _providers = plan_of()
    config.pop("provider")
    endpoints = [
        live("novita/fp4", supported_parameters=["temperature"], max_completion_tokens=1_000),
        live("deepinfra/fp8", supported_parameters=["seed"], max_completion_tokens=512),
    ]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        result = await check(config, picks, [], require_params=["tools"])
        capped = await check(config, picks, [], max_tokens=4_096)
    assert result.ok is True and capped.ok is True  # there is nothing pinned to be wrong about
    (warning,) = of_level(result, "warning")
    assert (warning.check, warning.model) == ("params", MODEL)
    assert "tools" in warning.message and "ignored" in warning.message
    assert [i.check for i in of_level(capped, "warning")] == ["max_tokens"]


async def test_a_model_with_nothing_pinned_is_not_warned_when_some_live_endpoint_can_honour_it():
    config, picks, _providers = plan_of()
    config.pop("provider")
    endpoints = [
        live("novita/fp4", supported_parameters=["temperature"]),
        live("deepinfra/fp8", supported_parameters=["tools"]),
    ]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        result = await check(config, picks, [], require_params=["tools"])
    assert result.ok is True and not of_level(result, "warning")


# ---- how the listings are fetched --------------------------------------------------------------------------------


async def test_at_most_four_listings_are_fetched_at_a_time():
    models = {f"acme/m{i}": [("novita/fp4", 0.08, 0.35)] for i in range(7)}
    config, picks, providers = plan_of(models)
    running = peak = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return httpx.Response(200, json={"data": {"endpoints": [live("novita/fp4", 0.08, 0.35)]}})

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=rf"{BASE}/models/acme/m\d/endpoints").mock(side_effect=answer)
        result = await check(config, picks, providers)
    assert result.ok is True and len(result.items) == 7
    assert 1 < peak <= 4


# ---- the preset's own provider filters, require_parameters and the free tier ----------------------------------------


def served(result: Validation) -> list[Check]:
    return [i for i in result.items if i.check == "served"]


async def test_a_model_none_of_whose_live_endpoints_passes_the_quantization_list_is_warned_about():
    config, picks, providers = plan_of({MODEL: STORED, OTHER: [("novita/fp4", 0.08, 0.35)]})
    with respx.mock(assert_all_called=False) as router:
        listed(
            router,
            MODEL,
            [live("novita/fp4", 0.08, 0.35, quantization="fp8"), live("deepinfra/fp8", quantization="fp8")],
        )
        listed(router, OTHER, [live("novita/fp4", 0.08, 0.35, quantization="bf16")])
        result = await check(config, picks, providers, quantizations=["bf16"])
    assert result.ok is True  # the other model can still be served
    (warning,) = served(result)
    assert (warning.level, warning.model) == ("warning", MODEL)
    assert "will never be served" in warning.message and MODEL in warning.message
    assert [(i.level, i.check, i.model) for i in result.items if i.model == OTHER] == [("ok", "listing", OTHER)]


async def test_a_preset_none_of_whose_models_can_be_served_is_an_error():
    config, picks, providers = plan_of({MODEL: STORED, OTHER: [("novita/fp4", 0.08, 0.35)]})
    with respx.mock(assert_all_called=False) as router:
        listed(
            router,
            MODEL,
            [live("novita/fp4", 0.08, 0.35, quantization="fp8"), live("deepinfra/fp8", quantization="fp8")],
        )
        listed(router, OTHER, [live("novita/fp4", 0.08, 0.35, quantization="fp8")])
        result = await check(config, picks, providers, quantizations=["bf16"])
    assert result.ok is False
    assert [(i.level, i.model) for i in served(result)] == [("error", MODEL), ("error", OTHER)]


async def test_an_endpoint_with_no_listed_precision_is_unknown_and_only_a_list_that_names_unknown_keeps_it():
    config, picks, providers = plan_of()
    rows = [live("novita/fp4", 0.08, 0.35, quantization="unknown"), live("deepinfra/fp8", quantization=None)]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, rows)
        fp8 = await check(config, picks, providers, quantizations=["fp8"])
        both = await check(config, picks, providers, quantizations=["fp8", "unknown"])
        none = await check(config, picks, providers)
    assert [i.level for i in served(fp8)] == ["error"] and fp8.ok is False
    assert served(both) == [] and both.ok is True
    assert served(none) == [] and none.ok is True


async def test_a_model_whose_every_live_endpoint_is_ignored_is_never_served_whatever_the_case_or_the_name_used():
    config, picks, providers = plan_of()
    rows = [live("novita/fp4", 0.08, 0.35), live("deepinfra/fp8")]  # provider names Novita and Deepinfra
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, rows)
        by_slug = await check(config, picks, providers, ignore=["NOVITA", "deepinfra"])
        by_tag = await check(config, picks, providers, ignore=["novita/fp4", "Deepinfra/fp8"])
        by_name = await check(config, picks, providers, ignore=["novita", "deepinfra"])
        one_left = await check(config, picks, providers, ignore=["novita"])
    assert all(served(r) and r.ok is False for r in (by_slug, by_tag, by_name))
    assert served(one_left) == [] and one_left.ok is True


async def test_the_two_filters_apply_together():
    config, picks, providers = plan_of()
    rows = [live("novita/fp4", 0.08, 0.35, quantization="fp4"), live("deepinfra/fp8", quantization="fp8")]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, rows)
        result = await check(config, picks, providers, quantizations=["fp8"], ignore=["deepinfra"])
        spare = await check(config, picks, providers, quantizations=["fp8", "fp4"], ignore=["deepinfra"])
    assert [i.level for i in served(result)] == ["error"]
    assert served(spare) == []  # novita is fp4, which is allowed there, and not ignored


async def test_a_model_that_cannot_be_served_carries_no_all_clear_for_the_same_model():
    config, picks, providers = plan_of()
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, [live("novita/fp4", 0.08, 0.35, quantization="fp8")])
        result = await check(config, picks, providers, quantizations=["bf16"])
    assert [i.check for i in result.items] == ["served"]


async def test_a_pinned_endpoint_that_lacks_a_parameter_is_an_error_with_require_parameters_and_a_warning_without():
    endpoints = [
        live("novita/fp4", 0.08, 0.35, supported_parameters=["temperature", "tools"]),
        live("deepinfra/fp8", supported_parameters=["temperature"]),
    ]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        strict = await check(require_params=["tools"], require_parameters=True)
        default = await check(require_params=["tools"])  # the requirement has always been strict
        lenient = await check(require_params=["tools"], require_parameters=False)
    assert strict.ok is False and default.ok is False
    assert lenient.ok is True
    (warning,) = [i for i in lenient.items if i.check == "params"]
    assert (warning.level, warning.provider) == ("warning", "deepinfra/fp8")
    assert "tools" in warning.message and "ignored" in warning.message


async def test_the_output_cap_stays_an_error_whether_or_not_parameters_are_required():
    endpoints = [live("novita/fp4", 0.08, 0.35, max_completion_tokens=4_096), live("deepinfra/fp8")]
    with respx.mock(assert_all_called=False) as router:
        listed(router, MODEL, endpoints)
        result = await check(max_tokens=8_192, require_parameters=False)
    assert result.ok is False and [i.check for i in of_level(result, "error")] == ["max_tokens"]


async def test_a_preset_of_free_models_only_is_warned_about_the_daily_cap():
    config, picks, providers = plan_of()
    picks[0].is_free = True
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        result = await check(config, picks, providers)
    (warning,) = [i for i in result.items if i.check == "free"]
    assert (warning.level, warning.model, warning.provider) == ("warning", None, None)
    assert warning.message == FREE_TIER_WARNING and "00:00 UTC" in warning.message and "1000" in warning.message
    assert result.ok is True


async def test_one_model_that_is_not_free_is_enough_to_leave_out_the_free_tier_warning():
    config, picks, providers = plan_of({MODEL: STORED, OTHER: [("novita/fp4", 0.08, 0.35)]})
    picks[0].is_free = True
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        live_stored(router, OTHER, [("novita/fp4", 0.08, 0.35)])
        result = await check(config, picks, providers)
        picks[1].is_free = True
        both = await check(config, picks, providers)
    assert not [i for i in result.items if i.check == "free"]
    assert len([i for i in both.items if i.check == "free"]) == 1


async def test_a_model_the_plan_did_not_pick_is_not_assumed_free():
    config, picks, providers = plan_of()
    picks[0].is_free = True
    config = {**config, "models": [MODEL, "acme/unlisted"]}
    with respx.mock(assert_all_called=False) as router:
        live_stored(router)
        live_stored(router, "acme/unlisted", [])
        result = await check(config, picks, providers)
    assert not [i for i in result.items if i.check == "free"]
