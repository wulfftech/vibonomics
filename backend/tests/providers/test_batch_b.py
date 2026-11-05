"""Provider adapters, batch B: Groq, Moonshot, Z.ai, Together, Fireworks, Cerebras, Requesty, Chutes, NVIDIA.

Public-endpoint fixtures in ``fixtures/batch_b`` are trimmed copies of real responses fetched on 2026-10-24.
Keyed endpoints couldn't be fetched without accounts, so their payloads below follow each provider's
documented schema / example response.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.http import FetchError
from vibonomics.providers import cerebras, chutes, fireworks, groq, moonshot, nvidia, requesty, together, zai
from vibonomics.providers.base import FetchContext, ResolvedProvider
from vibonomics.settings_store import AppSettings

FIX = Path(__file__).parent / "fixtures" / "batch_b"


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def js(name: str):
    return json.loads(text(name))


@pytest.fixture
def ctx() -> FetchContext:
    return FetchContext(catalog=ModelsDevCatalog(js("modelsdev.json")), settings=AppSettings())


def make(cls, ctx, *, key=None, mgmt=None, base_url=None, **options):
    cfg = ResolvedProvider(
        id=1, type=cls.spec.type, name=cls.spec.name, api_key=key, mgmt_key=mgmt, base_url=base_url, options=options
    )
    return cls(cfg, ctx)


def by_id(offers):
    return {o.model_id: o for o in offers}


# -- Cerebras ----------------------------------------------------------------------------------


@respx.mock
async def test_cerebras_public_prices_per_token_to_per_million(ctx):
    respx.get(cerebras.PUBLIC_MODELS).respond(json=js("cerebras_models.json"))
    offers = by_id(await make(cerebras.CerebrasAdapter, ctx).fetch_models())
    assert set(offers) == {"gpt-oss-120b", "qwen-3.8-27b"}
    gpt = offers["gpt-oss-120b"]
    assert (gpt.price.input, gpt.price.output) == (0.35, 0.75)
    assert gpt.price.cache_read == 0.35  # catalogue cache rate kept because the base rate matches
    assert gpt.pricing_source == "cerebras api"
    assert gpt.canonical_id == "openai/gpt-oss-120b"
    assert gpt.context_length == 131072 and gpt.max_output == 40960
    assert offers["qwen-3.8-27b"].price.output == 1.49
    assert "image" in offers["qwen-3.8-27b"].input_modalities


@respx.mock
async def test_cerebras_falls_back_to_catalogue_when_api_changes(ctx):
    respx.get(cerebras.PUBLIC_MODELS).respond(json={"models": "moved"})
    adapter = make(cerebras.CerebrasAdapter, ctx)
    offers = by_id(await adapter.fetch_models())
    assert offers["gpt-oss-120b"].pricing_source == "models.dev"
    assert offers["gpt-oss-120b"].price.input == 0.35
    assert "pricing_page_error" in offers["gpt-oss-120b"].extra
    assert (await adapter.fetch_balance()).status == "unsupported"


# -- Groq --------------------------------------------------------------------------------------


def test_groq_docs_parser_handles_mangled_cells():
    rows = {r.model_id: r for r in groq.parse_docs(text("groq_models.md"))}
    assert (rows["openai/gpt-oss-120b"].input, rows["openai/gpt-oss-120b"].output) == (0.15, 0.60)
    assert rows["openai/gpt-oss-20b"].input == 0.075
    assert rows["qwen/qwen3.8-27b"].output == 4.0
    assert rows["llama-3.3-70b-versatile"].flags == {"enterprise": True, "contact_sales": True}
    assert rows["whisper-large-v3"].input is None and "per hour" in rows["whisper-large-v3"].flags["unit_price"]
    assert rows["openai/gpt-oss-120b"].context == 131072 and rows["openai/gpt-oss-120b"].max_output == 65536
    assert rows["qwen/qwen3.8-27b"].section == "Preview Models"


@respx.mock
async def test_groq_no_key_uses_docs_as_catalogue(ctx):
    respx.get(groq.DOCS_MODELS).respond(text=text("groq_models.md"))
    adapter = make(groq.GroqAdapter, ctx)
    offers = by_id(await adapter.fetch_models())
    gpt = offers["openai/gpt-oss-120b"]
    assert (gpt.price.input, gpt.price.output, gpt.price.cache_read) == (0.15, 0.6, 0.075)
    assert gpt.pricing_source == "groq docs"
    # Deprecated in models.dev, but still listed (Enterprise / contact sales): kept, price cleared.
    llama = offers["llama-3.3-70b-versatile"]
    assert not llama.deprecated and llama.price.input is None and llama.pricing_source == "groq docs (contact sales)"
    assert "qwen/qwen3.6-27b" not in offers  # deprecated and gone from the docs
    assert offers["minimaxai/minimax-m2.7"].pricing_source == "groq docs (contact sales)"
    assert (await adapter.fetch_balance()).status == "unsupported"


@respx.mock
async def test_groq_live_list_and_page_failure(ctx):
    respx.get(groq.DOCS_MODELS).respond(404)
    respx.get(f"{groq.API}/models").respond(
        json={
            "object": "list",
            "data": [
                {"id": "openai/gpt-oss-120b", "object": "model", "active": True, "context_window": 131072},
                {"id": "groq/compound", "object": "model", "active": True, "context_window": 131072},
                {"id": "whisper-large-v3", "object": "model", "active": False, "context_window": 448},
            ],
        }
    )
    offers = by_id(await make(groq.GroqAdapter, ctx, key="gsk_test").fetch_models())
    assert set(offers) == {"openai/gpt-oss-120b", "groq/compound"}
    assert offers["openai/gpt-oss-120b"].pricing_source == "models.dev"
    assert offers["openai/gpt-oss-120b"].price.input == 0.15
    assert "HTTP 404" in offers["openai/gpt-oss-120b"].extra["pricing_page_error"]
    assert offers["groq/compound"].price.input is None


# -- Moonshot ----------------------------------------------------------------------------------

MOONSHOT_BALANCE = {  # documented example response
    "code": 0,
    "data": {"available_balance": 49.58894, "voucher_balance": 46.58893, "cash_balance": 3.00001},
    "scode": "0x0",
    "status": True,
}


@respx.mock
async def test_moonshot_no_key_and_live_overlay(ctx):
    adapter = make(moonshot.MoonshotAdapter, ctx)
    offers = by_id(await adapter.fetch_models())
    assert offers["kimi-k3.1-pro"].price.input == 2 and offers["kimi-k3.1-pro"].pricing_source == "models.dev"
    assert (await adapter.fetch_balance()).status == "needs_key"

    respx.get(f"{moonshot.API}/models").respond(
        json={
            "object": "list",
            "data": [
                {
                    "id": "kimi-k3.1-pro",
                    "context_length": 1048576,
                    "supports_image_in": True,
                    "supports_video_in": True,
                    "supports_reasoning": True,
                },
                {"id": "kimi-k4-preview", "context_length": 2097152},
            ],
        }
    )
    offers = by_id(await make(moonshot.MoonshotAdapter, ctx, key="sk-test").fetch_models())
    assert set(offers) == {"kimi-k3.1-pro", "kimi-k4-preview"}
    assert "video" in offers["kimi-k3.1-pro"].input_modalities
    assert offers["kimi-k4-preview"].price.input is None


@respx.mock
async def test_moonshot_balance_and_china_region(ctx):
    route = respx.get(f"{moonshot.API}/users/me/balance").respond(json=MOONSHOT_BALANCE)
    info = await make(moonshot.MoonshotAdapter, ctx, key="sk-test").fetch_balance()
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-test"
    assert info.status == "ok" and info.account_balance == 49.58894 and info.currency == "USD"
    assert info.details == {"voucher_balance": 46.58893, "cash_balance": 3.00001}
    assert info.message is None

    owed = {"data": {"available_balance": 2.0, "voucher_balance": 2.0, "cash_balance": -1.5}}
    respx.get(f"{moonshot.API_CN}/users/me/balance").respond(json=owed)
    cn = make(moonshot.MoonshotAdapter, ctx, key="sk-cn", region="cn")
    assert cn.base_url == moonshot.API_CN
    info = await cn.fetch_balance()
    assert info.currency == "CNY" and "negative" in info.message
    assert "sk-cn" not in (info.message or "")


async def test_moonshot_china_uses_cn_catalogue(ctx):
    cn = make(moonshot.MoonshotAdapter, ctx, region="cn")
    offers = by_id(await cn.fetch_models())
    assert offers["kimi-k3.1-pro"].price.input == 1.9437  # models.dev CNY→USD listing
    assert offers["kimi-k3.1-pro"].extra["modelsdev_provider"] == "moonshotai-cn"


# -- Z.ai --------------------------------------------------------------------------------------


def test_zai_pricing_parser_reads_quoted_tables_and_free_cells():
    rows = {r.name: r for r in zai.parse_pricing(text("zai_pricing.md"))}
    assert (rows["GLM-5.3"].input.current, rows["GLM-5.3"].output.current) == (1.4, 4.4)
    assert rows["GLM-5.3"].cache_read.current == 0.26
    assert rows["GLM-5.3"].extra["cache_storage_price"] == "Limited-time Free"
    assert rows["GLM-4.7-Flash"].input.current == 0.0  # in the blockquoted "Other Pricing" section
    assert rows["GLM-4.5-AirX"].output.current == 4.5
    assert rows["GLM-4-32B-0414-128K"].cache_read.current is None
    assert "GLM-Image" not in rows and "Web Search" not in rows  # per-image / per-use tables skipped


def test_zai_cell_discounts():
    assert zai.parse_cell("~~$1.4~~ $0.7").current == 0.7
    assert zai.parse_cell("~~$1.4~~ $0.7").list_price == 1.4
    assert zai.parse_cell("Limited-time Free").note == "Limited-time free"
    assert zai.parse_cell("\\$0.26").current == 0.26


@respx.mock
async def test_zai_prices_and_detected_discounts(ctx):
    page = text("zai_pricing.md")
    page = page.replace("| GLM-5.3 | \\$1.4 | \\$0.26 |", "| GLM-5.3 | ~~\\$1.4~~ \\$0.7 | \\$0.13 |")
    page = page.replace(
        "| GLM-5.3-Flash | \\$0.15 | \\$0.03 | Limited-time Free | \\$0.50 |",
        "| GLM-5.3-Flash | Limited-time Free | Free | Free | Limited-time Free |",
    )
    respx.get(zai.PRICING_PAGE).respond(text=page)
    offers = by_id(await make(zai.ZaiAdapter, ctx).fetch_models())
    glm = offers["glm-5.3"]
    assert glm.price.input == 0.7 and glm.price.output == 4.4 and glm.pricing_source == "z.ai docs"
    assert glm.list_price.input == 1.4 and glm.discount == round(1 - 5.1 / 5.8, 4)
    flash = offers["glm-5.3-flash"]
    assert flash.is_free and flash.discount == 1.0 and flash.discount_note == "Limited-time free"
    assert flash.list_price.input == 0.15  # undiscounted rate from the catalogue
    assert offers["glm-4.5-airx"].extra["id_from_pricing_page"]  # priced on the page, unknown to the catalogue
    assert offers["glm-5-turbo"].pricing_source == "models.dev"  # not on the page: catalogue price kept


@respx.mock
async def test_zai_page_failure_keeps_catalogue_and_coding_plan(ctx):
    respx.get(zai.PRICING_PAGE).respond(text="<html>moved</html>")
    offers = by_id(await make(zai.ZaiAdapter, ctx).fetch_models())
    assert offers["glm-5.3"].pricing_source == "models.dev" and offers["glm-5.3"].price.input == 1.4
    assert offers["glm-5.3"].extra["pricing_page_error"]

    coding = make(zai.ZaiAdapter, ctx, endpoint="coding")
    assert coding.base_url == zai.API_CODING
    offers = by_id(await coding.fetch_models())
    assert set(offers) == {"glm-5.3"}
    assert offers["glm-5.3"].pricing_source == "models.dev (GLM Coding Plan)" and not offers["glm-5.3"].is_free
    assert (await coding.fetch_balance()).status == "unsupported"


# -- Together ----------------------------------------------------------------------------------


def test_together_docs_parser():
    rows = {r.model_id: r for r in together.parse_docs(text("together_models.md"))}
    assert len(rows) == 23
    kimi = rows["moonshotai/Kimi-K3"]
    assert (kimi.input, kimi.cache_read, kimi.output, kimi.context) == (3.0, 0.3, 15.0, 1048576)
    assert rows["Prism-ML/Ternary-Bonsai-27B"].input == 0.0
    assert rows["openai/gpt-oss-120b"].cache_read is None
    assert rows["MiniMaxAI/MiniMax-M3"].extra == {"quantization": "FP4"}


@respx.mock
async def test_together_no_key_uses_docs(ctx):
    respx.get(together.DOCS_MODELS).respond(text=text("together_models.md"))
    offers = by_id(await make(together.TogetherAdapter, ctx).fetch_models())
    assert len(offers) == 23
    assert "deepseek-ai/DeepSeek-R1" not in offers  # deprecated catalogue entry, not served
    assert offers["zai-org/GLM-5.3"].pricing_source == "together docs"
    assert offers["Prism-ML/Ternary-Bonsai-27B"].is_free
    assert offers["moonshotai/Kimi-K3"].canonical_id == "moonshotai/kimi-k3"


@respx.mock
async def test_together_docs_discount_and_failure(ctx):
    page = text("together_models.md").replace(
        "| \\$3.00 | \\$0.30 | \\$15.00 |", "| ~~\\$3.00~~ \\$1.50 | \\$0.30 | \\$15.00 |"
    )
    respx.get(together.DOCS_MODELS).respond(text=page)
    kimi = by_id(await make(together.TogetherAdapter, ctx).fetch_models())["moonshotai/Kimi-K3"]
    assert kimi.price.input == 1.5 and kimi.list_price.input == 3.0 and kimi.discount == 0.0833

    respx.get(together.DOCS_MODELS).respond(404)
    offers = by_id(await make(together.TogetherAdapter, ctx).fetch_models())
    assert offers["moonshotai/Kimi-K3"].pricing_source == "models.dev"
    assert "pricing_page_error" in offers["moonshotai/Kimi-K3"].extra


@respx.mock
async def test_together_key_uses_pricing_api(ctx):
    respx.get(f"{together.API_ROOT}/v2/supported-models").respond(
        json={
            "object": "list",
            "data": [
                {
                    "id": "kimi-k3",
                    "name": "moonshotai/Kimi-K3",
                    "serverlessEndpoint": "moonshotai/Kimi-K3",
                    "status": "SUPPORTED_MODEL_STATUS_RECOMMENDED",
                    "capabilities": ["CAPABILITY_CHAT"],
                    "features": ["FEATURE_TOOL_CALLING", "FEATURE_REASONING"],
                    "inputModalities": ["MODALITY_TEXT", "MODALITY_IMAGE"],
                    "contextLength": "1048576",
                    "pricing": {"input": 2.5, "output": 12.5, "cachedInput": 0.25},
                },
                {
                    "id": "glm",
                    "name": "zai-org/GLM-5.3",
                    "status": "SUPPORTED_MODEL_STATUS_SUPPORTED",
                    "capabilities": ["CAPABILITY_CHAT"],
                    "pricing": {"input": 1.4, "output": 4.4},
                },
            ],
            "next_cursor": None,
        }
    )
    docs = respx.get(together.DOCS_MODELS).respond(text=text("together_models.md"))
    offers = by_id(await make(together.TogetherAdapter, ctx, key="tgp_test").fetch_models())
    assert set(offers) == {"moonshotai/Kimi-K3", "zai-org/GLM-5.3"}
    kimi = offers["moonshotai/Kimi-K3"]
    assert (kimi.price.input, kimi.price.output, kimi.price.cache_read) == (2.5, 12.5, 0.25)
    assert kimi.pricing_source == "together api" and kimi.reasoning and "image" in kimi.input_modalities
    assert offers["zai-org/GLM-5.3"].price.cache_read == 0.26  # API omits it; catalogue rate kept
    assert not docs.called  # every model priced by the API


@respx.mock
async def test_together_balance(ctx):
    usage = {
        "object": "list",
        "organization_id": "org_1",
        "billing_period": "2026-10",
        "currency": "USD",
        "earliest_window_start": None,
        "latest_window_end": None,
        "next_cursor": None,
        "data": [
            {
                "date": "2026-10-01",
                "line_items": [
                    {
                        "product_name": "Serverless Inference - Input Tokens",
                        "quantity": "1000",
                        "unit_price": "0.000003",
                        "cost": "1.25",
                    }
                ],
            },
            {"date": "2026-10-02", "line_items": [{"product_name": "x", "cost": "2.5"}, {"product_name": "y"}]},
        ],
    }
    respx.get(f"{together.API_ROOT}/v1/billing/usage").respond(json=usage)
    info = await make(together.TogetherAdapter, ctx, key="tgp_test").fetch_balance()
    assert info.status == "ok" and info.account_used == 3.75 and info.account_balance is None

    respx.get(f"{together.API_ROOT}/v1/billing/usage").respond(404, json={"error": {"message": "not found"}})
    info = await make(together.TogetherAdapter, ctx, key="tgp_test").fetch_balance()
    assert info.status == "unsupported" and "billing-usage" in info.message
    assert (await make(together.TogetherAdapter, ctx).fetch_balance()).status == "needs_key"


# -- Fireworks ---------------------------------------------------------------------------------


def test_fireworks_pricing_parser():
    rows = {(r.model_id, r.variant): r for r in fireworks.parse_pricing(text("fireworks_pricing.md"))}
    kimi = rows[("accounts/fireworks/models/kimi-k3", "")]
    assert (kimi.standard.input, kimi.standard.cache_read, kimi.standard.output) == (3.0, 0.3, 15.0)
    assert kimi.priority.output == 18.75
    fast = rows[("accounts/fireworks/routers/kimi-k3-fast", "fast")]
    assert fast.standard.input == 4.5 and fast.priority is None
    assert rows[("accounts/fireworks/models/kimi-k3", "us")].standard.input == 4.5
    assert rows[("accounts/fireworks/models/nemotron-3-ultra-nvfp4", "")].name == "NVIDIA Nemotron 3 Ultra"


def test_fireworks_fast_is_a_router_only_when_it_is_the_suffix_of_the_name():
    models = "https://app.fireworks.ai/models/fireworks"
    extra_rows = "".join(
        f"| [{name}]({models}/{slug}) | \\$1.00 / \\$0.10 / \\$2.00 | - |\n"
        for name, slug in (
            ("Fast Llama 70B", "fast-llama-70b"),
            ("Turbo Fast Coder", "turbo-fast-coder"),
            ("Mini Fast", "mini-fast"),
        )
    )
    page = text("fireworks_pricing.md").replace("| [Qwen 3.8 Max]", extra_rows + "| [Qwen 3.8 Max]")
    rows = {r.name: r for r in fireworks.parse_pricing(page)}
    # "Fast" inside a model's own name is not Fireworks' fast mode, which has no id of its own: it is a router.
    assert rows["Fast Llama 70B"].model_id == "accounts/fireworks/models/fast-llama-70b"
    assert rows["Fast Llama 70B"].variant == ""
    assert rows["Turbo Fast Coder"].model_id == "accounts/fireworks/models/turbo-fast-coder"
    assert rows["Turbo Fast Coder"].variant == ""
    # Its own slug already says fast, so a "-fast" router id would be for a model that does not exist.
    assert rows["Mini Fast"].model_id == "accounts/fireworks/models/mini-fast"
    assert rows["Mini Fast"].variant == ""
    # The real fast rows (a base model's name plus "Fast", on the base model's slug) are still routers.
    assert rows["Kimi K3 Fast"].model_id == "accounts/fireworks/routers/kimi-k3-fast"
    assert rows["Kimi K3 Fast"].variant == "fast"
    assert rows["GLM 5.3 Fast"].model_id == "accounts/fireworks/routers/glm-5p3-fast"


@respx.mock
async def test_fireworks_models_docs_and_live(ctx):
    respx.get(fireworks.PRICING_PAGE).respond(text=text("fireworks_pricing.md"))
    offers = by_id(await make(fireworks.FireworksAdapter, ctx).fetch_models())
    kimi = offers["accounts/fireworks/models/kimi-k3"]
    assert kimi.pricing_source == "fireworks docs" and kimi.extra["us_only_price"]["input"] == 4.5
    assert kimi.extra["priority_price"]["output"] == 18.75
    assert offers["accounts/fireworks/routers/kimi-k3-fast"].pricing_source == "fireworks docs"
    assert offers["accounts/fireworks/routers/kimi-k3-spot"].extra["tier"].startswith("spot")
    assert not offers["accounts/fireworks/models/gpt-oss-120b"].deprecated  # on the page again
    assert "accounts/fireworks/models/inkling" not in offers  # deprecated and not listed
    assert offers["accounts/fireworks/models/ember-1"].pricing_source == "fireworks docs"  # new, from page

    respx.get(f"{fireworks.API}/models").respond(
        json={"object": "list", "data": [{"id": "accounts/fireworks/models/kimi-k3"}]}
    )
    offers = by_id(await make(fireworks.FireworksAdapter, ctx, key="fw_test").fetch_models())
    assert "accounts/fireworks/models/glm-5p3" not in offers  # plain model missing from the live list
    assert "accounts/fireworks/routers/kimi-k3-fast" in offers  # routers aren't in /models; kept


@respx.mock
async def test_fireworks_page_failure(ctx):
    respx.get(fireworks.PRICING_PAGE).respond(text="# Pricing\n\nSee the website.")
    offers = by_id(await make(fireworks.FireworksAdapter, ctx).fetch_models())
    assert offers["accounts/fireworks/models/kimi-k3"].pricing_source == "models.dev"
    assert offers["accounts/fireworks/models/kimi-k3"].extra["pricing_page_error"]


@respx.mock
async def test_fireworks_balance(ctx):
    respx.get(f"{fireworks.MGMT}/accounts").respond(json={"accounts": [{"name": "accounts/acme"}]})
    respx.get(f"{fireworks.MGMT}/accounts/acme").respond(
        json={"name": "accounts/acme", "displayName": "Acme", "state": "READY", "suspendState": "CREDIT_DEPLETED"}
    )
    summary = respx.get(f"{fireworks.MGMT}/accounts/acme/billing/summary").respond(
        json={
            "lineItems": [
                {
                    "category": "Text Completion",
                    "totalCost": {"currencyCode": "USD", "units": "12", "nanos": 500000000},
                },
                {"category": "Audio", "totalCost": {"currencyCode": "USD", "nanos": 250000000}},
            ]
        }
    )
    info = await make(fireworks.FireworksAdapter, ctx, key="fw_test").fetch_balance()
    assert info.status == "ok" and info.account_used == 12.75 and info.currency == "USD"
    assert info.details["suspend_state"] == "CREDIT_DEPLETED" and info.message.startswith("Account suspended")
    params = summary.calls.last.request.url.params
    assert params["startTime"].endswith("-01T00:00:00Z") and "endTime" in params

    respx.get(f"{fireworks.MGMT}/accounts").respond(json={"accounts": [{"name": "accounts/a"}, {"name": "accounts/b"}]})
    info = await make(fireworks.FireworksAdapter, ctx, key="fw_test").fetch_balance()
    assert info.status == "error" and "Account ID" in info.message


# -- Requesty ----------------------------------------------------------------------------------


@respx.mock
async def test_requesty_prices_discounts_and_tiers(ctx):
    respx.get(f"{requesty.API}/models").respond(json=js("requesty_models.json"))
    offers = by_id(await make(requesty.RequestyAdapter, ctx).fetch_models())
    mm = offers["minimaxi/minimax-m3"]
    assert (mm.price.input, mm.price.output, mm.price.cache_read) == (0.3, 1.2, 0.06)
    assert (mm.list_price.input, mm.list_price.output) == (0.6, 2.4)
    assert mm.discount == 0.5 and mm.discount_note == "50% off on Requesty"
    assert mm.canonical_id == "minimax/MiniMax-M3"
    qwen = offers["alibaba/qwen3.7-plus"]
    assert qwen.discount == 0.2 and qwen.list_price.input == 0.4
    kimi = offers["moonshot/kimi-k3.1-pro"]
    assert kimi.discount is None and (kimi.price.input, kimi.price.cache_write) == (2.0, 2.0)
    haiku = offers["anthropic/claude-haiku-5-5"]
    assert haiku.extra["tiers"][0]["tier"] == {"type": "context", "size": 100000}
    assert haiku.extra["tiers"][0]["input"] == 0.5
    assert offers["vertex/gemini-4-argon@eu"].name.endswith("(eu)")
    assert offers["nvidia/nemotron-3-super-120b-a12b"].is_free
    assert any(o.extra.get("expires") for o in offers.values())


@respx.mock
async def test_requesty_balance_key_and_org(ctx):
    respx.get(f"{requesty.MANAGE}/apikey/self").respond(
        json={
            "id": "k1",
            "name": "vibonomics",
            "monthly_spend": 4.5,
            "monthly_limit": 20,
            "permissions": {"manage": "none", "completions": "write"},
        }
    )
    org = respx.get(f"{requesty.MANAGE}/org").respond(403, json={"error": {"origin": "router", "message": "no"}})
    info = await make(requesty.RequestyAdapter, ctx, key="rq_test").fetch_balance()
    assert info.status == "ok" and (info.key_usage, info.key_limit, info.key_remaining) == (4.5, 20.0, 15.5)
    assert info.account_balance is None and "org_hint" in info.details

    org.respond(json={"name": "Acme", "balance": 87.25})
    info = await make(requesty.RequestyAdapter, ctx, key="rq_test", mgmt="rq_mgmt").fetch_balance()
    assert info.account_balance == 87.25 and info.details["organization"] == "Acme"
    assert org.calls.last.request.headers["Authorization"] == "Bearer rq_mgmt"


# -- Chutes ------------------------------------------------------------------------------------


@respx.mock
async def test_chutes_public_prices(ctx):
    respx.get(f"{chutes.API}/models").respond(json=js("chutes_models.json"))
    offers = by_id(await make(chutes.ChutesAdapter, ctx).fetch_models())
    assert len(offers) == 14
    kimi = offers["moonshotai/Kimi-K3-TEE"]
    assert (kimi.price.input, kimi.price.output, kimi.price.cache_read) == (1.85, 9.5, 0.185)
    assert kimi.canonical_id == "moonshotai/kimi-k3" and kimi.extra["confidential_compute"]
    assert kimi.reasoning and kimi.tool_call and "video" in kimi.input_modalities
    assert offers["Qwen/Qwen3-32B-TEE"].canonical_id is None or offers["Qwen/Qwen3-32B-TEE"].creator


@respx.mock
async def test_chutes_account_discounts_and_balance(ctx):
    data = js("chutes_models.json")
    kimi_chute = next(m["chute_id"] for m in data["data"] if m["id"] == "moonshotai/Kimi-K3-TEE")
    gemma_chute = next(m["chute_id"] for m in data["data"] if m["id"] == "google/gemma-4-31B-turbo-TEE")
    respx.get(f"{chutes.API}/models").respond(json=data)
    respx.get(f"{chutes.MGMT}/users/me/price_overrides").respond(
        json=[
            {
                "user_id": "u",
                "chute_id": gemma_chute,
                "per_million_in": 0.06,
                "per_million_out": 0.2,
                "per_request": None,
                "per_step": None,
                "cache_discount": None,
            }
        ]
    )
    respx.get(f"{chutes.MGMT}/users/me/discounts").respond(
        json=[{"user_id": "u", "chute_id": kimi_chute, "discount": 0.2}]
    )
    offers = by_id(await make(chutes.ChutesAdapter, ctx, key="cpk_test").fetch_models())
    kimi = offers["moonshotai/Kimi-K3-TEE"]
    assert (kimi.price.input, kimi.price.output) == (1.48, 7.6) and kimi.discount == 0.2
    assert kimi.list_price.input == 1.85 and "20% account discount" in kimi.discount_note
    gemma = offers["google/gemma-4-31B-turbo-TEE"]
    assert gemma.price.input == 0.06 and gemma.price.cache_read == 0.006 and gemma.discount
    assert offers["Qwen/Qwen3-32B-TEE"].discount is None

    respx.get(f"{chutes.MGMT}/users/me").respond(
        json={"username": "alice", "user_id": "u", "balance": 12.34, "permissions": []}
    )
    respx.get(f"{chutes.MGMT}/users/me/subscription_usage").respond(
        json={"subscription": True, "monthly_price": 10, "monthly": {"usage": 7.5, "cap": 50, "remaining": 42.5}}
    )
    info = await make(chutes.ChutesAdapter, ctx, key="cpk_test").fetch_balance()
    assert info.status == "ok" and info.account_balance == 12.34 and info.key_label == "alice"
    assert "$7.50 of $50.00" in info.message


@respx.mock
async def test_chutes_bad_key_keeps_public_catalogue(ctx):
    respx.get(f"{chutes.API}/models").respond(json=js("chutes_models.json"))
    respx.get(f"{chutes.MGMT}/users/me/price_overrides").respond(401)
    offers = await make(chutes.ChutesAdapter, ctx, key="cpk_bad").fetch_models()
    assert len(offers) == 14 and all(o.discount is None for o in offers)
    assert "account_pricing_error" in offers[0].extra


# -- NVIDIA ------------------------------------------------------------------------------------


@respx.mock
async def test_nvidia_free_trial(ctx):
    respx.get(f"{nvidia.API}/models").respond(json=js("nvidia_models.json"))
    adapter = make(nvidia.NvidiaAdapter, ctx)
    offers = by_id(await adapter.fetch_models())
    assert len(offers) == 85
    assert all(o.is_free and o.price.input == 0 for o in offers.values())
    assert offers["z-ai/glm-5.3"].canonical_id == "zhipuai/glm-5.3"
    assert "moonshotai/kimi-k3" not in offers  # deprecated, no longer served
    assert "black-forest-labs/flux.1-dev" not in offers  # not in the live list
    assert offers["01-ai/yi-large"].pricing_source == "nvidia (free trial)"
    assert (await adapter.fetch_balance()).status == "unsupported"


@respx.mock
async def test_nvidia_key_check(ctx):
    route = respx.post(f"{nvidia.API}/chat/completions").respond(403)
    with pytest.raises(FetchError, match="rejected"):
        await make(nvidia.NvidiaAdapter, ctx, key="nvapi-bad").test_connection()
    route.respond(json={"choices": [{"message": {"content": "h"}}]})
    assert "accepted" in await make(nvidia.NvidiaAdapter, ctx, key="nvapi-ok").test_connection()


@respx.mock
async def test_nvidia_list_failure_keeps_catalogue(ctx):
    respx.get(f"{nvidia.API}/models").respond(404)
    offers = by_id(await make(nvidia.NvidiaAdapter, ctx).fetch_models())
    assert "openai/gpt-oss-20b" in offers and offers["openai/gpt-oss-20b"].is_free
    assert "moonshotai/kimi-k3" not in offers  # deprecated entries need the live list to survive
    assert offers["openai/gpt-oss-20b"].extra["live_list_error"]


async def test_specs_are_complete():
    classes = [
        groq.GroqAdapter,
        moonshot.MoonshotAdapter,
        zai.ZaiAdapter,
        together.TogetherAdapter,
        fireworks.FireworksAdapter,
        cerebras.CerebrasAdapter,
        requesty.RequestyAdapter,
        chutes.ChutesAdapter,
        nvidia.NvidiaAdapter,
    ]
    assert len({c.spec.type for c in classes}) == len(classes)
    for c in classes:
        s = c.spec
        assert s.name and s.description and s.homepage.startswith("https://")
        assert s.default_base_url.startswith("https://") and s.modelsdev_ids and s.keys_url
        assert s.balance_note
        assert not s.models_require_key  # every adapter here has a public price source
