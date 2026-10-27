"""Provider adapters, batch A: OpenCode, Anthropic, OpenAI, Gemini, DeepSeek, xAI, Mistral.

Pricing-page fixtures are trimmed copies of the real pages fetched on 2026-10-24; the models.dev
fixture is a small subset of the real catalogue.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.http import FetchError
from vibonomics.providers import anthropic, deepseek, google, mistral, openai, opencode, xai
from vibonomics.providers.base import FetchContext, ModelOffer, Price, ResolvedProvider
from vibonomics.providers.pricing_pages import apply_page_prices
from vibonomics.settings_store import AppSettings

FIX = Path(__file__).parent / "fixtures" / "batch_a"


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def js(name: str) -> dict:
    return json.loads(text(name))


@pytest.fixture
def ctx() -> FetchContext:
    return FetchContext(catalog=ModelsDevCatalog(js("modelsdev.json")), settings=AppSettings())


def make(cls, ctx: FetchContext, *, key=None, mgmt=None, extra=None, base_url=None, **options):
    cfg = ResolvedProvider(
        id=1,
        type=cls.spec.type,
        name=cls.spec.name,
        api_key=key,
        mgmt_key=mgmt,
        base_url=base_url,
        options=options,
        extra_key=extra,
    )
    return cls(cfg, ctx)


# -- Anthropic -------------------------------------------------------------------------------


@respx.mock
async def test_anthropic_prices_from_page(ctx):
    respx.get(anthropic.PRICING_URL).mock(return_value=httpx.Response(200, text=text("anthropic_pricing.md")))
    offers = {o.model_id: o for o in await make(anthropic.AnthropicAdapter, ctx).fetch_models()}
    opus = offers["claude-opus-5-5"]
    assert (opus.price.input, opus.price.output) == (4.0, 20.0)
    assert opus.price.cache_read == 0.2
    assert opus.price.cache_write == 5.0
    assert opus.pricing_source == "anthropic pricing page"
    assert opus.extra["cache_write_1h"] == 8.0


@respx.mock
async def test_anthropic_keeps_catalogue_prices_when_page_fails(ctx):
    respx.get(anthropic.PRICING_URL).mock(side_effect=httpx.ConnectError("boom"))
    offers = await make(anthropic.AnthropicAdapter, ctx).fetch_models()
    assert offers and all(o.extra.get("pricing_page_error") for o in offers)


@respx.mock
async def test_anthropic_admin_spend(ctx):
    route = respx.get(f"{anthropic.API}/v1/organizations/cost_report").mock(
        return_value=httpx.Response(200, json={"data": [{"results": [{"amount": "1000"}, {"amount": "234.5"}]}]})
    )
    info = await make(anthropic.AnthropicAdapter, ctx, key="sk-ant-x", mgmt="sk-ant-admin01-x").fetch_balance()
    assert info.status == "ok"
    # The headline is the calendar month, the figure the Console's billing page budgets against.
    assert info.account_used == 12.35
    assert info.details["month_to_date"] == 12.35
    assert info.details["last_30_days"] == 12.35
    assert info.details["spend_period"] == "this month"
    starts = [call.request.url.params["starting_at"] for call in route.calls]
    assert starts[0].endswith("-01T00:00:00Z")


async def test_anthropic_balances_without_admin_key(ctx):
    assert (await make(anthropic.AnthropicAdapter, ctx).fetch_balance()).status == "unsupported"
    assert (await make(anthropic.AnthropicAdapter, ctx, key="sk-ant-x").fetch_balance()).status == "needs_key"


@respx.mock
async def test_anthropic_subscription_usage(ctx):
    respx.get(anthropic.USAGE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "subscription_type": "max",
                "rate_limits": {
                    "five_hour": {"utilization": 0.25, "resets_at": "2026-10-25T18:00:00Z"},
                    "seven_day": {"utilization": 0.5, "resets_at": "2026-10-30T00:00:00Z"},
                    "extra_usage": {
                        "is_enabled": True,
                        "monthly_limit": 2000,
                        "used_credits": 1250,
                        "utilization": 0.625,
                        "currency": "USD",
                    },
                },
            },
        )
    )
    info = await make(anthropic.AnthropicAdapter, ctx, extra="oauth-token").fetch_balance()
    assert info.status == "ok"
    subscription = info.details["subscription"]
    assert [w["key"] for w in subscription["windows"]] == ["five_hour", "seven_day"]
    assert subscription["extra_usage"]["used"] == 12.5
    assert subscription["plan"] == "max"


@respx.mock
async def test_anthropic_subscription_usage_error_is_reported(ctx):
    respx.get(anthropic.USAGE_URL).mock(return_value=httpx.Response(401, json={"error": {"message": "expired"}}))
    info = await make(anthropic.AnthropicAdapter, ctx, extra="stale-token").fetch_balance()
    assert info.status == "error"
    assert "401" in (info.message or "")


def test_anthropic_chat_target_uses_messages_api(ctx):
    target = make(anthropic.AnthropicAdapter, ctx, key="k").chat_target("claude-opus-5-5")
    assert target.wire == "anthropic"
    assert target.base_url == "https://api.anthropic.com"
    assert target.headers["x-api-key"] == "k"
    assert target.headers["anthropic-version"] == "2023-06-01"


# -- OpenAI ----------------------------------------------------------------------------------


@respx.mock
async def test_openai_prices_from_page(ctx):
    respx.get(openai.PRICING_URL).mock(return_value=httpx.Response(200, text=text("openai_pricing.md")))
    offers = {o.model_id: o for o in await make(openai.OpenAIAdapter, ctx).fetch_models()}
    astra = offers["gpt-6-astra"]
    assert (astra.price.input, astra.price.output, astra.price.cache_read) == (10.0, 50.0, 1.0)
    long_context = astra.extra["long_context"]
    assert (long_context["input"], long_context["output"]) == (20.0, 75.0)
    # "(<272K context length)" in the page is stripped so the id matches the catalogue.
    assert offers["gpt-5.5"].price.input == 5.0


@pytest.mark.parametrize("ids", [["gpt-4o"], ["gpt-4o", "gpt-4o-2024-05-13"], ["gpt-4o-2024-05-13", "gpt-4o"]])
def test_a_dated_row_never_reprices_the_undated_model(ids):
    offers = {i: ModelOffer(model_id=i, name=i, price=Price(input=1, output=1)) for i in ids}
    apply_page_prices(list(offers.values()), openai.parse_pricing(text("openai_pricing.md")), "page")
    base = offers["gpt-4o"].price
    assert (base.input, base.output, base.cache_read) == (2.5, 10.0, 1.25)
    if "gpt-4o-2024-05-13" in offers:
        dated = offers["gpt-4o-2024-05-13"].price
        assert (dated.input, dated.output) == (5.0, 15.0)


@respx.mock
async def test_openai_admin_spend(ctx):
    respx.get(f"{openai.API}/organization/costs").mock(
        return_value=httpx.Response(200, json={"data": [{"results": [{"amount": {"value": 3.5}}]}]})
    )
    info = await make(openai.OpenAIAdapter, ctx, key="sk-x", mgmt="sk-admin-x").fetch_balance()
    assert (info.status, info.account_used) == ("ok", 3.5)


# -- Google ----------------------------------------------------------------------------------


@respx.mock
async def test_gemini_prices_and_promos(ctx):
    respx.get(google.PRICING_URL).mock(return_value=httpx.Response(200, text=text("gemini_pricing.md")))
    offers = {o.model_id: o for o in await make(google.GoogleAdapter, ctx).fetch_models()}
    argon = offers["gemini-4-argon"]
    assert (argon.price.input, argon.price.output) == (2.0, 10.0)
    assert argon.discount == 0.5
    assert argon.discount_until == "2026-11-30"
    assert argon.list_price.input == 4.0
    assert argon.extra["free_tier"] is False


@respx.mock
async def test_gemini_free_tier_models(ctx):
    respx.get(google.PRICING_URL).mock(return_value=httpx.Response(200, text=text("gemini_pricing.md")))
    offers = {o.model_id: o for o in await make(google.GoogleAdapter, ctx).fetch_models()}
    flash = offers.get("gemini-3.8-flash")
    assert flash is not None
    assert flash.discount == 0.5 and flash.discount_until == "2026-12-31"


# -- DeepSeek --------------------------------------------------------------------------------


@respx.mock
async def test_deepseek_peak_prices_and_off_peak(ctx):
    respx.get(deepseek.PRICING_URL).mock(return_value=httpx.Response(200, text=text("deepseek_pricing.html")))
    offers = {o.model_id: o for o in await make(deepseek.DeepSeekAdapter, ctx).fetch_models()}
    flash = offers["deepseek-flash"]
    assert (flash.price.input, flash.price.output, flash.price.cache_read) == (0.3, 1.2, 0.006)
    assert flash.extra["off_peak_prices"] == {"input": 0.15, "output": 0.6, "cache_read": 0.003}
    assert flash.context_length == 1_000_000
    assert flash.max_output == 384_000
    assert offers["deepseek-v4-pro"].price.input == 1.32


@respx.mock
async def test_deepseek_balance(ctx):
    respx.get(f"{deepseek.API}/user/balance").mock(
        return_value=httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [
                    {"currency": "CNY", "total_balance": "100.00", "granted_balance": "0", "topped_up_balance": "100"},
                    {
                        "currency": "USD",
                        "total_balance": "12.34",
                        "granted_balance": "2.00",
                        "topped_up_balance": "10.34",
                    },
                ],
            },
        )
    )
    info = await make(deepseek.DeepSeekAdapter, ctx, key="sk-x").fetch_balance()
    assert (info.status, info.account_balance, info.currency) == ("ok", 12.34, "USD")
    assert info.details["balances"]["CNY"]["total"] == "100.00"


# -- xAI -------------------------------------------------------------------------------------


@respx.mock
async def test_xai_prices_and_long_context(ctx):
    respx.get(xai.PRICING_URL).mock(return_value=httpx.Response(200, text=text("xai_pricing.md")))
    offers = {o.model_id: o for o in await make(xai.XaiAdapter, ctx).fetch_models()}
    grok = offers["grok-4.7"]
    assert (grok.price.input, grok.price.output, grok.price.cache_read) == (2.0, 6.0, 0.5)
    assert grok.extra["long_context"] == {"input": 4.0, "output": 12.0}


@respx.mock
async def test_xai_prepaid_balance(ctx):
    respx.get("https://management-api.x.ai/v1/billing/teams/team-1/prepaid/balance").mock(
        return_value=httpx.Response(200, json={"changes": [], "total": {"val": "-1250"}})
    )
    info = await make(xai.XaiAdapter, ctx, key="xai-x", mgmt="xai-m", team_id="team-1").fetch_balance()
    assert (info.status, info.account_balance) == ("ok", 12.5)


async def test_xai_balance_needs_team_id(ctx):
    info = await make(xai.XaiAdapter, ctx, mgmt="xai-m").fetch_balance()
    assert info.status == "needs_key"


# -- Mistral ---------------------------------------------------------------------------------


@respx.mock
async def test_mistral_prices(ctx):
    respx.get(mistral.PRICING_URL).mock(return_value=httpx.Response(200, text=text("mistral_pricing.md")))
    offers = {o.model_id: o for o in await make(mistral.MistralAdapter, ctx).fetch_models()}
    large = offers["mistral-large-4"]
    assert (large.price.input, large.price.output, large.price.cache_read) == (1.36, 4.18, 0.14)
    assert large.price.input > 0


async def test_mistral_has_no_balance_api(ctx):
    assert (await make(mistral.MistralAdapter, ctx).fetch_balance()).status == "unsupported"


# -- OpenCode --------------------------------------------------------------------------------


@respx.mock
async def test_opencode_discounts_and_prices(ctx):
    respx.get(opencode.ZEN_MODELS).mock(
        return_value=httpx.Response(
            200,
            json={"object": "list", "data": [{"id": mid} for mid in ("glm-5.3", "gemini-4-argon", "mystery-model")]},
        )
    )
    respx.get(opencode.ZEN_DOCS).mock(return_value=httpx.Response(200, text=text("opencode_pricing.md")))
    offers = {o.model_id: o for o in await make(opencode.OpenCodeAdapter, ctx).fetch_models()}
    argon = offers["gemini-4-argon"]
    assert (argon.price.input, argon.price.output) == (2.0, 10.0)
    assert argon.discount == 0.5 and argon.list_price.input == 4.0
    assert offers["mistral-large-4"].discount == 0.5
    # A live model the catalogue doesn't know yet still appears, with no price.
    assert offers["mystery-model"].price.input is None


async def test_opencode_chat_routing(ctx):
    adapter = make(opencode.OpenCodeAdapter, ctx, key="k")
    claude = adapter.chat_target("claude-opus-5-5")
    assert claude.wire == "anthropic"
    assert claude.base_url == "https://opencode.ai/inference/anthropic/v1"
    glm = adapter.chat_target("glm-5.3")
    assert (glm.wire, glm.base_url) == ("openai", "https://opencode.ai/inference/openai/v1")
    with pytest.raises(FetchError):
        adapter.chat_target("gemini-4-argon")


async def test_opencode_go_is_metered_subscription(ctx):
    adapter = make(opencode.OpenCodeGoAdapter, ctx)
    import respx as _respx

    with _respx.mock:
        _respx.get(opencode.GO_MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "glm-5.3"}]}))
        _respx.get(opencode.GO_DOCS).mock(return_value=httpx.Response(200, text=text("opencode_pricing.md")))
        offers = {o.model_id: o for o in await adapter.fetch_models()}
    assert offers["glm-5.3"].extra["subscription"] is True
    assert offers["glm-5.3"].pricing_source == "opencode go metered rates"


async def test_opencode_test_connection_never_calls_the_inference_models_route(ctx):
    import respx as _respx

    with _respx.mock as mock:
        _respx.get(opencode.ZEN_MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "glm-5.3"}]}))
        message = await make(opencode.OpenCodeAdapter, ctx, key="k").test_connection()
    # The inference base has no /models route; asking it is a 404 even with a good key.
    assert "/inference/openai/v1/models" not in str([call.request.url for call in mock.calls])
    assert "Key stored" in message and "1 models" in message


async def test_opencode_console_no_balance_api(ctx):
    info = await make(opencode.OpenCodeAdapter, ctx).fetch_balance()
    assert info.status == "unsupported"
    assert "console" in (info.message or "").lower()


async def test_opencode_go_usage_needs_a_key(ctx):
    info = await make(opencode.OpenCodeGoAdapter, ctx).fetch_balance()
    assert info.status == "needs_key"


@respx.mock
async def test_opencode_go_reports_usage_windows(ctx):
    route = respx.get(opencode.USAGE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "usage": {
                    "rolling": {"status": "ok", "percent": 4.5, "resetsAt": "2026-10-27T16:20:31.000Z"},
                    "weekly": {"status": "rate-limited", "percent": 100, "resetsAt": "2026-11-02T00:00:00.000Z"},
                    "monthly": {"status": "ok", "percent": 50, "resetsAt": "2026-11-22T12:28:46.000Z"},
                }
            },
        )
    )
    info = await make(opencode.OpenCodeGoAdapter, ctx, key="k").fetch_balance()
    assert info.status == "ok"
    windows = {w["key"]: w for w in info.details["usage_windows"]}
    assert windows["rolling"]["used_percentage"] == 4.5
    assert windows["weekly"]["used_percentage"] == 100
    assert windows["monthly"]["resets_at"].startswith("2026-11-22")
    assert route.calls[0].request.headers["Authorization"] == "Bearer k"
    assert info.details["source"] == "zen-go-usage"


@respx.mock
async def test_opencode_go_usage_reports_a_rejected_key(ctx):
    respx.get(opencode.USAGE_URL).mock(return_value=httpx.Response(401, json={"error": "Unauthorized"}))
    info = await make(opencode.OpenCodeGoAdapter, ctx, key="k").fetch_balance()
    assert info.status == "error"
    assert "401" in (info.message or "")


async def test_opencode_usage_parser_skips_broken_windows():
    windows = opencode.parse_usage(
        {"usage": {"rolling": {"percent": "12"}, "weekly": {"percent": None}, "monthly": "nonsense"}}
    )
    assert [w["key"] for w in windows] == ["rolling"]
    assert windows[0]["used_percentage"] == 12
    assert windows[0]["resets_at"] is None
    assert opencode.parse_usage({"unexpected": True}) == []


@respx.mock
async def test_opencode_go_test_connection_reads_usage(ctx):
    respx.get(opencode.GO_MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "glm-5.3"}]}))
    respx.get(opencode.USAGE_URL).mock(
        return_value=httpx.Response(200, json={"usage": {"monthly": {"status": "ok", "percent": 12}}})
    )
    message = await make(opencode.OpenCodeGoAdapter, ctx, key="k").test_connection()
    assert "1 Go usage window(s)" in message
