"""The best-value preset: ranking, the config it builds, the dry run, and the write that follows it."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from vibonomics import crypto
from vibonomics.app import create_app
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import BenchmarkScore, EvalResult, EvalRun, Model, ProviderConfig, Setting
from vibonomics.preset_validation import FREE_TIER_WARNING
from vibonomics.presets import (
    PLACEHOLDER_MESSAGES,
    STATE_KEY,
    Candidate,
    Excluded,
    build_config,
    choose_endpoints,
    current_models,
    diff_config,
    digest_of,
    merge_endpoints,
    owned_view,
    owner_fingerprint,
    passthrough_digest,
    passthrough_view,
    rank,
    select_models,
    unsaved_keys,
)
from vibonomics.pricing import Task
from vibonomics.settings_store import (
    AppSettings,
    PresetGuardrails,
    PresetSettings,
    PresetsSettings,
    load_settings,
    preset_slug,
    save_settings,
)

BASE = "https://openrouter.ai/api/v1"
SLUG = "vibonomics-best-value"
KEY = "sk-or-test-key-1234567890"
PROVIDER = "Preset test router"
TASK = Task(600_000, 20_000, 0.8)

ENDPOINTS: list[dict[str, Any]] = [
    {
        "provider": "DeepInfra",
        "tag": "deepinfra/fp8",
        "quantization": "fp8",
        "input": 0.10,
        "output": 0.40,
        "discount": 0.3,
        "uptime_1d": 99,
    },
    {
        "provider": "Wafer",
        "tag": "wafer/bf16",
        "quantization": "bf16",
        "input": 0.12,
        "output": 0.45,
        "discount": 0,
        "uptime_1d": 98,
    },
    {
        "provider": "Novita",
        "tag": "novita/fp4",
        "quantization": "fp4",
        "input": 0.08,
        "output": 0.35,
        "discount": 0,
        "uptime_1d": 97,
    },
    {
        "provider": "Slow",
        "tag": "slow/fp8",
        "quantization": "fp8",
        "input": 0.5,
        "output": 2.0,
        "discount": 0,
        "uptime_1d": 50,
    },
    {"provider": "Broken", "tag": "broken/none", "input": None, "output": None},
]

# model id: (input, output, score, extra Model fields)
MODELS: dict[str, tuple[float, float, float, dict[str, Any]]] = {
    "acme/best": (0.10, 0.40, 80, {"extra": {"endpoints": ENDPOINTS}}),
    "acme/steady": (0.105, 0.42, 80, {}),  # about 5% less value than acme/best
    "acme/pricey": (1.0, 4.0, 90, {}),
    "acme/free:free": (0.0, 0.0, 95, {"is_free": True}),
    "acme/slab:batch": (0.01, 0.01, 95, {}),
    "acme/notools": (0.01, 0.01, 95, {"tool_call": False}),
    "acme/low": (0.01, 0.01, 40, {}),
    "acme/gone": (0.01, 0.01, 95, {"active": False}),
    "acme/small": (0.01, 0.01, 95, {"context_length": 8_000}),
}


def _canonical(model_id: str) -> str:
    return "preset-test/" + model_id.split("/")[1].split(":")[0]


def _settings(**patch: Any) -> None:
    # acme/small is the best value of all and has a small context window: most tests screen it out.
    fields = {"enabled": True, "min_context": 100_000, **patch}
    with session_scope() as session:
        save_settings(session, AppSettings(presets=PresetsSettings(items=[PresetSettings(**fields)])))


def _clean(session) -> None:
    # SQLite hands a deleted provider's id to the next one, which would inherit its test results.
    runs = select(EvalRun.id).where(EvalRun.suite == "preset-test")
    session.execute(delete(EvalResult).where(EvalResult.run_id.in_(runs)))
    session.execute(delete(EvalRun).where(EvalRun.suite == "preset-test"))
    session.execute(delete(ProviderConfig).where(ProviderConfig.type == "openrouter"))
    session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id.like("preset-test/%")))
    session.execute(delete(Setting).where(Setting.key.in_(["app", STATE_KEY])))


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    monkeypatch.delenv("VIBONOMICS_OPENROUTER_API_KEY", raising=False)
    get_config.cache_clear()
    init_db()
    with session_scope() as session:
        _clean(session)
        provider = ProviderConfig(type="openrouter", name=PROVIDER, enabled=True, api_key_enc=crypto.encrypt(KEY))
        session.add(provider)
        session.flush()
        for model_id, (price_in, price_out, score, extra) in MODELS.items():
            fields: dict[str, Any] = {"active": True, "is_free": False, "tool_call": True, "context_length": 200_000}
            session.add(
                Model(
                    provider_config_id=provider.id,
                    model_id=model_id,
                    name=model_id.split("/")[1],
                    canonical_id=_canonical(model_id),
                    price_input=price_in,
                    price_output=price_out,
                    **{**fields, **extra},
                )
            )
            session.add(
                BenchmarkScore(
                    source="epoch",
                    metric="eci",
                    dimension="intelligence",
                    source_model=f"preset-test-{model_id}",
                    canonical_id=_canonical(model_id),
                    value=score,
                    percentile=score,
                )
            )
    _settings()
    yield TestClient(create_app())
    with session_scope() as session:
        _clean(session)
    get_config.cache_clear()


def _preset(
    slug: str, config: dict[str, Any], version: int = 1, system_prompt: str | None = None, **data: Any
) -> dict[str, Any]:
    live = {"version": version, "config": config, "system_prompt": system_prompt}
    return {
        "data": {"slug": slug, "name": slug, "updated_at": "2026-11-04T07:36:33Z", "designated_version": live, **data}
    }


def _remote(router: respx.Router, slug: str, config: dict[str, Any] | None, **kwargs: Any) -> None:
    """The preset as OpenRouter holds it; None for a slug the account has no preset under."""
    url = f"{BASE}/presets/{slug}"
    if config is None:
        router.get(url).mock(return_value=httpx.Response(404, json={"error": {"message": "Preset not found"}}))
        return
    router.get(url).mock(return_value=httpx.Response(200, json=_preset(slug, config, **kwargs)))
    history = [
        {"version": v, "created_at": f"2026-11-0{v}T00:00:00Z", "config": config} for v in (1, kwargs.get("version", 1))
    ]
    router.get(f"{url}/versions").mock(return_value=httpx.Response(200, json={"data": history, "total_count": 2}))


def _plan(client: TestClient, slug: str = SLUG) -> dict[str, Any]:
    response = client.post("/api/presets/plan", json={"slug": slug})
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    return response.json()


def _apply(client: TestClient, plan: dict[str, Any] | str, slug: str = SLUG) -> httpx.Response:
    digest = plan if isinstance(plan, str) else plan["digest"]
    return client.post("/api/presets/apply", json={"slug": slug, "digest": digest})


def _slug_state(client: TestClient, slug: str = SLUG) -> dict[str, Any]:
    return client.get("/api/presets/state").json()["slugs"].get(slug, {})


# ---- the pieces ----------------------------------------------------------------------------------------------


def test_the_cheapest_endpoints_for_one_task_come_first_and_the_unusable_are_skipped():
    chosen = choose_endpoints(ENDPOINTS, TASK, {}, 3)
    assert [e["tag"] for e in chosen] == ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]
    assert chosen[0]["task_cost"] == pytest.approx((600_000 * 0.08 + 20_000 * 0.35) / 1e6)
    assert [e["tag"] for e in choose_endpoints(ENDPOINTS, TASK, {}, 10)][-1] == "slow/fp8"


def test_the_presets_own_provider_filters_narrow_the_choice():
    only_fp8 = choose_endpoints(ENDPOINTS, TASK, {"quantizations": ["FP8"]}, 5)
    assert [e["tag"] for e in only_fp8] == ["deepinfra/fp8", "slow/fp8"]
    ignoring = choose_endpoints(ENDPOINTS, TASK, {"ignore": ["Novita", "wafer"]}, 5)
    assert [e["tag"] for e in ignoring] == ["deepinfra/fp8", "slow/fp8"]


def test_an_allow_list_leaves_out_endpoints_whose_provider_is_not_on_it():
    # a provider may be named by its slug, by the full tag, or by its display name, in any case
    assert [e["tag"] for e in choose_endpoints(ENDPOINTS, TASK, {}, 5, allow=["deepinfra"])] == ["deepinfra/fp8"]
    assert [e["tag"] for e in choose_endpoints(ENDPOINTS, TASK, {}, 5, allow=["novita/fp4"])] == ["novita/fp4"]
    assert [e["tag"] for e in choose_endpoints(ENDPOINTS, TASK, {}, 5, allow=["WAFER", "Novita"])] == [
        "novita/fp4",
        "wafer/bf16",
    ]
    assert choose_endpoints(ENDPOINTS, TASK, {}, 5, allow=["nobody"]) == []
    assert len(choose_endpoints(ENDPOINTS, TASK, {}, 5, allow=[])) == 4  # an empty list restricts nothing


def test_the_cache_price_counts_toward_which_endpoint_is_cheapest():
    cheap_cache = [
        {"tag": "a/x", "input": 1.0, "output": 1.0},
        {"tag": "b/x", "input": 1.2, "output": 1.0, "cache_read": 0.0},
    ]
    assert choose_endpoints(cheap_cache, TASK, {}, 1)[0]["tag"] == "b/x"  # free cache reads at an 80% hit rate
    uncached = choose_endpoints(cheap_cache, Task(1000, 1000, 0.0), {}, 2)  # and with no hits the cache is no help
    assert [e["tag"] for e in uncached] == ["a/x", "b/x"] and uncached[0]["task_cost"] == pytest.approx(0.002)


def test_each_endpoint_is_costed_with_its_own_cache_price_and_none_means_uncached():
    # at an 80% hit rate the endpoint that caches is the cheaper one, though its input price is the higher
    plain = {"tag": "plain/x", "input": 0.08, "output": 0.35, "uptime_1d": 99}
    nulled = {"tag": "nulled/x", "input": 0.08, "output": 0.35, "cache_read": None, "uptime_1d": 98}
    caching = {"tag": "caching/x", "input": 0.10, "output": 0.40, "cache_read": 0.01, "uptime_1d": 97}
    # 0.01 is the model's own listed cache price, which an endpoint with no price of its own must not borrow
    chosen = choose_endpoints([plain, nulled, caching], TASK, {}, 3)
    assert [e["tag"] for e in chosen] == ["caching/x", "plain/x", "nulled/x"]
    assert chosen[0]["task_cost"] == pytest.approx((600_000 * (0.10 * 0.2 + 0.01 * 0.8) + 20_000 * 0.40) / 1e6)
    assert chosen[1]["task_cost"] == pytest.approx((600_000 * 0.08 + 20_000 * 0.35) / 1e6)  # 0.055, uncached


def _able(tag: str, params: list[str] | None, cap: int | None = None, price: float = 0.1) -> dict[str, Any]:
    """An endpoint as the adapter stores it now: with the guardrail parameters it supports and its output cap."""
    return {"tag": tag, "input": price, "output": price, "params": params, "max_completion_tokens": cap}


def test_an_endpoint_that_lacks_a_required_parameter_is_skipped():
    endpoints = [
        _able("plain/x", ["temperature"], price=0.01),
        _able("tools/x", ["temperature", "tools", "seed"], price=0.02),
        _able("everything/x", ["tools", "seed", "temperature"], price=0.03),
    ]
    assert _tags(choose_endpoints(endpoints, TASK, {}, 3)) == ["plain/x", "tools/x", "everything/x"]
    assert _tags(choose_endpoints(endpoints, TASK, {}, 3, require_params=["tools"])) == ["tools/x", "everything/x"]
    # every one of them is required, not any
    assert _tags(choose_endpoints(endpoints, TASK, {}, 3, require_params=["tools", "seed"])) == [
        "tools/x",
        "everything/x",
    ]
    assert choose_endpoints(endpoints, TASK, {}, 3, require_params=["tools", "top_k"]) == []


def test_an_endpoint_with_no_parameter_data_is_not_skipped_because_unknown_is_not_a_failure():
    stored_before = {"tag": "old/x", "input": 0.1, "output": 0.1}  # no `params` key: written before they were kept
    unreported = _able("silent/x", None)  # the listing did not say
    refusing = _able("no/x", ["temperature"])
    chosen = choose_endpoints([stored_before, unreported, refusing], TASK, {}, 3, require_params=["tools"])
    assert _tags(chosen) == ["old/x", "silent/x"]
    assert _tags(choose_endpoints(ENDPOINTS, TASK, {}, 5, require_params=["tools"])) == _tags(
        choose_endpoints(ENDPOINTS, TASK, {}, 5)
    )


def test_an_endpoint_whose_output_cap_is_below_max_tokens_is_skipped_and_an_unknown_cap_is_not():
    endpoints = [
        _able("small/x", [], cap=4_096, price=0.01),
        _able("exact/x", [], cap=8_192, price=0.02),
        _able("large/x", [], cap=65_536, price=0.03),
        _able("unknown/x", [], cap=None, price=0.04),
        {"tag": "old/x", "input": 0.1, "output": 0.1},  # stored before the cap was kept
    ]
    assert _tags(choose_endpoints(endpoints, TASK, {}, 5)) == ["small/x", "exact/x", "large/x", "unknown/x", "old/x"]
    assert _tags(choose_endpoints(endpoints, TASK, {}, 5, max_tokens=8_192)) == [
        "exact/x",
        "large/x",
        "unknown/x",
        "old/x",
    ]


def test_the_two_requirements_apply_together_with_the_other_filters():
    endpoints = [
        _able("a/x", ["tools"], cap=1_000, price=0.01),
        _able("b/x", ["tools"], cap=9_000, price=0.02),
        _able("c/x", [], cap=9_000, price=0.03),
    ]
    assert _tags(choose_endpoints(endpoints, TASK, {}, 3, require_params=["tools"], max_tokens=8_000)) == ["b/x"]
    assert (
        _tags(choose_endpoints(endpoints, TASK, {"ignore": ["b"]}, 3, require_params=["tools"], max_tokens=8_000)) == []
    )


def test_the_plan_pins_the_caching_endpoint_ahead_of_the_one_with_the_lower_sticker_price(client):
    rows = [
        {"provider": "Plain", "tag": "plain/x", "input": 0.08, "output": 0.35, "uptime_1d": 99},
        {"provider": "Caching", "tag": "caching/x", "input": 0.10, "output": 0.40, "cache_read": 0.01, "uptime_1d": 99},
    ]
    _set_extra("acme/best", {"endpoints": rows})  # the shape the OpenRouter adapter stores
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert [p["tag"] for p in plan["providers"]] == ["caching/x", "plain/x"]
    assert plan["config"]["provider"]["order"] == ["caching/x", "plain/x"]


def test_a_config_keeps_everything_but_the_model_and_the_provider_lists():
    base = {
        "model": "old/model",
        "tools": [{"type": "openrouter:web_search"}],
        "reasoning": {"enabled": True},
        "provider": {"only": ["old"], "order": ["old"], "quantizations": ["fp8"], "allow_fallbacks": False},
    }
    config = build_config(base, ["new/model"], ["a/fp8", "b/fp8"])
    assert config == {
        "model": "new/model",
        "tools": [{"type": "openrouter:web_search"}],
        "reasoning": {"enabled": True},
        "provider": {
            "only": ["a/fp8", "b/fp8"],
            "order": ["a/fp8", "b/fp8"],
            "quantizations": ["fp8"],
            "allow_fallbacks": False,
        },
    }
    assert base["model"] == "old/model"  # the base is not edited in place


def test_a_model_with_no_provider_data_drops_lists_left_over_from_another_model():
    config = build_config({"model": "old/model", "provider": {"only": ["old"], "order": ["old"]}}, ["new/model"], [])
    assert config["provider"] == {}
    assert build_config(None, ["m"], ["x/fp8"]) == {"model": "m", "provider": {"only": ["x/fp8"], "order": ["x/fp8"]}}
    # no provider block is invented where there is nothing to put in it, and no routing default is written for the
    # operator: OpenRouter's own (fallbacks on) already applies
    assert build_config(None, ["m"], []) == {"model": "m"}


def test_a_diff_opens_the_provider_block_and_names_what_goes():
    before = {"model": "a", "tools": [1], "provider": {"only": ["x"], "allow_fallbacks": True}}
    after = {"model": "b", "provider": {"only": ["y"], "allow_fallbacks": True}}
    changes = {c.path: (c.before, c.after) for c in diff_config(before, after)}
    assert changes == {"model": ('"a"', '"b"'), "tools": ("[1]", None), "provider.only": ('["x"]', '["y"]')}
    assert [c.path for c in diff_config(None, after)] == ["model", "provider.allow_fallbacks", "provider.only"]
    assert diff_config(after, after) == []


def test_keys_openrouter_did_not_keep_are_named():
    sent = {"model": "m", "cache_enabled": True, "provider": {"only": ["x"], "order": ["x"]}}
    stored = {"model": "m", "provider": {"only": ["x"]}}
    assert unsaved_keys(sent, stored) == ["cache_enabled", "provider.order"]
    assert unsaved_keys(sent, sent) == []


def test_ranking_applies_every_filter_and_puts_the_best_value_first(client):
    with session_scope() as session:
        settings = load_settings(session).model_copy(deep=True)
        item = settings.presets.items[0]
        ids = [p.id for p in session.query(ProviderConfig).filter_by(type="openrouter")]
        item.min_context = 0
        ranked, _task, profile = rank(session, settings, ids, item)
        assert profile == "agentic"
        assert [c.model_id for c in ranked] == ["acme/small", "acme/best", "acme/steady", "acme/pricey"]
        item.min_context = 100_000
        assert [c.model_id for c in rank(session, settings, ids, item)[0]] == [
            "acme/best",
            "acme/steady",
            "acme/pricey",
        ]
        item.require_tool_call = False
        item.include_free = True
        names = [c.model_id for c in rank(session, settings, ids, item)[0]]
        assert "acme/notools" in names and "acme/free:free" in names
        assert not {"acme/slab:batch", "acme/gone", "acme/low"} & set(names)


# ---- the dry run ---------------------------------------------------------------------------------------------


def test_a_missing_preset_is_a_create_with_the_exact_request_shown(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["action"] == "create"
    assert plan["current_version"] is None
    assert plan["pick"]["model_id"] == "acme/best"
    assert [p["tag"] for p in plan["providers"]] == ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]
    assert plan["config"] == {
        "model": "acme/best",
        "provider": {
            "only": ["novita/fp4", "deepinfra/fp8", "wafer/bf16"],
            "order": ["novita/fp4", "deepinfra/fp8", "wafer/bf16"],
        },
    }
    assert plan["request"] == {
        "method": "POST",
        "url": f"{BASE}/presets/{SLUG}/chat/completions",
        "body": {**plan["config"], "messages": PLACEHOLDER_MESSAGES},
    }
    assert plan["digest"]
    assert [r["model_id"] for r in plan["runners_up"]][:2] == ["acme/steady", "acme/pricey"]


def test_the_dry_run_writes_nothing(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions")
        _plan(client)
        assert post.call_count == 0
    assert client.get("/api/presets/state").json()["owned"] == []


def test_a_new_preset_starts_from_the_template_and_the_template_is_only_read(client):
    _settings(template_slug="my-tools")
    template = {
        "model": "other/model",
        "tools": [{"type": "openrouter:datetime"}],
        "provider": {"quantizations": ["fp8"]},
    }
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", template, version=3)
        post = router.post(f"{BASE}/presets/my-tools/chat/completions")
        plan = _plan(client)
        assert post.call_count == 0
    assert plan["config"]["tools"] == [{"type": "openrouter:datetime"}]
    assert plan["config"]["model"] == "acme/best"
    # the template asks for fp8, so the fp4 and bf16 endpoints are not listed
    assert plan["config"]["provider"]["only"] == ["deepinfra/fp8", "slow/fp8"]
    assert plan["config"]["provider"]["quantizations"] == ["fp8"]


def test_a_templates_provider_allow_list_is_honoured_and_the_plan_says_so(client):
    _settings(template_slug="my-tools")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", {"model": "other/model", "provider": {"only": ["deepinfra"]}}, version=3)
        plan = _plan(client)
    assert plan["config"]["provider"]["only"] == ["deepinfra/fp8"]  # not novita/fp4, which is cheaper
    assert [p["tag"] for p in plan["providers"]] == ["deepinfra/fp8"]
    assert any("template" in w and "deepinfra" in w and "only" in w for w in plan["warnings"])


def test_a_templates_allow_list_that_leaves_nothing_means_no_pin_and_a_warning(client):
    _settings(template_slug="my-tools")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", {"model": "other/model", "provider": {"only": ["nobody"]}}, version=3)
        plan = _plan(client)
    assert plan["action"] == "create" and plan["providers"] == []
    assert "only" not in plan["config"].get("provider", {}) and "order" not in plan["config"].get("provider", {})
    assert any("template" in w and "nobody" in w for w in plan["warnings"])
    assert any("no provider" in w.lower() for w in plan["warnings"])


def test_the_allow_list_is_the_templates_and_not_the_lists_this_preset_wrote_itself(client):
    # once the preset exists its own `only` is the last plan's output, and filtering by it would never let it change
    _own_as(KEY)
    live = {"model": "acme/best", "provider": {"only": ["deepinfra/fp8"], "order": ["deepinfra/fp8"]}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=3)
        plan = _plan(client)
    assert plan["config"]["provider"]["only"] == ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]
    assert not any("template" in w for w in plan["warnings"])


def test_a_missing_template_blocks_the_plan(client):
    _settings(template_slug="nowhere")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "nowhere", None)
        plan = _plan(client)
    assert plan["action"] == "blocked"
    assert "nowhere" in plan["reason"]
    assert plan["request"] is None


def test_a_preset_vibonomics_did_not_create_is_never_touched(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "mine/by-hand"})
        plan = _plan(client)
    assert plan["action"] == "blocked"
    assert "did not create it" in plan["reason"]
    assert plan["request"] is None and plan["digest"] is None
    assert plan["current_version"] == 1


def test_a_preset_with_a_system_prompt_is_planned_and_the_prompt_goes_back_with_the_write(client):
    # OpenRouter takes the prompt from the system message of each write and nulls it when there is none, so the
    # live one is resent: the preset is no longer one the app has to refuse.
    with session_scope() as session:
        row = session.get(Setting, STATE_KEY) or Setting(key=STATE_KEY, value={})
        row.value = {"owned": [SLUG]}
        session.merge(row)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, system_prompt="Be brief.")
        plan = _plan(client)
    assert plan["action"] == "update"
    assert plan["request"]["body"]["messages"] == [{"role": "system", "content": "Be brief."}, *PLACEHOLDER_MESSAGES]
    assert not any(c["path"] == "system_prompt" for c in plan["changes"])  # sent back as it is: nothing changes


def _own(slug: str = SLUG) -> None:
    with session_scope() as session:
        session.merge(Setting(key=STATE_KEY, value={"owned": [slug]}))


def test_our_own_preset_is_read_back_with_its_version_and_history(client):
    _own()
    current = {"model": "acme/best", "tools": [{"type": "openrouter:web_search"}]}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, current, version=4)
        plan = _plan(client)
    assert plan["action"] == "update"  # the provider lists are not there yet
    assert plan["current_version"] == 4
    assert [v["version"] for v in plan["versions"]] == [4, 1]
    assert plan["versions"][0]["live"] is True and plan["versions"][1]["live"] is False
    assert plan["config"]["tools"] == current["tools"]  # an edit made in the OpenRouter UI survives
    assert {c["path"] for c in plan["changes"]} == {"provider.only", "provider.order"}


def test_a_preset_that_is_already_right_is_unchanged(client):
    _own()
    desired = build_config(None, ["acme/best"], ["novita/fp4", "deepinfra/fp8", "wafer/bf16"])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, desired, version=2)
        plan = _plan(client)
    assert plan["action"] == "unchanged"
    assert plan["changes"] == []


def test_a_slightly_better_model_does_not_unseat_the_current_one(client):
    _own()
    current = {"model": "acme/steady", "provider": {"allow_fallbacks": True}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, current, version=2)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/steady"
    assert plan["kept_current"] is True
    assert plan["action"] == "unchanged"
    assert "stays" in plan["reason"]


def test_a_clearly_better_model_replaces_the_current_one(client):
    _own()
    _settings(switch_margin_pct=1)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/steady", "provider": {"allow_fallbacks": True}}, version=2)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/best"
    assert plan["action"] == "update"
    assert plan["reason"].startswith("Switches acme/steady to best, ")


def test_no_key_and_no_matching_model_each_block_with_a_reason(client):
    _settings(min_score=99)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        assert "No OpenRouter model passes" in _plan(client)["reason"]
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"api_key_enc": None})
    assert "Add an OpenRouter API key" in _plan(client)["reason"]


def test_openrouter_failing_blocks_the_plan_and_names_the_status(client):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/presets/{SLUG}").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
        plan = _plan(client)
    assert plan["action"] == "blocked"
    assert "HTTP 401" in plan["reason"]


# ---- the write -----------------------------------------------------------------------------------------------


def _echo(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)
    sent.pop("messages", None)
    return httpx.Response(200, json=_preset(SLUG, sent, version=1))


def test_apply_writes_exactly_the_reviewed_plan_and_the_slug_becomes_ours(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = _apply(client, plan)
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    body = response.json()
    assert body["applied"] is True and body["version"] == 1 and body["unsaved"] == []
    assert post.call_count == 1
    request = post.calls[0].request
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == plan["request"]["body"]
    assert client.get("/api/presets/state").json()["owned"] == [SLUG]
    state = _slug_state(client)
    assert (state["model_id"], state["version"]) == ("acme/best", 1)
    assert state["last_status"] == "applied" and state["last_trigger"] == "manual"


def test_apply_says_which_keys_openrouter_did_not_keep(client):
    _settings(template_slug="my-tools")

    def drop_cache(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        for key in ("messages", "cache_enabled"):
            sent.pop(key, None)
        return httpx.Response(200, json=_preset(SLUG, sent, version=1))

    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", {"model": "x/y", "cache_enabled": True})
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=drop_cache)
        plan = _plan(client)
        assert plan["config"]["cache_enabled"] is True
        body = _apply(client, plan).json()
    assert body["unsaved"] == ["cache_enabled"]
    assert "did not keep: cache_enabled" in body["message"]


def test_apply_refuses_a_plan_that_has_changed_since_it_was_reviewed(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        response = _apply(client, "stale")
        assert post.call_count == 0
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_apply_does_nothing_until_the_operator_allows_writes(client):
    _settings(enabled=False)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 0
    assert response.status_code == 409
    assert "Turn on" in response.json()["detail"]


def test_apply_writes_nothing_when_the_preset_is_already_right(client):
    _own()
    desired = build_config(None, ["acme/best"], ["novita/fp4", "deepinfra/fp8", "wafer/bf16"])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, desired, version=2)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 0
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["message"] == "Already up to date."
    assert _slug_state(client)["last_status"] == "unchanged"


def test_a_failed_write_is_reported_recorded_and_not_retried(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": f"boom {KEY}"})
        )
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 1
    assert response.status_code == 502
    assert KEY not in response.text
    assert _slug_state(client)["last_status"] == "failed"
    assert client.get("/api/presets/state").json()["owned"] == []  # nothing was created, so nothing is ours


# ---- settings and schedule -----------------------------------------------------------------------------------


def test_a_name_gets_the_prefix_and_is_made_into_a_slug():
    assert preset_slug("best value") == "vibonomics-best-value"
    assert preset_slug("  My Coding Preset! ") == "vibonomics-my-coding-preset"
    assert preset_slug("vibonomics-fast") == "vibonomics-fast"  # not prefixed twice
    assert preset_slug("Vibonomics Fast") == "vibonomics-fast"
    for bad in ("", "   ", "!!!", "x" * 80):
        with pytest.raises(ValueError):
            preset_slug(bad)


def _with_item(current: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """The settings document with the first preset changed."""
    presets = current["presets"]
    return {**current, "presets": {**presets, "items": [{**presets["items"][0], **patch}, *presets["items"][1:]]}}


def test_the_name_the_operator_types_is_saved_with_the_prefix(client):
    current = client.get("/api/settings").json()["settings"]
    for typed in ("", "!!!"):
        assert client.put("/api/settings", json={"settings": _with_item(current, slug=typed)}).status_code == 422, typed
    saved = client.put("/api/settings", json={"settings": _with_item(current, slug="Fast coding")})
    assert saved.status_code == 200
    assert saved.json()["settings"]["presets"]["items"][0]["slug"] == "vibonomics-fast-coding"


def test_up_to_three_models_and_ten_providers_are_allowed_and_no_more(client):
    # OpenRouter refuses a preset that lists a fourth model: HTTP 400 "A preset can have at most 3 models".
    current = client.get("/api/settings").json()["settings"]
    ok = _with_item(current, max_models=3, max_providers=10)
    assert client.put("/api/settings", json={"settings": ok}).status_code == 200
    for field, over in (("max_models", 4), ("max_providers", 11)):
        for value in (0, over):
            bad = _with_item(current, **{field: value})
            assert client.put("/api/settings", json={"settings": bad}).status_code == 422, (field, value)


def test_the_scheduled_update_can_run_every_thirty_minutes_and_no_more_often(client):
    current = client.get("/api/settings").json()["settings"]
    for hours, status in ((0.5, 200), (0.25, 422)):
        body = _with_item(current, interval_hours=hours)
        assert client.put("/api/settings", json={"settings": body}).status_code == status, hours
    assert PresetSettings(enabled=True, auto_apply=True, interval_hours=0.5).interval_hours == 0.5


def _auto(**patch: Any) -> AppSettings:
    fields = {"enabled": True, "auto_apply": True, **patch}
    return AppSettings(presets=PresetsSettings(items=[PresetSettings(**fields)]))


@pytest.mark.asyncio
async def test_the_scheduler_runs_the_preset_every_thirty_minutes():
    from vibonomics import scheduler

    try:
        scheduler.start(AppSettings())
        scheduler.reschedule(_auto(interval_hours=0.5))
        job = scheduler._scheduler.get_job(f"preset-sync:{SLUG}")
        assert job is not None and job.trigger.interval.total_seconds() == 30 * 60
    finally:
        scheduler.shutdown()


def test_it_is_off_until_the_operator_turns_it_on():
    assert AppSettings().presets.items == []  # a new instance manages no preset
    assert PresetSettings().enabled is False
    assert PresetSettings().auto_apply is False


@pytest.mark.asyncio
async def test_the_scheduler_adds_and_removes_the_preset_job():
    from vibonomics import scheduler

    try:
        job_id = f"preset-sync:{SLUG}"
        scheduler.start(AppSettings())
        assert scheduler._scheduler.get_job(job_id) is None
        scheduler.reschedule(_auto(auto_apply=False))
        assert scheduler._scheduler.get_job(job_id) is None
        scheduler.reschedule(_auto(interval_hours=12))
        job = scheduler._scheduler.get_job(job_id)
        assert job is not None and job.trigger.interval.total_seconds() == 12 * 3600
        scheduler.reschedule(_auto(enabled=False))
        assert scheduler._scheduler.get_job(job_id) is None
    finally:
        scheduler.shutdown()


# ---- several models ------------------------------------------------------------------------------------------


def _cand(model_id: str, value: float, score: float = 70, cost: float = 0.1, endpoints=None) -> Candidate:
    return Candidate(model_id, model_id, score, cost, value, None, None, endpoints or [])


def test_the_lead_model_and_the_fallbacks_are_both_written():
    config = build_config({"model": "old/m", "models": ["old/m"], "tools": [1]}, ["a/m", "b/m", "c/m"], ["x/fp8"])
    assert config["model"] == "a/m"
    assert config["models"] == ["a/m", "b/m", "c/m"]
    assert config["tools"] == [1]
    # going back to one model takes the fallback list away rather than leaving dropped models in it
    assert "models" not in build_config(config, ["a/m"], ["x/fp8"])


def test_a_preset_names_its_models_lead_first():
    assert current_models({"model": "a", "models": ["a", "b", "b", 3, None, "c"]}) == ["a", "b", "c"]
    assert current_models({"models": ["b", "a"]}) == ["b", "a"]
    assert current_models({"model": "a", "models": "not a list"}) == ["a"]
    assert current_models({}) == []


def test_the_best_value_models_are_chosen_in_order():
    ranked = [_cand("a", 100), _cand("b", 90), _cand("c", 80), _cand("d", 70)]
    picked, kept = select_models(ranked, [], 3, 10)
    assert [c.model_id for c in picked] == ["a", "b", "c"] and kept is False
    assert [c.model_id for c in select_models(ranked, [], 10, 10)[0]] == ["a", "b", "c", "d"]


def test_a_model_already_in_the_preset_is_kept_unless_beaten_by_the_margin():
    ranked = [_cand("a", 100), _cand("new", 95), _cand("b", 90), _cand("c", 80)]
    picked, kept = select_models(ranked, ["a", "b"], 2, 10)
    assert [c.model_id for c in picked] == ["a", "b"]  # 95 does not beat 90 by 10%
    assert kept is True
    picked, kept = select_models(ranked, ["a", "b"], 2, 1)
    assert [c.model_id for c in picked] == ["a", "new"]  # but it does by 1%, and b falls out
    assert kept is False


def test_the_lead_changes_only_when_another_model_beats_it_by_the_margin():
    ranked = [_cand("b", 108), _cand("a", 100)]
    assert [c.model_id for c in select_models(ranked, ["a", "b"], 2, 10)[0]] == ["a", "b"]
    assert [c.model_id for c in select_models(ranked, ["a", "b"], 2, 5)[0]] == ["b", "a"]


def _ep(tag: str) -> dict:
    return {"tag": tag, "provider": tag, "input": 1, "output": 1, "discount": 0}


def test_providers_for_several_models_are_taken_in_turn_and_shared_ones_name_both():
    merged = merge_endpoints(
        [("m1", [_ep("a"), _ep("b"), _ep("c")]), ("m2", [_ep("d"), _ep("a"), _ep("e")])],
        limit=4,
    )
    assert [e["tag"] for e in merged] == ["a", "d", "b", "c"]
    by_tag = {e["tag"]: e["models"] for e in merged}
    assert by_tag["a"] == ["m1", "m2"]  # m2 lists it too, so it names both and costs no extra slot
    assert len(merged) == 4


def test_the_first_model_cannot_take_every_provider_slot():
    merged = merge_endpoints([("m1", [_ep(t) for t in "abcdef"]), ("m2", [_ep(t) for t in "uvwxyz"])], limit=4)
    assert {e["tag"] for e in merged} == {"a", "u", "b", "v"}
    assert merge_endpoints([], 5) == []


def test_every_model_that_has_endpoints_is_guaranteed_one_of_its_own():
    per_model = [("m1", [_ep("a"), _ep("b")]), ("m2", [_ep("c"), _ep("d")]), ("m3", [_ep("e"), _ep("f")])]
    merged = merge_endpoints(per_model, limit=2)
    assert [e["tag"] for e in merged] == ["a", "c", "e"]  # three, though the limit is two: m3 would have none
    assert [e["models"] for e in merged] == [["m1"], ["m2"], ["m3"]]
    # once each has one, the limit holds again
    assert [e["tag"] for e in merge_endpoints(per_model, limit=4)] == ["a", "c", "e", "b"]
    # a shared endpoint serves both and takes one slot
    shared = merge_endpoints([("m1", [_ep("a"), _ep("b")]), ("m2", [_ep("a"), _ep("c")])], limit=1)
    assert [(e["tag"], e["models"]) for e in shared] == [("a", ["m1", "m2"])]


def test_a_plan_with_several_models_lists_them_and_their_providers(client):
    _settings(max_models=3, max_providers=4)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert [c["model_id"] for c in plan["picks"]] == ["acme/best", "acme/steady", "acme/pricey"]
    assert plan["pick"]["model_id"] == "acme/best"
    assert plan["config"]["model"] == "acme/best"
    assert plan["config"]["models"] == ["acme/best", "acme/steady", "acme/pricey"]
    assert plan["request"]["body"]["models"] == plan["config"]["models"]
    assert plan["reason"] == f"Creates {SLUG} with 3 models, led by best."
    assert plan["runners_up"] == []
    # only acme/best has endpoint data, and a provider list shared by every model would leave the other two with no
    # provider they can be served by, so no list is written at all
    assert plan["providers"] == []
    assert "provider" not in plan["config"]
    assert any("steady, pricey" in w and "no provider data" in w for w in plan["warnings"])


def _give_endpoints(model_id: str, specs: list[tuple]) -> None:
    """Endpoints for a model, as (tag, input, output) or (tag, input, output, uptime_1d)."""
    rows = [
        {
            "provider": tag.split("/")[0],
            "tag": tag,
            "quantization": "fp8",
            "input": i,
            "output": o,
            "uptime_1d": rest[0] if rest else 99,
        }
        for tag, i, o, *rest in specs
    ]
    _set_extra(model_id, {"endpoints": rows})


def _set_extra(model_id: str, extra: dict[str, Any]) -> None:
    with session_scope() as session:
        session.query(Model).filter_by(model_id=model_id).one().extra = extra


def test_every_model_in_the_preset_keeps_a_provider_of_its_own_even_past_the_limit(client):
    _settings(max_models=3, max_providers=2)
    _give_endpoints("acme/steady", [("steady-a/fp8", 0.1, 0.4), ("steady-b/fp8", 0.2, 0.5)])
    _give_endpoints("acme/pricey", [("pricey-a/fp8", 1.0, 4.0), ("pricey-b/fp8", 2.0, 5.0)])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    tags = ["novita/fp4", "steady-a/fp8", "pricey-a/fp8"]
    assert [p["tag"] for p in plan["providers"]] == tags
    assert [p["models"] for p in plan["providers"]] == [["acme/best"], ["acme/steady"], ["acme/pricey"]]
    assert plan["config"]["provider"] == {"only": tags, "order": tags}
    assert any("more than the limit of 2" in w for w in plan["warnings"])


def test_growing_the_lineup_says_which_models_were_added(client):
    _own()
    _settings(max_models=2)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "provider": {"allow_fallbacks": True}}, version=2)
        plan = _plan(client)
    assert plan["action"] == "update"
    assert plan["reason"] == "Model list: adds steady. Leads with best."
    assert {c["path"] for c in plan["changes"]} >= {"models"}


def test_shrinking_the_lineup_says_which_models_were_dropped(client):
    _own()
    current = {"model": "acme/best", "models": ["acme/best", "acme/steady", "acme/pricey"]}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, current, version=2)
        plan = _plan(client)
    assert plan["config"].get("models") is None
    assert plan["reason"].startswith("Model list: drops acme/steady, acme/pricey.")


# ---- archived presets and the template list -------------------------------------------------------------------


def test_an_archived_preset_blocks_the_plan_and_says_how_to_go_on(client):
    _own()
    with respx.mock(assert_all_called=False) as router:
        url = f"{BASE}/presets/{SLUG}"
        payload = _preset(SLUG, {"model": "acme/best"})
        payload["data"]["status"] = "archived"
        router.get(url).mock(return_value=httpx.Response(200, json=payload))
        router.get(f"{url}/versions").mock(return_value=httpx.Response(200, json={"data": []}))
        post = router.post(f"{url}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert post.call_count == 0
    assert plan["action"] == "blocked"
    assert "archived" in plan["reason"] and "another name" in plan["reason"]
    assert plan["request"] is None


def _listing(router: respx.Router) -> None:
    rows = [
        {"slug": "my-tools", "name": "My tools", "status": "active", "updated_at": "2026-11-03T00:00:00Z"},
        {"slug": "old-one", "name": "Old", "status": "archived", "updated_at": "2026-10-01T00:00:00Z"},
        {"slug": SLUG, "name": SLUG, "status": "active", "updated_at": "2026-11-04T00:00:00Z"},
        {"slug": "ds-fast", "name": "DS fast", "status": "active", "updated_at": "2026-11-04T00:00:00Z"},
    ]
    router.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={"data": rows, "total_count": 4}))
    router.get(f"{BASE}/presets/my-tools").mock(
        return_value=httpx.Response(
            200, json=_preset("my-tools", {"model": "a/m", "provider": {"only": ["deepinfra"]}}, 3)
        )
    )
    router.get(f"{BASE}/presets/ds-fast").mock(
        return_value=httpx.Response(200, json=_preset("ds-fast", {"model": "b/m", "models": ["b/m", "c/m"]}, 1))
    )


def test_the_accounts_presets_can_be_listed_to_start_from(client):
    _own()  # SLUG is one Vibonomics wrote
    with respx.mock(assert_all_called=False) as router:
        _listing(router)
        response = client.post("/api/presets/list")
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    body = response.json()
    assert body["error"] is None
    assert [p["slug"] for p in body["presets"]] == ["ds-fast", "my-tools"]  # newest first; ours and archived left out
    mine = {p["slug"]: p for p in body["presets"]}
    assert mine["my-tools"]["models"] == ["a/m"] and mine["my-tools"]["providers"] == ["deepinfra"]
    assert mine["my-tools"]["version"] == 3
    assert mine["ds-fast"]["models"] == ["b/m", "c/m"]


def test_a_preset_whose_detail_cannot_be_read_is_still_listed(client):
    _own()
    with respx.mock(assert_all_called=False) as router:
        _listing(router)
        router.get(f"{BASE}/presets/my-tools").mock(return_value=httpx.Response(500, json={"error": "boom"}))
        body = client.post("/api/presets/list").json()
    row = next(p for p in body["presets"] if p["slug"] == "my-tools")
    assert row["models"] == [] and row["version"] is None


def test_the_list_says_why_it_is_empty(client):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/presets").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
        body = client.post("/api/presets/list").json()
    assert body["presets"] == [] and "HTTP 401" in body["error"]
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"api_key_enc": None})
    assert "Add an OpenRouter API key" in client.post("/api/presets/list").json()["error"]


# ---- cost and value limits ------------------------------------------------------------------------------------


def _ranked(**patch) -> list[str]:
    with session_scope() as session:
        settings = load_settings(session).model_copy(deep=True)
        item = settings.presets.items[0]
        for key, value in patch.items():
            setattr(item, key, value)
        ids = [p.id for p in session.query(ProviderConfig).filter_by(type="openrouter")]
        return [c.model_id for c in rank(session, settings, ids, item)[0]]


def test_there_is_no_cost_or_value_limit_until_one_is_set(client):
    assert PresetSettings().max_task_cost is None and PresetSettings().min_value is None
    assert _ranked() == ["acme/best", "acme/steady", "acme/pricey"]


def test_a_cost_limit_leaves_out_models_that_cost_more_for_one_task(client):
    # a task of the default profile costs acme/best $0.068, acme/steady $0.0714 and acme/pricey $0.68
    assert _ranked(max_task_cost=0.07) == ["acme/best"]
    assert _ranked(max_task_cost=0.0714) == ["acme/best", "acme/steady"]
    assert _ranked(max_task_cost=5) == ["acme/best", "acme/steady", "acme/pricey"]
    assert _ranked(max_task_cost=0.01) == []


def test_a_value_floor_leaves_out_models_that_give_less_score_per_dollar(client):
    # acme/best gives 80 / 0.068 = 1176 points per dollar, acme/steady 1120 and acme/pricey 132
    assert _ranked(min_value=1150) == ["acme/best"]
    assert _ranked(min_value=1000) == ["acme/best", "acme/steady"]
    assert _ranked(min_value=0) == ["acme/best", "acme/steady", "acme/pricey"]
    assert _ranked(min_value=5000) == []


def test_the_two_limits_apply_together(client):
    assert _ranked(max_task_cost=0.5, min_value=1000) == ["acme/best", "acme/steady"]
    assert _ranked(max_task_cost=0.07, min_value=1150) == ["acme/best"]
    assert _ranked(max_task_cost=0.07, min_value=1500) == []


def test_the_limits_are_saved_and_can_be_cleared(client):
    current = client.get("/api/settings").json()["settings"]
    on = _with_item(current, max_task_cost=0.05, min_value=2000)
    saved = client.put("/api/settings", json={"settings": on}).json()["settings"]["presets"]["items"][0]
    assert (saved["max_task_cost"], saved["min_value"]) == (0.05, 2000)
    off = _with_item(current, max_task_cost=None, min_value=None)
    cleared = client.put("/api/settings", json={"settings": off}).json()["settings"]["presets"]["items"][0]
    assert cleared["max_task_cost"] is None and cleared["min_value"] is None
    for field in ("max_task_cost", "min_value"):
        bad = _with_item(current, **{field: -1})
        assert client.put("/api/settings", json={"settings": bad}).status_code == 422, field


def test_a_plan_with_nothing_left_after_the_limits_names_them(client):
    _settings(min_value=5000)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["action"] == "blocked" and plan["considered"] == 0
    assert "value floor" in plan["reason"] and "cost limit" in plan["reason"]


# ---- preferring the model's own test scores, for one preset ---------------------------------------------------


def _tested_well(model_id: str = "acme/steady", tasks: int = 5) -> None:
    """Give a model weak third-party coding and agentic scores and a perfect run of its own tests.

    Blended with the benchmarks that earns it a composite near 65, below acme/best's 80; taken as it stands it
    is 94, which puts it ahead of acme/best despite costing 5% more.
    """
    canonical = _canonical(model_id)
    with session_scope() as session:
        provider = session.scalars(select(ProviderConfig).where(ProviderConfig.name == PROVIDER)).one()
        for source, metric, dimension in (
            ("epoch", "swe_bench_verified", "coding"),
            ("terminal-bench", "accuracy", "agentic"),
        ):
            session.add(
                BenchmarkScore(
                    source=source,
                    metric=metric,
                    dimension=dimension,
                    source_model=f"preset-test-{model_id}-{metric}",
                    canonical_id=canonical,
                    value=20,
                    percentile=20,
                )
            )
        run = EvalRun(status="done", suite="preset-test")
        session.add(run)
        session.flush()
        for n in range(tasks):
            session.add(
                EvalResult(
                    run_id=run.id,
                    provider_config_id=provider.id,
                    model_id=model_id,
                    canonical_id=canonical,
                    task_id=f"t{n}",
                    category="chat",
                    status="passed",
                    score=100.0,
                )
            )


def _prefer_globally(on: bool) -> None:
    with session_scope() as session:
        settings = load_settings(session).model_copy(deep=True)
        settings.scoring.prefer_own_tests = on
        save_settings(session, settings)


def _ranked_preferring(override: bool | None, globally: bool) -> list[str]:
    _prefer_globally(globally)
    return _ranked(prefer_own_tests=override)


def test_a_preset_can_prefer_the_models_own_tests_while_the_global_setting_does_not(client):
    _tested_well()
    assert _ranked_preferring(None, False) == ["acme/best", "acme/steady", "acme/pricey"]
    assert _ranked_preferring(True, False) == ["acme/steady", "acme/best", "acme/pricey"]


def test_a_preset_can_ignore_the_models_own_tests_while_the_global_setting_prefers_them(client):
    _tested_well()
    assert _ranked_preferring(None, True) == ["acme/steady", "acme/best", "acme/pricey"]
    assert _ranked_preferring(False, True) == ["acme/best", "acme/steady", "acme/pricey"]


def test_a_preset_with_no_choice_of_its_own_follows_the_global_setting(client):
    _tested_well()
    assert PresetSettings().prefer_own_tests is None
    assert _ranked_preferring(None, True) == _ranked_preferring(True, True)
    assert _ranked_preferring(None, False) == _ranked_preferring(False, False)


def test_the_preference_still_waits_for_the_minimum_number_of_graded_tasks(client):
    _tested_well(tasks=4)  # one short of the default five
    assert _ranked_preferring(True, False) == ["acme/best", "acme/steady", "acme/pricey"]


def test_the_choice_changes_which_model_the_plan_leads_with_and_the_plan_says_which_scores_it_used(client):
    _tested_well()
    _settings(prefer_own_tests=True)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/steady"
    assert plan["own_tests_preferred"] is True
    assert plan["config"]["model"] == "acme/steady"


@pytest.mark.parametrize(
    ("override", "globally", "effective"),
    [(None, False, False), (None, True, True), (True, False, True), (False, True, False)],
)
def test_the_plan_reports_the_value_that_was_in_force_not_the_presets_own_field(client, override, globally, effective):
    _settings(prefer_own_tests=override)
    _prefer_globally(globally)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        assert _plan(client)["own_tests_preferred"] is effective


def test_a_draft_is_ranked_with_its_own_choice(client):
    _tested_well()
    saved = _saved_presets(client)
    assert saved["prefer_own_tests"] is None
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        saved_plan = _plan(client)
        draft = client.post("/api/presets/plan", json={"settings": {**saved, "prefer_own_tests": True}}).json()
    assert (saved_plan["pick"]["model_id"], saved_plan["own_tests_preferred"]) == ("acme/best", False)
    assert (draft["pick"]["model_id"], draft["own_tests_preferred"]) == ("acme/steady", True)
    assert draft["draft"] is True
    assert _saved_presets(client)["prefer_own_tests"] is None  # previewing kept nothing


# ---- previewing settings that are not saved -------------------------------------------------------------------


def _saved_presets(client: TestClient) -> dict:
    """The first saved preset, which is what the dry-run tests preview."""
    return client.get("/api/settings").json()["settings"]["presets"]["items"][0]


def test_a_plan_from_the_saved_settings_says_it_is_not_a_draft(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        assert _plan(client)["draft"] is False


def test_settings_can_be_previewed_without_saving_them(client):
    saved = _saved_presets(client)
    draft = {**saved, "max_models": 3}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        response = client.post("/api/presets/plan", json={"settings": draft})
        assert post.call_count == 0
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    plan = response.json()
    assert plan["draft"] is True
    assert [c["model_id"] for c in plan["picks"]] == ["acme/best", "acme/steady", "acme/pricey"]
    assert _saved_presets(client)["max_models"] == saved["max_models"] == 1  # nothing was kept
    assert client.get("/api/presets/state").json()["owned"] == []


def test_a_draft_is_tried_with_its_own_limits_and_name(client):
    saved = _saved_presets(client)
    draft = {**saved, "max_task_cost": 0.07, "slug": "Try this"}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, "vibonomics-try-this", None)
        plan = client.post("/api/presets/plan", json={"settings": draft}).json()
    assert plan["slug"] == "vibonomics-try-this"
    assert plan["action"] == "create"
    assert [c["model_id"] for c in plan["picks"]] == ["acme/best"]
    assert _saved_presets(client)["slug"] == SLUG


def test_a_draft_that_is_not_valid_is_refused(client):
    saved = _saved_presets(client)
    for patch in ({"slug": ""}, {"max_models": 4}, {"min_value": -5}):
        response = client.post("/api/presets/plan", json={"settings": {**saved, **patch}})
        assert response.status_code == 422, patch


def test_a_draft_plan_cannot_be_applied_because_apply_plans_from_what_is_saved(client):
    saved = _saved_presets(client)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        draft = client.post("/api/presets/plan", json={"settings": {**saved, "max_models": 3}}).json()
        response = _apply(client, draft)
        assert post.call_count == 0
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


# ---- who owns a preset ----------------------------------------------------------------------------------------

OTHER_KEY = "sk-or-test-other-key-0987654321"


def _state() -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(Setting, STATE_KEY)
        return dict(row.value) if row is not None and row.value else {}


def _own_as(key: str = KEY, slug: str = SLUG, **record: Any) -> None:
    """Record ``slug`` as created under ``key``, the way a successful write now does."""
    owner = {"fingerprint": owner_fingerprint(BASE, key), "version_at_create": 1, **record}
    with session_scope() as session:
        session.merge(Setting(key=STATE_KEY, value={"owned": [slug], "owners": {slug: owner}}))


def _use_key(key: str) -> None:
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"api_key_enc": crypto.encrypt(key)})


def test_a_preset_made_under_another_key_is_left_alone(client):
    _own_as(KEY)
    _use_key(OTHER_KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "someone/elses"}, version=3)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert post.call_count == 0
    assert plan["action"] == "blocked"
    assert "different OpenRouter key or account" in plan["reason"]
    assert plan["request"] is None and plan["digest"] is None
    assert OTHER_KEY not in json.dumps(plan) and KEY not in json.dumps(plan)


def test_a_preset_made_under_the_key_in_use_is_still_ours(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=3)
        plan = _plan(client)
    assert plan["action"] == "update"
    assert plan["digest"]


def test_a_slug_recorded_before_accounts_were_tracked_is_trusted_once_and_then_tied_to_the_key(client, caplog):
    _own()  # the form the live instance holds: a bare slug and no record
    with caplog.at_level("INFO", logger="vibonomics.presets"), respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=3)
        assert _plan(client)["action"] == "update"
    assert _state()["owners"][SLUG]["fingerprint"] == owner_fingerprint(BASE, KEY)
    assert any("adopt" in r.getMessage() and SLUG in r.getMessage() for r in caplog.records)
    assert KEY not in json.dumps(_state())
    # the trust is spent: a different key no longer reaches it
    _use_key(OTHER_KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "someone/elses"}, version=3)
        plan = _plan(client)
    assert plan["action"] == "blocked" and "different OpenRouter key or account" in plan["reason"]


def test_a_preset_deleted_and_made_again_by_hand_is_no_longer_ours(client):
    _own_as(KEY, preset_id="p-1", created_at="2026-11-01T00:00:00Z")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "by/hand"}, version=1, id="p-2", created_at="2026-11-04T00:00:00Z")
        plan = _plan(client)
    assert plan["action"] == "blocked" and "no longer the one Vibonomics created" in plan["reason"]
    # the same id is the same preset, and a creation time written another way is the same instant
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=4, id="p-1", created_at="2026-11-01T00:00:00+00:00")
        assert _plan(client)["action"] == "update"
    # a creation time alone is enough to tell them apart
    _own_as(KEY, created_at="2026-11-01T00:00:00Z")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "by/hand"}, version=1, created_at="2026-11-04T00:00:00Z")
        assert _plan(client)["action"] == "blocked"


def test_a_response_with_no_id_or_creation_time_leaves_the_key_to_decide(client):
    _own_as(KEY, preset_id="p-1", created_at="2026-11-01T00:00:00Z")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)  # the live preset names neither
        assert _plan(client)["action"] == "update"


def test_a_create_records_the_account_and_what_openrouter_called_the_preset(client):
    def create(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        sent.pop("messages", None)
        return httpx.Response(200, json=_preset(SLUG, sent, version=1, id="p-1", created_at="2026-11-05T00:00:00Z"))

    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=create)
        plan = _plan(client)
        assert _apply(client, plan).status_code == 200
    state = _state()
    assert state["owned"] == [SLUG]
    owner = state["owners"][SLUG]
    assert owner["fingerprint"] == owner_fingerprint(BASE, KEY)
    assert (owner["version_at_create"], owner["preset_id"], owner["created_at"]) == (1, "p-1", "2026-11-05T00:00:00Z")
    assert KEY not in json.dumps(state)


def test_a_create_records_the_id_and_time_a_later_read_will_report_not_the_answers_wording(client):
    def create(request: httpx.Request) -> httpx.Response:
        account.take(request)
        return httpx.Response(200, json=_preset(SLUG, account.written, version=1, id="create-1", created_at="t0"))

    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None, id="get-1", created_at="2026-11-05T00:00:01Z")
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=create)
        plan = _plan(client)
        assert _apply(client, plan).status_code == 200
        owner = _state()["owners"][SLUG]
        assert (owner["preset_id"], owner["created_at"]) == ("get-1", "2026-11-05T00:00:01Z")
        assert _plan(client)["action"] == "unchanged"  # and our own preset is not blocked as replaced


def test_the_digest_names_the_account_and_the_version_it_was_planned_against(client):
    config = {"model": "acme/best"}
    assert digest_of(SLUG, config, "aaaa", 1) != digest_of(SLUG, config, "bbbb", 1)
    assert digest_of(SLUG, config, "aaaa", 1) != digest_of(SLUG, config, "aaaa", 2)
    _own_as(KEY)
    digests = []
    for version in (2, 3):
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, {"model": "acme/best"}, version=version)
            digests.append(_plan(client)["digest"])
    assert digests[0] != digests[1]  # someone made a version in between, so the reviewed plan is stale


# ---- a write that did not report itself, and a preset that cannot be read ------------------------------------


def _raw(router: respx.Router, slug: str, data: dict[str, Any]) -> None:
    """The preset GET answers 200 with exactly ``data``."""
    url = f"{BASE}/presets/{slug}"
    router.get(url).mock(return_value=httpx.Response(200, json={"data": data}))
    router.get(f"{url}/versions").mock(return_value=httpx.Response(200, json={"data": []}))


def test_a_preset_whose_live_version_cannot_be_read_blocks_the_plan_instead_of_planning_from_nothing(client):
    _own_as(KEY)
    for data in (
        {"slug": SLUG, "status": "active"},  # no designated version at all
        {"slug": SLUG, "designated_version": {"version": 2}},  # a version with no config
        {"slug": SLUG, "designated_version": {"version": 2, "config": "not a dict"}},
    ):
        with respx.mock(assert_all_called=False) as router:
            _raw(router, SLUG, data)
            plan = _plan(client)
        assert plan["action"] == "blocked", data
        assert "could not read" in plan["reason"] and "live version" in plan["reason"]
        assert plan["request"] is None


def test_a_preset_with_an_empty_config_can_still_be_planned(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _raw(router, SLUG, {"slug": SLUG, "designated_version": {"version": 2, "config": {}}})
        assert _plan(client)["action"] == "update"


def test_a_template_whose_live_version_cannot_be_read_blocks_the_plan(client):
    _settings(template_slug="my-tools")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _raw(router, "my-tools", {"slug": "my-tools"})
        plan = _plan(client)
    assert plan["action"] == "blocked" and "my-tools" in plan["reason"] and "live version" in plan["reason"]


def _answers(payload: dict[str, Any]):
    return lambda request: httpx.Response(200, json=payload)


def test_a_create_whose_answer_carries_no_version_is_a_failed_write_and_claims_nothing(client):
    bodies = (
        {"error": {"message": f"No credits {KEY}", "code": 402}},
        {"data": {"slug": SLUG, "name": SLUG}},  # no designated version
        {"data": {"slug": SLUG, "designated_version": {"config": {"model": "acme/best"}}}},  # no version number
        {"data": {"slug": SLUG, "designated_version": {"version": 1}}},  # no config
        {"data": {"slug": "someone-else", "designated_version": {"version": 1, "config": {"model": "acme/best"}}}},
    )
    for body in bodies:
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, None)
            post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_answers(body))
            plan = _plan(client)
            response = _apply(client, plan)
            assert post.call_count == 1  # and never retried
        assert response.status_code == 502, body
        assert KEY not in response.text
        state = _slug_state(client)
        assert state["last_status"] == "failed", body
        assert client.get("/api/presets/state").json()["owned"] == [] and state["version"] is None, body
        assert "owners" not in _state()


def test_an_update_whose_answer_carries_no_version_does_not_count_as_written(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_answers({"data": {}}))
        plan = _plan(client)
        response = _apply(client, plan)
    assert response.status_code == 502
    state = _slug_state(client)
    assert state["last_status"] == "failed" and state["version"] is None


# ---- a write whose answer was lost -----------------------------------------------------------------------------


class _Account:
    """An OpenRouter account that has the preset only once the write has gone through, whatever the answer was."""

    def __init__(self, router: respx.Router, before: dict[str, Any] | None, version: int = 1, **ids: Any) -> None:
        self.before, self.version, self.ids = before, version, ids
        self.written: dict[str, Any] | None = None
        self.reads = router.get(f"{BASE}/presets/{SLUG}").mock(side_effect=self._get)
        router.get(f"{BASE}/presets/{SLUG}/versions").mock(return_value=httpx.Response(200, json={"data": []}))

    def take(self, request: httpx.Request) -> None:
        sent = json.loads(request.content)
        sent.pop("messages", None)
        self.written, self.version = sent, self.version + (0 if self.before is None else 1)

    def _get(self, request: httpx.Request) -> httpx.Response:
        config = self.written if self.written is not None else self.before
        if config is None:
            return httpx.Response(404, json={"error": {"message": "Preset not found"}})
        return httpx.Response(200, json=_preset(SLUG, config, version=self.version, **self.ids))


def _lost_after_taking(account: _Account, how):
    def post(request: httpx.Request) -> httpx.Response:
        account.take(request)
        return how(request)

    return post


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out", request=request)


def test_a_create_whose_answer_never_arrived_is_found_by_reading_it_back_and_claimed(client):
    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None, id="p-1", created_at="2026-11-05T00:00:00Z")
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(
            side_effect=_lost_after_taking(account, _timeout)
        )
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 1  # never retried
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is True and body["version"] == 1 and body["unsaved"] == []
    assert "read back" in body["message"]
    state = _state()
    assert state["owned"] == [SLUG]
    assert state["owners"][SLUG]["fingerprint"] == owner_fingerprint(BASE, KEY)
    assert state["owners"][SLUG]["preset_id"] == "p-1"
    assert _slug_state(client)["last_status"] == "applied"
    # the next plan is a normal one, not a block on a preset that "Vibonomics did not create"
    with respx.mock(assert_all_called=False) as router:
        _Account(router, account.written, version=1, id="p-1", created_at="2026-11-05T00:00:00Z")
        assert _plan(client)["action"] == "unchanged"


def test_a_body_that_is_not_json_is_read_back_the_same_way(client):
    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(
            side_effect=_lost_after_taking(account, lambda request: httpx.Response(200, text="<html>gateway</html>"))
        )
        plan = _plan(client)
        response = _apply(client, plan)
    assert response.status_code == 200 and response.json()["applied"] is True
    assert _state()["owned"] == [SLUG]


def test_an_update_whose_answer_was_lost_is_reported_as_applied_when_the_preset_has_the_plan(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, {"model": "acme/best"}, version=2)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(
            side_effect=_lost_after_taking(account, lambda request: httpx.Response(504, text="gateway timeout"))
        )
        plan = _plan(client)
        response = _apply(client, plan)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True and response.json()["version"] == 3
    assert _slug_state(client)["last_status"] == "applied"


def test_a_lost_answer_is_a_failure_when_the_preset_is_not_what_the_plan_wrote(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    with respx.mock(assert_all_called=False) as router:
        # the preset is not there when the apply plans, and someone has made one of their own by the time it re-reads
        reads = iter([404, 200])

        def get(request: httpx.Request) -> httpx.Response:
            if next(reads) == 404:
                return httpx.Response(404, json={"error": {"message": "Preset not found"}})
            return httpx.Response(200, json=_preset(SLUG, {"model": "by/hand"}, version=1))

        router.get(f"{BASE}/presets/{SLUG}").mock(side_effect=get)
        router.get(f"{BASE}/presets/{SLUG}/versions").mock(return_value=httpx.Response(200, json={"data": []}))
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_timeout)
        response = _apply(client, plan)
    assert response.status_code == 502
    assert "did not take the update" in response.json()["detail"]
    assert client.get("/api/presets/state").json()["owned"] == []
    assert _slug_state(client)["last_status"] == "failed"


def test_a_refusal_is_not_read_back(client):
    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(
            side_effect=_lost_after_taking(account, lambda request: httpx.Response(400, json={"error": "bad"}))
        )
        plan = _plan(client)
        reads = account.reads.call_count
        response = _apply(client, plan)
        assert account.reads.call_count == reads + 1  # the apply's own plan, and no read after the 400
    assert response.status_code == 502


async def test_a_write_that_is_cancelled_is_still_claimed_when_it_went_through(client, monkeypatch):
    from vibonomics import http, presets

    async def cancelled(method: str, url: str, **kwargs: Any):
        account.take(httpx.Request(method, url, content=json.dumps(kwargs["json_body"]).encode()))
        raise asyncio.CancelledError

    await http.close_client()
    try:
        with respx.mock(assert_all_called=False) as router:
            account = _Account(router, None, id="p-1")
            monkeypatch.setattr(presets, "request", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await presets.apply(SLUG, None, "schedule")
    finally:
        await http.close_client()
    assert _state()["owned"] == [SLUG]
    assert _state()["last"][SLUG]["status"] == "applied"


# ---- keys Vibonomics does not own -------------------------------------------------------------------------------


def test_a_store_that_drops_a_default_does_not_make_every_sync_write_a_new_version(client):
    account: _Account

    def stores_less(request: httpx.Request) -> httpx.Response:
        account.take(request)
        assert account.written is not None
        (account.written.get("provider") or {}).pop("allow_fallbacks", None)  # whatever it does not keep
        return httpx.Response(200, json=_preset(SLUG, account.written, version=1, id="p-1"))

    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None, id="p-1")
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=stores_less)
        plan = _plan(client)
        assert "allow_fallbacks" not in json.dumps(plan["config"])  # OpenRouter's own default stands
        assert _apply(client, plan).json()["applied"] is True
        for _ in range(2):
            again = _plan(client)
            assert again["action"] == "unchanged" and again["changes"] == []
            assert _apply(client, again).json()["applied"] is False
        assert post.call_count == 1


def test_only_the_keys_vibonomics_owns_decide_whether_the_preset_changes(client):
    _own_as(KEY)
    tags = ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]
    live = {
        "model": "acme/best",
        "provider": {"only": tags, "order": tags, "allow_fallbacks": False, "data_collection": "deny"},
        "tools": [{"type": "openrouter:web_search"}],
    }
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=3)
        assert _plan(client)["action"] == "unchanged"
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {**live, "provider": {**live["provider"], "order": tags[::-1]}}, version=3)
        plan = _plan(client)
    assert plan["action"] == "update" and [c["path"] for c in plan["changes"]] == ["provider.order"]
    # and the whole config, the operator's keys included, is still what is sent
    assert plan["config"]["provider"]["allow_fallbacks"] is False and plan["config"]["tools"] == live["tools"]


# ---- the key goes to OpenRouter and nowhere else ---------------------------------------------------------------

GATEWAY = "https://gateway.example.net/v1"


def test_a_base_url_override_on_the_provider_row_never_receives_the_key(client):
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"base_url": GATEWAY})
    with respx.mock(assert_all_called=False) as router:
        gateway = router.route(host="gateway.example.net").mock(return_value=httpx.Response(200, json={"data": []}))
        _remote(router, SLUG, None)
        write = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        router.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={"data": [], "total_count": 0}))
        plan = _plan(client)
        assert _apply(client, plan).status_code == 200
        assert client.post("/api/presets/list").status_code == 200
        assert gateway.call_count == 0
        assert {call.request.url.host for call in router.calls} == {"openrouter.ai"}
        assert write.call_count == 1 and write.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    assert plan["request"]["url"].startswith(BASE)
    assert any("Base URL" in w and "gateway.example.net" in w for w in plan["warnings"])


# ---- churn: equal prices, and a lookup that failed -------------------------------------------------------------


def _tags(chosen: list[dict[str, Any]]) -> list[str]:
    return [e["tag"] for e in chosen]


def test_endpoints_that_cost_the_same_keep_the_order_the_preset_already_has():
    a = {"tag": "a/x", "input": 1, "output": 1, "uptime_1d": 99}
    b = {"tag": "b/x", "input": 1, "output": 1, "uptime_1d": 60}
    cheaper = {"tag": "c/x", "input": 0.5, "output": 1, "uptime_1d": 10}
    assert _tags(choose_endpoints([a, b], TASK, {}, 2)) == ["a/x", "b/x"]  # no history: the steadier first
    assert _tags(choose_endpoints([a, b], TASK, {}, 2, current=["b/x", "a/x"])) == ["b/x", "a/x"]
    assert _tags(choose_endpoints([a, b], TASK, {}, 2, current=["b/x"])) == ["b/x", "a/x"]
    # a price difference still wins over the history
    assert _tags(choose_endpoints([a, b, cheaper], TASK, {}, 3, current=["b/x", "a/x"]))[0] == "c/x"


def test_a_models_refresh_that_reorders_equal_priced_providers_writes_no_new_version(client):
    _own_as(KEY)
    _give_endpoints("acme/best", [("a/fp8", 0.1, 0.4, 99), ("b/fp8", 0.1, 0.4, 60)])
    live = {"model": "acme/best", "provider": {"only": ["b/fp8", "a/fp8"], "order": ["b/fp8", "a/fp8"]}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=3)
        plan = _plan(client)
    assert plan["action"] == "unchanged" and plan["config"]["provider"]["order"] == ["b/fp8", "a/fp8"]


def _stores(account: _Account):
    def post(request: httpx.Request) -> httpx.Response:
        account.take(request)
        return httpx.Response(200, json=_preset(SLUG, account.written, version=1, id="p-1"))

    return post


def test_a_failed_endpoint_lookup_does_not_unpin_the_preset(client):
    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None, id="p-1")
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_stores(account))
        plan = _plan(client)
        assert _apply(client, plan).json()["applied"] is True
    pinned = account.written["provider"]
    assert pinned["only"] and _state()["pins"] == {SLUG: ["acme/best"]}

    _set_extra("acme/best", {})  # the next refresh could not read the endpoints
    with respx.mock(assert_all_called=False) as router:
        _Account(router, account.written, id="p-1")
        write = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert plan["action"] == "unchanged" and plan["changes"] == []
        assert plan["config"] == account.written  # what stays live, not an unpinned copy of it
        assert any(
            "best" in w and "provider data" in w and "keeps its current providers" in w for w in plan["warnings"]
        )
        body = _apply(client, plan).json()
        assert body["applied"] is False and "provider data" in body["message"]
        assert write.call_count == 0
    assert _slug_state(client)["last_status"] == "unchanged"
    assert _state()["pins"] == {SLUG: ["acme/best"]}  # still remembered, for when the data is back

    _set_extra("acme/best", {"endpoints": ENDPOINTS})  # and the next good refresh puts it back
    with respx.mock(assert_all_called=False) as router:
        _Account(router, account.written, id="p-1")
        plan = _plan(client)
    assert plan["action"] == "unchanged" and not plan["warnings"]


def test_a_model_that_never_had_provider_data_does_not_hold_the_preset_pinned(client):
    _settings(max_models=2)
    _own_as(KEY)
    live = {
        "model": "acme/best",
        "models": ["acme/best", "acme/steady"],
        "provider": {"only": ["novita/fp4"], "order": ["novita/fp4"]},
    }
    for pins in (None, {SLUG: ["acme/best"]}):  # an older write, and a write that knew steady had no data
        with session_scope() as session:
            session.merge(Setting(key=STATE_KEY, value={**_state(), **({"pins": pins} if pins else {})}))
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, live, version=3)
            plan = _plan(client)
        assert plan["action"] == "update", pins  # `only` would keep steady from being served: unpin it
        assert "provider" not in plan["config"] or "only" not in plan["config"]["provider"]


# ---- several presets: state and the engine by slug --------------------------------------------------------------

SECOND = "vibonomics-second"


def _items(*items: PresetSettings, **shared: Any) -> None:
    """Save these presets as the managed ones, with the settings that apply to all of them."""
    with session_scope() as session:
        save_settings(session, AppSettings(presets=PresetsSettings(items=list(items), **shared)))


def _item(slug: str = SLUG, **fields: Any) -> PresetSettings:
    return PresetSettings(**{"enabled": True, "min_context": 100_000, "slug": slug, **fields})


def _creates(router: respx.Router, slug: str, **ids: Any) -> respx.Route:
    """A write to ``slug`` that the account takes, answering the way OpenRouter does."""

    def create(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        sent.pop("messages", None)
        return httpx.Response(200, json=_preset(slug, sent, version=1, **ids))

    return router.post(f"{BASE}/presets/{slug}/chat/completions").mock(side_effect=create)


def test_state_an_older_release_kept_as_one_record_is_read_as_the_record_of_the_preset_it_names(client):
    old = {
        "owned": [SLUG],
        "applied": {"slug": SLUG, "model_id": "acme/best", "version": 3, "at": "2026-11-01T00:00:00+00:00"},
        "last": {
            "slug": SLUG,
            "at": "2026-11-02T00:00:00+00:00",
            "status": "unchanged",
            "message": "Already up to date.",
            "trigger": "schedule",
        },
    }
    with session_scope() as session:
        session.merge(Setting(key=STATE_KEY, value=old))

    body = client.get("/api/presets/state").json()

    assert body["owned"] == [SLUG]
    assert set(body["slugs"]) == {SLUG}
    assert body["slugs"][SLUG]["version"] == 3 and body["slugs"][SLUG]["model_id"] == "acme/best"
    assert (body["slugs"][SLUG]["last_status"], body["slugs"][SLUG]["last_trigger"]) == ("unchanged", "schedule")
    # and the first write rewrites it in the new shape
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=3)
        _creates(router, SLUG)
        assert _apply(client, _plan(client)).status_code == 200
    stored = _state()
    assert stored["last"][SLUG]["status"] == "applied" and stored["applied"][SLUG]["model_id"] == "acme/best"


def test_applying_one_preset_leaves_the_record_of_another_alone(client):
    _items(_item(), _item(SECOND, max_models=2))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, SECOND, None)
        _creates(router, SLUG)
        second = _creates(router, SECOND)
        assert _apply(client, _plan(client)).status_code == 200
        before = _slug_state(client)
        stored = _state()

        plan = _plan(client, SECOND)
        assert plan["slug"] == SECOND and [c["model_id"] for c in plan["picks"]] == ["acme/best", "acme/steady"]
        assert _apply(client, plan, SECOND).status_code == 200
        assert second.call_count == 1

    assert _slug_state(client) == before
    after = _state()
    assert after["owned"] == [SLUG, SECOND]
    assert after["applied"][SLUG] == stored["applied"][SLUG] and after["last"][SLUG] == stored["last"][SLUG]
    assert after["owners"][SLUG] == stored["owners"][SLUG] and after["pins"][SLUG] == stored["pins"][SLUG]
    assert after["applied"][SECOND]["model_id"] == "acme/best"
    slugs = client.get("/api/presets/state").json()["slugs"]
    assert slugs[SECOND]["last_status"] == "applied" and slugs[SECOND]["last_trigger"] == "manual"


def test_a_failed_write_to_one_preset_is_recorded_for_that_preset_only(client):
    _items(_item(), _item(SECOND))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, SECOND, None)
        _creates(router, SLUG)
        router.post(f"{BASE}/presets/{SECOND}/chat/completions").mock(return_value=httpx.Response(400, json={}))
        assert _apply(client, _plan(client)).status_code == 200
        assert _apply(client, _plan(client, SECOND), SECOND).status_code == 502

    assert _slug_state(client)["last_status"] == "applied"
    assert _slug_state(client, SECOND)["last_status"] == "failed"
    assert client.get("/api/presets/state").json()["owned"] == [SLUG]


def test_each_preset_is_planned_with_its_own_settings(client):
    _items(_item(max_models=1), _item(SECOND, max_models=3))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, SECOND, None)
        first, second = _plan(client), _plan(client, SECOND)

    assert [c["model_id"] for c in first["picks"]] == ["acme/best"]
    assert [c["model_id"] for c in second["picks"]] == ["acme/best", "acme/steady", "acme/pricey"]
    assert first["slug"] == SLUG and second["slug"] == SECOND


def test_a_plan_needs_a_preset_named_either_by_slug_or_by_settings(client):
    assert client.post("/api/presets/plan", json={}).status_code == 422
    assert client.post("/api/presets/plan").status_code == 422
    assert client.post("/api/presets/plan", json={"slug": "vibonomics-nobody"}).status_code == 404
    assert client.post("/api/presets/apply", json={"slug": "vibonomics-nobody", "digest": "x"}).status_code == 404


def test_a_draft_for_a_preset_that_is_not_saved_previews_and_can_never_be_applied(client):
    draft = PresetSettings(slug="Brand new", enabled=True, min_context=100_000).model_dump(mode="json")
    new_slug = "vibonomics-brand-new"
    with respx.mock(assert_all_called=False) as router:
        _remote(router, new_slug, None)
        post = _creates(router, new_slug)
        plan = client.post("/api/presets/plan", json={"settings": draft}).json()
        assert plan["slug"] == new_slug and plan["draft"] is True and plan["action"] == "create"
        assert plan["digest"]
        assert _apply(client, plan, new_slug).status_code == 404
        assert post.call_count == 0
    assert client.get("/api/presets/state").json()["owned"] == []


def test_a_preset_switched_off_is_not_written_while_another_is_on(client):
    _items(_item(enabled=False), _item(SECOND))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, SECOND, None)
        off = _creates(router, SLUG)
        on = _creates(router, SECOND)
        assert _apply(client, _plan(client)).status_code == 409
        assert _apply(client, _plan(client, SECOND), SECOND).status_code == 200
        assert (off.call_count, on.call_count) == (0, 1)


# ---- blocklists -------------------------------------------------------------------------------------------------


def _ranked_with(
    models: list[str] = (),  # type: ignore[assignment]
    providers: list[str] = (),  # type: ignore[assignment]
    *,
    everywhere_models: list[str] = (),  # type: ignore[assignment]
    everywhere_providers: list[str] = (),  # type: ignore[assignment]
    **patch: Any,
) -> tuple[list[str], Excluded]:
    """The ranking with these blocklists on the preset and (the ``everywhere_`` ones) on every preset."""
    with session_scope() as session:
        settings = load_settings(session).model_copy(deep=True)
        item = settings.presets.items[0]
        item.blocked_models, item.blocked_providers = list(models), list(providers)
        settings.presets.blocked_models = list(everywhere_models)
        settings.presets.blocked_providers = list(everywhere_providers)
        for key, value in patch.items():
            setattr(item, key, value)
        ids = [p.id for p in session.query(ProviderConfig).filter_by(type="openrouter")]
        excluded = Excluded()
        ranked = rank(session, settings, ids, item, excluded)[0]
        return [c.model_id for c in ranked], excluded


def test_a_blocked_model_is_never_a_candidate_whichever_way_it_is_written(client):
    everyone = ["acme/best", "acme/steady", "acme/pricey"]
    assert _ranked_with()[0] == everyone
    assert _ranked_with(["acme/best"])[0] == ["acme/steady", "acme/pricey"]  # exact
    assert _ranked_with(["ACME/Best"])[0] == ["acme/steady", "acme/pricey"]  # in any case
    assert _ranked_with(["acme/pri*", "*steady"])[0] == ["acme/best"]  # patterns
    assert _ranked_with(["acme/*"])[0] == []
    # the id OpenRouter uses is not the only name: the canonical id counts too
    assert _ranked_with(["preset-test/steady"])[0] == ["acme/best", "acme/pricey"]
    assert _ranked_with(["*:free"], include_free=True)[0] == everyone  # the free offer goes, the rest stay
    assert "acme/free:free" in _ranked_with(include_free=True)[0]
    assert "acme/free:free" not in _ranked_with(["*:free"], include_free=True)[0]


def test_the_list_for_every_preset_and_the_list_for_this_one_are_added_together(client):
    ranked, excluded = _ranked_with(["acme/best"], everywhere_models=["acme/steady"])

    assert ranked == ["acme/pricey"]
    assert excluded.models == 2


def test_the_exclusions_count_only_models_that_would_otherwise_have_been_candidates(client):
    # acme/low scores 40 and acme/gone is inactive: blocking them removes nothing the run would have considered
    ranked, excluded = _ranked_with(["acme/low", "acme/gone", "acme/pricey"])

    assert ranked == ["acme/best", "acme/steady"]
    assert excluded.models == 1


def test_a_provider_pattern_matches_the_tag_the_provider_part_or_the_display_name(client):
    for pattern in ("novita/fp4", "novita", "NOVITA", "nov*", "*/fp4"):
        assert [e for e in choose_endpoints(ENDPOINTS, TASK, {}, 5, blocked=[pattern]) if "novita" in e["tag"]] == []
        assert len(choose_endpoints(ENDPOINTS, TASK, {}, 5, blocked=[pattern])) == 3, pattern
    # the display name is its own name for the provider: a tag that says otherwise still matches it
    renamed = [{"provider": "Fancy Cloud", "tag": "fc1/fp8", "input": 1, "output": 1}]
    assert choose_endpoints(renamed, TASK, {}, 5, blocked=["fancy cloud"]) == []
    assert choose_endpoints(renamed, TASK, {}, 5, blocked=["fancy"]) != []  # no wildcard, no match


def test_a_blocked_provider_is_never_pinned_and_nothing_is_written_to_ignore_it(client):
    _settings(blocked_providers=["novita", "wafer/*"])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)

    assert [p["tag"] for p in plan["providers"]] == ["deepinfra/fp8", "slow/fp8"]
    assert plan["config"]["provider"]["only"] == plan["config"]["provider"]["order"] == ["deepinfra/fp8", "slow/fp8"]
    assert "ignore" not in plan["config"]["provider"]  # blocking is the app's own filter, not the preset's
    assert plan["excluded"] == {"models": 0, "providers": ["Novita", "Wafer"]}


def test_a_blocked_provider_in_the_list_for_every_preset_counts_too(client):
    _items(_item(), _item(SECOND), blocked_providers=["novita"])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, SECOND, None)
        first, second = _plan(client), _plan(client, SECOND)

    for plan in (first, second):
        assert "novita/fp4" not in [p["tag"] for p in plan["providers"]]


def test_a_model_whose_every_known_endpoint_is_blocked_is_left_out_but_one_with_no_data_is_not(client):
    everything = ["deepinfra", "wafer", "novita", "slow", "broken"]

    ranked, excluded = _ranked_with(providers=everything)
    assert ranked == ["acme/steady", "acme/pricey"]  # acme/best has endpoints and they are all blocked
    assert excluded.models == 1
    assert excluded.providers == ["Broken", "DeepInfra", "Novita", "Slow", "Wafer"]
    # one endpoint left is enough to keep it
    assert _ranked_with(providers=everything[:-1])[0] == ["acme/best", "acme/steady", "acme/pricey"]
    # and a block that matches no provider the model has touches nothing
    ranked, excluded = _ranked_with(providers=["nobody"])
    assert ranked == ["acme/best", "acme/steady", "acme/pricey"] and excluded == Excluded()


def test_a_blocked_incumbent_is_dropped_from_the_live_lineup(client):
    _own_as(KEY)
    _settings(max_models=2, blocked_models=["acme/best"])
    live = {"model": "acme/best", "models": ["acme/best", "acme/steady"]}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=3)
        plan = _plan(client)

    assert plan["action"] == "update"
    assert [c["model_id"] for c in plan["picks"]] == ["acme/steady", "acme/pricey"]
    assert plan["config"]["model"] == "acme/steady" and plan["config"]["models"] == ["acme/steady", "acme/pricey"]
    assert plan["reason"].startswith("Model list: adds pricey; drops acme/best.")
    assert plan["excluded"]["models"] == 1


def test_a_blocklist_does_not_give_a_blocked_models_margin_to_anything(client):
    # steady has been the lead; blocking it hands the lead to the best value without the switch margin's help
    _own_as(KEY)
    _settings(blocked_models=["acme/steady"], switch_margin_pct=50)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/steady"}, version=3)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/best" and plan["kept_current"] is False


def test_a_plan_whose_blocklists_leave_no_model_says_so(client):
    _settings(blocked_models=["acme/*"])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)

    assert plan["action"] == "blocked" and plan["considered"] == 0
    assert "blocklist" in plan["reason"]
    assert plan["excluded"]["models"] == 3


def test_a_plan_with_no_blocklist_excludes_nothing(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        assert _plan(client)["excluded"] == {"models": 0, "providers": []}


def test_blocklists_are_saved_with_the_settings_and_trimmed(client):
    current = client.get("/api/settings").json()["settings"]
    doc = _with_item(current, blocked_models=[" openai/* ", "", "*:free"], blocked_providers=["DeepInfra"])
    doc["presets"]["blocked_models"] = ["x-ai/*"]
    doc["presets"]["blocked_providers"] = ["slow*", "SLOW*"]

    saved = client.put("/api/settings", json={"settings": doc}).json()["settings"]["presets"]

    assert saved["items"][0]["blocked_models"] == ["openai/*", "*:free"]
    assert saved["items"][0]["blocked_providers"] == ["DeepInfra"]
    assert (saved["blocked_models"], saved["blocked_providers"]) == (["x-ai/*"], ["slow*"])


# ---- route ------------------------------------------------------------------------------------------------------

TAGS = ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]


def test_sort_writes_the_route_and_the_provider_sort_and_price_is_the_default():
    config = build_config({"model": "old", "tools": [1]}, ["a/m"], ["x/fp8"], route="sort", sort="latency")
    assert config == {
        "model": "a/m",
        "tools": [1],
        "route": "sort",
        "provider": {"only": ["x/fp8"], "order": ["x/fp8"], "sort": "latency"},
    }
    assert build_config(None, ["m"], [], route="sort") == {"model": "m", "route": "sort", "provider": {"sort": "price"}}
    assert build_config(None, ["m"], ["x"], route="sort", sort=None)["provider"]["sort"] == "price"


def test_fallback_writes_no_route_and_takes_away_one_a_live_preset_has():
    live = {"model": "old", "route": "sort", "provider": {"sort": "price", "only": ["x"], "quantizations": ["fp8"]}}

    config = build_config(live, ["a/m"], ["x/fp8"])

    assert "route" not in config
    assert config["provider"] == {"only": ["x/fp8"], "order": ["x/fp8"], "quantizations": ["fp8"]}
    assert "route" not in build_config(None, ["m"], [])
    assert live["route"] == "sort"  # the base is not edited in place


def test_the_route_and_the_sort_are_among_the_keys_the_app_decides():
    live = {"model": "a", "route": "sort", "provider": {"sort": "price", "only": ["x"], "data_collection": "deny"}}
    assert owned_view(live) == {
        "model": "a",
        "models": None,
        "route": "sort",
        "only": ["x"],
        "order": None,
        "sort": "price",
    }
    assert owned_view({"model": "a"})["route"] is None and owned_view({"model": "a"})["sort"] is None
    # a key that is not the app's does not show
    assert "data_collection" not in str(owned_view(live))


def test_the_diff_names_a_route_that_goes_and_a_sort_that_changes():
    before = {"model": "a", "route": "sort", "provider": {"sort": "price"}}
    fallback = build_config(before, ["a"], [])
    assert {c.path: (c.before, c.after) for c in diff_config(before, fallback)} == {
        "route": ('"sort"', None),
        "provider.sort": ('"price"', None),
    }
    after = {"model": "a", "route": "sort", "provider": {"sort": "latency"}}
    assert {c.path: (c.before, c.after) for c in diff_config(before, after)} == {
        "provider.sort": ('"price"', '"latency"')
    }


def test_a_route_openrouter_dropped_is_named_among_the_keys_it_did_not_keep():
    sent = {"model": "m", "route": "sort", "provider": {"sort": "price", "only": ["x"]}}
    assert unsaved_keys(sent, {"model": "m", "provider": {"sort": "price", "only": ["x"]}}) == ["route"]
    assert unsaved_keys(sent, {"model": "m", "route": "sort", "provider": {"only": ["x"]}}) == ["provider.sort"]
    assert unsaved_keys(sent, sent) == []


def _live_sorted(sort: str = "price", **extra: Any) -> dict[str, Any]:
    return {
        "model": "acme/best",
        "route": "sort",
        "provider": {"only": TAGS, "order": TAGS, "sort": sort, **extra},
    }


def test_a_plan_for_the_sort_route_writes_both_keys_and_says_which_route_it_is(client):
    _settings(route="sort", sort="throughput")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)

    assert plan["config"]["route"] == "sort" and plan["config"]["provider"]["sort"] == "throughput"
    assert (plan["route"], plan["sort"]) == ("sort", "throughput")
    assert plan["request"]["body"]["route"] == "sort"


def test_sort_without_a_choice_means_price(client):
    _settings(route="sort")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)

    assert plan["config"]["provider"]["sort"] == "price" and plan["sort"] is None


def test_a_preset_already_on_the_sort_route_is_unchanged_and_a_new_sort_is_an_update(client):
    _own_as(KEY)
    _settings(route="sort")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, _live_sorted(), version=2)
        assert _plan(client)["action"] == "unchanged"
    _settings(route="sort", sort="latency")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, _live_sorted(), version=2)
        plan = _plan(client)
    assert plan["action"] == "update" and [c["path"] for c in plan["changes"]] == ["provider.sort"]


def test_switching_to_fallback_takes_the_route_and_the_sort_off_the_live_preset(client):
    _own_as(KEY)
    live = _live_sorted(data_collection="deny")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=2)
        plan = _plan(client)

    assert plan["action"] == "update"
    assert {c["path"] for c in plan["changes"]} == {"route", "provider.sort"}
    assert "route" not in plan["config"] and "sort" not in plan["config"]["provider"]
    assert plan["config"]["provider"]["data_collection"] == "deny"  # the rest of the provider block is not touched
    assert any("route" in w and "sort" in w for w in plan["warnings"])


def test_a_preset_that_never_had_a_route_stays_unchanged_on_fallback(client):
    _own_as(KEY)
    desired = build_config(None, ["acme/best"], TAGS)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, desired, version=2)
        plan = _plan(client)
    assert plan["action"] == "unchanged" and plan["warnings"] == []


def test_a_store_that_drops_the_route_is_reported_and_does_not_write_a_new_version_every_sync(client):
    _settings(route="sort")
    account: _Account

    def stores_less(request: httpx.Request) -> httpx.Response:
        account.take(request)
        assert account.written is not None
        account.written.pop("route", None)  # whatever it does not keep
        return httpx.Response(200, json=_preset(SLUG, account.written, version=1, id="p-1"))

    with respx.mock(assert_all_called=False) as router:
        account = _Account(router, None, id="p-1")
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=stores_less)
        plan = _plan(client)
        assert plan["config"]["route"] == "sort"
        body = _apply(client, plan).json()
        assert body["applied"] is True and body["unsaved"] == ["route"]
        assert "OpenRouter did not keep: route" in body["message"]
        assert _state()["applied"][SLUG]["unsaved"] == ["route"]

        for _ in range(2):  # and every later sync leaves it be, instead of sending the same route again
            again = _plan(client)
            assert again["action"] == "unchanged" and again["changes"] == []
            assert any("did not keep" in w and "route" in w for w in again["warnings"])
            assert _apply(client, again).json()["applied"] is False
        assert post.call_count == 1


def test_a_changed_sort_is_written_even_while_the_route_is_one_openrouter_does_not_keep(client):
    _own_as(KEY)
    with session_scope() as session:
        session.merge(
            Setting(key=STATE_KEY, value={**_state(), "applied": {SLUG: {"slug": SLUG, "unsaved": ["route"]}}})
        )
    _settings(route="sort", sort="latency")
    live = {"model": "acme/best", "provider": {"only": TAGS, "order": TAGS, "sort": "price"}}  # no route kept
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=2)
        plan = _plan(client)
    assert plan["action"] == "update" and "provider.sort" in {c["path"] for c in plan["changes"]}


# ---- the passthrough: what the app sends back as it found it -------------------------------------------------------


def test_the_passthrough_is_every_key_the_app_does_not_decide_with_the_provider_block_opened_up():
    config = {
        "model": "a",
        "models": ["a", "b"],
        "route": "sort",
        "tools": [{"type": "openrouter:web_search"}],
        "reasoning": {"enabled": True},
        "provider": {
            "only": ["x"],
            "order": ["x"],
            "sort": "price",
            "quantizations": ["fp8"],
            "ignore": ["slow"],
            "data_collection": "deny",
            "allow_fallbacks": False,
        },
    }

    assert passthrough_view(config) == {
        "tools": [{"type": "openrouter:web_search"}],
        "reasoning": {"enabled": True},
        "provider": {
            "quantizations": ["fp8"],
            "ignore": ["slow"],
            "data_collection": "deny",
            "allow_fallbacks": False,
        },
    }
    for managed in ("model", "models", "route"):
        assert managed not in passthrough_view(config)
    for managed in ("only", "order", "sort"):
        assert managed not in passthrough_view(config)["provider"]


def test_a_provider_block_holding_only_what_the_app_decides_leaves_no_provider_in_the_passthrough():
    assert passthrough_view({"model": "a", "provider": {"only": ["x"], "order": ["x"], "sort": "price"}}) == {}
    assert passthrough_view({"model": "a", "provider": {}}) == {}
    assert passthrough_view({"model": "a"}) == {}
    assert passthrough_view({}) == {}
    # something that is not a block is not the app's to hide
    assert passthrough_view({"model": "a", "provider": "odd"}) == {"provider": "odd"}


def test_the_passthrough_is_a_copy_and_its_digest_ignores_the_order_of_keys_and_the_managed_ones():
    config = {"model": "a", "tools": [1], "provider": {"only": ["x"], "quantizations": ["fp8"]}}
    view = passthrough_view(config)
    view["tools"].append(2)
    assert config["tools"] == [1]
    same = {"provider": {"quantizations": ["fp8"], "only": ["y", "z"]}, "tools": [1], "model": "other"}
    assert passthrough_digest(config) == passthrough_digest(same)
    assert passthrough_digest(config) != passthrough_digest({**config, "tools": [1, 2]})
    assert passthrough_digest(config) != passthrough_digest({**config, "provider": {"quantizations": ["fp4"]}})


def test_a_plan_shows_the_passthrough_it_will_send_back_and_the_keys_it_manages(client):
    _own_as(KEY)
    live = {
        "model": "acme/best",
        "tools": [{"type": "openrouter:web_search"}],
        "provider": {"quantizations": ["fp8", "bf16"], "data_collection": "deny", "ignore": ["slow"]},
    }
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=3)
        plan = _plan(client)

    assert plan["passthrough"] == {
        "tools": [{"type": "openrouter:web_search"}],
        "provider": {"quantizations": ["fp8", "bf16"], "data_collection": "deny", "ignore": ["slow"]},
    }
    assert plan["managed_keys"] == ["model", "models", "route", "provider.only", "provider.order", "provider.sort"]
    # it is what the request carries, apart from the keys the app decides
    body = {key: value for key, value in plan["request"]["body"].items() if key != "messages"}
    assert passthrough_view(body) == plan["passthrough"]


def test_a_new_preset_shows_the_passthrough_it_takes_from_its_template(client):
    _settings(template_slug="my-tools")
    template = {
        "model": "other/model",
        "tools": [{"type": "openrouter:datetime"}],
        "provider": {"quantizations": ["fp8"]},
    }
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", template, version=3)
        plan = _plan(client)

    assert plan["passthrough"] == {"tools": [{"type": "openrouter:datetime"}], "provider": {"quantizations": ["fp8"]}}


def test_a_plan_that_is_blocked_still_shows_what_the_live_preset_holds(client):
    _own_as(KEY)
    _settings(min_score=99)  # no model passes, so nothing is planned, and the live preset is what there is to show
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "tools": [1]}, version=3, system_prompt="Be brief.")
        plan = _plan(client)
    assert plan["action"] == "blocked" and plan["passthrough"] == {"tools": [1]}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _settings(min_score=99)
        assert _plan(client)["passthrough"] == {}


def test_the_plan_carries_the_id_and_the_workspace_the_account_names_for_the_preset(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2, id="p-1", workspace_slug="team-a")
        plan = _plan(client)
    assert (plan["preset_id"], plan["workspace"]) == ("p-1", "team-a")


def test_the_workspace_may_be_named_by_a_slug_an_id_or_an_object_and_a_slug_wins(client):
    _own_as(KEY)
    for extra, want in (
        ({"workspace": "ws-1"}, "ws-1"),
        ({"workspace_id": 42}, "42"),
        ({"workspace": {"slug": "team-b", "id": "w-9"}}, "team-b"),
        ({"workspace": {"id": "w-9"}}, "w-9"),
        ({"workspace_id": "w-9", "workspace_slug": "team-c"}, "team-c"),
        ({"workspace": ""}, None),
        ({"workspace": True}, None),
        ({"workspace": ["x"]}, None),
    ):
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, {"model": "acme/best"}, version=2, **extra)
            assert _plan(client)["workspace"] == want, extra


def test_the_id_and_the_workspace_are_null_when_the_response_names_neither(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        plan = _plan(client)
    assert plan["preset_id"] is None and plan["workspace"] is None
    with respx.mock(assert_all_called=False) as router:  # and a preset that does not exist yet has neither
        _remote(router, "vibonomics-fresh", None)
        draft = PresetSettings(slug="fresh", enabled=True).model_dump(mode="json")
        planned = client.post("/api/presets/plan", json={"settings": draft}).json()
    assert planned["preset_id"] is None and planned["workspace"] is None


def test_the_route_choice_is_saved_with_the_settings(client):
    current = client.get("/api/settings").json()["settings"]
    saved = client.put("/api/settings", json={"settings": _with_item(current, route="sort", sort="latency")})
    assert saved.status_code == 200
    item = saved.json()["settings"]["presets"]["items"][0]
    assert (item["route"], item["sort"]) == ("sort", "latency")
    for bad in ({"route": "fastest"}, {"sort": "cheap"}):
        assert client.put("/api/settings", json={"settings": _with_item(current, **bad)}).status_code == 422


# ---- looking at a preset made by hand, and adopting it -----------------------------------------------------------

HAND = "my-tools"  # a preset the owner made in the OpenRouter UI: no prefix, nothing of ours about it
HAND_CONFIG = {
    "model": "other/model",
    "models": ["other/model", "third/model"],
    "tools": [{"type": "openrouter:web_search"}],
    "reasoning": {"enabled": True},
    "provider": {
        "only": ["deepinfra/fp8"],
        "order": ["deepinfra/fp8"],
        "quantizations": ["fp8"],
        "ignore": ["slow"],
        "data_collection": "deny",
    },
}


def _inspect(client: TestClient, slug: str = HAND) -> httpx.Response:
    return client.post("/api/presets/inspect", json={"slug": slug})


def _adopt(client: TestClient, digest: str, slug: str = HAND) -> httpx.Response:
    return client.post("/api/presets/adopt", json={"slug": slug, "digest": digest})


def _adopted_item(slug: str = HAND, **fields: Any) -> PresetSettings:
    return PresetSettings(**{"adopted": True, "slug": slug, "min_context": 100_000, **fields})


def _adopt_hand_made(client: TestClient, config: dict[str, Any] | None = None, version: int = 3, **extra: Any) -> dict:
    """Inspect and adopt ``HAND`` against an account that holds it; returns what the inspect said."""
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, config or HAND_CONFIG, version=version, **extra)
        seen = _inspect(client).json()
        assert _adopt(client, seen["digest"]).status_code == 200
    return seen


def test_inspect_shows_a_preset_made_by_hand_with_everything_the_app_would_send_back(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, id="p-7", workspace_slug="team-a")
        post = router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=_echo)
        response = _inspect(client)
        assert post.call_count == 0
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    body = response.json()
    assert (body["slug"], body["status"], body["version"]) == (HAND, "active", 3)
    assert body["config"] == HAND_CONFIG
    assert body["models"] == ["other/model", "third/model"] and body["providers"] == ["deepinfra/fp8"]
    assert body["has_system_prompt"] is False
    assert body["managed_keys"] == ["model", "models", "route", "provider.only", "provider.order", "provider.sort"]
    assert body["passthrough"] == {
        "tools": [{"type": "openrouter:web_search"}],
        "reasoning": {"enabled": True},
        "provider": {"quantizations": ["fp8"], "ignore": ["slow"], "data_collection": "deny"},
    }
    assert (body["managed"], body["adoptable"], body["blockers"]) == (False, True, [])
    assert (body["preset_id"], body["workspace"]) == ("p-7", "team-a")
    assert [v["version"] for v in body["versions"]] == [3, 1] and body["versions"][0]["live"] is True
    assert body["digest"] == digest_of(HAND, HAND_CONFIG, None, 3)
    assert body["updated_at"] == "2026-11-04T07:36:33Z"


def test_inspect_gives_a_null_id_and_workspace_when_the_response_names_neither(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        body = _inspect(client).json()
    assert body["preset_id"] is None and body["workspace"] is None


def test_the_inspect_digest_names_the_slug_the_version_and_the_config(client):
    digests = []
    for config, version in ((HAND_CONFIG, 3), (HAND_CONFIG, 4), ({**HAND_CONFIG, "tools": []}, 3)):
        with respx.mock(assert_all_called=False) as router:
            _remote(router, HAND, config, version=version)
            digests.append(_inspect(client).json()["digest"])
    assert len(set(digests)) == 3
    with respx.mock(assert_all_called=False) as router:
        _remote(router, "other-tools", HAND_CONFIG, version=3)
        assert _inspect(client, "other-tools").json()["digest"] != digests[0]


def test_inspect_says_why_a_preset_cannot_be_adopted(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, status="archived")
        body = _inspect(client).json()
    assert body["adoptable"] is False
    assert any("archived" in blocker for blocker in body["blockers"]), body["blockers"]


def test_a_system_prompt_is_not_a_reason_to_refuse_a_preset_and_it_is_part_of_what_the_digest_names(client):
    digests = []
    for prompt in (None, "Be brief.", "Be terse."):
        with respx.mock(assert_all_called=False) as router:
            _remote(router, HAND, HAND_CONFIG, version=3, system_prompt=prompt)
            body = _inspect(client).json()
        assert (body["adoptable"], body["blockers"]) == (True, [])
        assert body["has_system_prompt"] is (prompt is not None)
        digests.append(body["digest"])
    assert len(set(digests)) == 3
    assert digests[0] == digest_of(HAND, HAND_CONFIG, None, 3)  # no prompt: the digest is what it was before prompts


def test_inspect_reads_a_preset_the_app_manages_too_and_says_it_is_managed(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        body = _inspect(client, SLUG).json()
    assert body["managed"] is True and body["adoptable"] is False
    assert any("already" in blocker for blocker in body["blockers"])


def test_inspect_has_an_answer_for_every_way_the_account_can_fail(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, "nowhere", None)
        assert _inspect(client, "nowhere").status_code == 404
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/presets/{HAND}").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
        failed = _inspect(client)
    assert failed.status_code == 502 and "HTTP 401" in failed.json()["detail"]
    with respx.mock(assert_all_called=False) as router:
        _raw(router, HAND, {"slug": HAND, "status": "active"})  # no live version at all
        body = _inspect(client).json()
    assert body["adoptable"] is False and body["config"] == {} and body["version"] is None
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"api_key_enc": None})
    assert _inspect(client).status_code == 409


def test_adopting_records_the_preset_as_ours_and_adds_it_to_the_list_without_writing_anything(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, id="p-7", created_at="2026-10-01T00:00:00Z")
        post = router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=_echo)
        seen = _inspect(client).json()
        response = _adopt(client, seen["digest"])
        assert post.call_count == 0
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["slug"] == HAND and item["adopted"] is True  # the name is kept, with no prefix added
    assert (item["enabled"], item["auto_apply"]) == (False, False)  # adopting writes nothing, now or on a timer
    state = _state()
    assert state["owned"] == [HAND]
    record = state["owners"][HAND]
    assert record["fingerprint"] == owner_fingerprint(BASE, KEY) and record["adopted"] is True
    assert (record["preset_id"], record["created_at"]) == ("p-7", "2026-10-01T00:00:00Z") and record["adopted_at"]
    assert state["reviewed"] == {HAND: passthrough_digest(HAND_CONFIG)}
    assert KEY not in json.dumps(state)
    saved = client.get("/api/settings").json()["settings"]["presets"]["items"]
    assert [i["slug"] for i in saved] == [SLUG, HAND] and saved[1]["adopted"] is True
    assert client.get("/api/presets/state").json()["owned"] == [HAND]
    # and it is no longer something to adopt
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        again = _inspect(client).json()
    assert again["managed"] is True and again["adoptable"] is False


def test_adopting_is_refused_when_the_preset_changed_since_it_was_shown(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        stale = _inspect(client).json()["digest"]
    for config, version in ((HAND_CONFIG, 4), ({**HAND_CONFIG, "tools": []}, 3)):
        with respx.mock(assert_all_called=False) as router:
            _remote(router, HAND, config, version=version)
            response = _adopt(client, stale)
        assert response.status_code == 409 and "changed" in response.json()["detail"]
    assert _state() == {} and [i["slug"] for i in _saved_items(client)] == [SLUG]


def _saved_items(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/api/settings").json()["settings"]["presets"]["items"]


def test_adopting_is_refused_for_an_archived_preset(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, status="archived")
        digest = _inspect(client).json()["digest"]
        response = _adopt(client, digest)
    assert response.status_code == 409 and "archived" in response.json()["detail"]
    assert _state() == {} and [i["slug"] for i in _saved_items(client)] == [SLUG]


def test_a_preset_with_a_system_prompt_is_adopted_and_the_reviewed_digest_covers_the_prompt(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, system_prompt="Be brief.")
        seen = _inspect(client).json()
        post = router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=_echo)
        response = _adopt(client, seen["digest"])
        assert post.call_count == 0
    assert response.status_code == 200 and response.json()["adopted"] is True
    assert _state()["reviewed"] == {HAND: passthrough_digest(HAND_CONFIG, None, "Be brief.")}
    assert _state()["reviewed"][HAND] != passthrough_digest(HAND_CONFIG)


def test_adopting_is_refused_when_only_the_system_prompt_changed_since_it_was_shown(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, system_prompt="Be brief.")
        stale = _inspect(client).json()["digest"]
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, system_prompt="Be terse.")
        response = _adopt(client, stale)
    assert response.status_code == 409 and "changed" in response.json()["detail"]
    assert _state() == {}


def test_adopting_is_refused_for_a_preset_that_is_already_managed_or_was_made_by_the_app(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        seen = _inspect(client, SLUG).json()
        response = _adopt(client, seen["digest"], SLUG)
    assert response.status_code == 409 and "already" in response.json()["detail"]
    # one that only the list holds, with no record, is as managed as the other
    _items(_item(), _adopted_item())
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        response = _adopt(client, _inspect(client).json()["digest"])
    assert response.status_code == 409
    assert HAND not in _state().get("owned", [])


def test_a_name_recorded_under_another_key_is_not_adoptable_with_this_one(client):
    _own_as(OTHER_KEY, slug=HAND)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        body = _inspect(client).json()
        response = _adopt(client, body["digest"])
    assert body["adoptable"] is False and response.status_code == 409
    assert _state()["owners"][HAND]["fingerprint"] == owner_fingerprint(BASE, OTHER_KEY)


def test_no_more_than_twenty_presets_can_be_managed(client):
    _items(*[_item(f"vibonomics-p{n}") for n in range(20)])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        body = _inspect(client).json()
        response = _adopt(client, body["digest"])
    assert body["adoptable"] is False and response.status_code == 409 and "20" in response.json()["detail"]


def test_adopting_a_preset_that_does_not_exist_or_cannot_be_read_is_not_a_success(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, "nowhere", None)
        assert _adopt(client, "x", "nowhere").status_code == 404
    with respx.mock(assert_all_called=False) as router:
        _raw(router, HAND, {"slug": HAND})
        body = _inspect(client).json()
        assert _adopt(client, body["digest"]).status_code == 409
    assert _state() == {}


def test_an_adopted_preset_is_planned_under_its_own_name_and_keeps_every_key_it_has(client):
    _adopt_hand_made(client)
    _items(_item(), _adopted_item(enabled=True))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        plan = _plan(client, HAND)

    assert plan["slug"] == HAND and plan["action"] == "update"
    assert plan["request"]["url"] == f"{BASE}/presets/{HAND}/chat/completions"
    config = plan["config"]
    assert config["tools"] == HAND_CONFIG["tools"] and config["reasoning"] == {"enabled": True}
    assert config["provider"]["quantizations"] == ["fp8"] and config["provider"]["data_collection"] == "deny"
    assert plan["passthrough"] == passthrough_view(HAND_CONFIG)


@pytest.mark.asyncio
async def test_an_adopted_slug_with_no_record_of_the_adoption_is_never_planned_or_written(client):
    from vibonomics import presets as engine

    # the list says adopted, but nothing says the owner agreed to it: not a path to writing a preset made by hand
    _items(_item(), _adopted_item(enabled=True, auto_apply=True))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        post = router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client, HAND)
        assert plan["action"] == "blocked" and plan["request"] is None and plan["digest"] is None
        assert "adopt" in plan["reason"].lower()
        assert client.post("/api/presets/apply", json={"slug": HAND, "digest": "anything"}).status_code == 409
        await engine.sync(HAND, "schedule")  # and a timer, which names no digest, gets no further
        assert post.call_count == 0
    assert _state()["last"][HAND]["status"] == "blocked"
    # a record for the name that is not an adoption (a slug the app created) does not do either
    _own_as(KEY, slug=HAND)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        post = router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=_echo)
        assert _plan(client, HAND)["action"] == "blocked"
        await engine.sync(HAND, "schedule")
        assert post.call_count == 0


def test_an_adopted_preset_that_is_gone_from_the_account_is_not_created_again(client):
    _adopt_hand_made(client)
    _items(_item(), _adopted_item(enabled=True))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, None)
        post = router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client, HAND)
        assert plan["action"] == "blocked" and "gone" in plan["reason"]
        assert post.call_count == 0


def _set_state(state: dict[str, Any]) -> None:
    with session_scope() as session:
        session.merge(Setting(key=STATE_KEY, value=state))


def _own_both(key: str = KEY) -> None:
    """SLUG made by the app and HAND adopted, both under ``key``."""
    mine = owner_fingerprint(BASE, key)
    _set_state(
        {
            "owned": [SLUG, HAND],
            "owners": {
                SLUG: {"fingerprint": mine, "version_at_create": 1},
                HAND: {"fingerprint": mine, "adopted": True, "adopted_at": "2026-11-05T00:00:00+00:00"},
            },
            "reviewed": {HAND: passthrough_digest(HAND_CONFIG)},
        }
    )


def test_the_ownership_rules_of_a_key_hold_for_each_preset_by_itself(client):
    _own_both()
    _items(_item(), _adopted_item(enabled=True))
    _use_key(OTHER_KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        _remote(router, HAND, HAND_CONFIG, version=3)
        for slug in (SLUG, HAND):
            plan = _plan(client, slug)
            assert plan["action"] == "blocked" and "different OpenRouter key or account" in plan["reason"], slug
            assert plan["request"] is None
    _use_key(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        _remote(router, HAND, HAND_CONFIG, version=3)
        assert _plan(client, SLUG)["action"] == "update" and _plan(client, HAND)["action"] == "update"
    # an adopted name that was deleted and made again is no longer the preset that was adopted
    state = _state()
    state["owners"][HAND]["preset_id"] = "p-7"
    _set_state(state)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        _remote(router, HAND, HAND_CONFIG, version=3, id="p-other")
        assert _plan(client, HAND)["action"] == "blocked"
        assert "no longer the one" in _plan(client, HAND)["reason"]
        assert _plan(client, SLUG)["action"] == "update"


def _adopted_world(client: TestClient) -> None:
    """HAND adopted from the account as HAND_CONFIG, then switched on to write and to run on a timer."""
    _adopt_hand_made(client)
    _items(_item(), _adopted_item(enabled=True, auto_apply=True))


def _write_to_hand(router: respx.Router) -> respx.Route:
    def write(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        sent.pop("messages", None)
        return httpx.Response(200, json=_preset(HAND, sent, version=4))

    return router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=write)


@pytest.mark.asyncio
async def test_a_scheduled_run_writes_an_adopted_preset_only_while_its_other_keys_are_as_they_were_reviewed(client):
    from vibonomics import presets as engine

    _adopted_world(client)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        post = _write_to_hand(router)
        await engine.sync(HAND, "schedule")
        assert post.call_count == 1  # nothing changed since the owner looked
    assert _state()["last"][HAND]["status"] == "applied"

    # someone adds a tool in the OpenRouter UI: the keys the owner looked at are not the keys that are there
    changed = {**HAND_CONFIG, "tools": [{"type": "openrouter:web_search"}, {"type": "openrouter:datetime"}]}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, changed, version=5)
        post = _write_to_hand(router)
        await engine.sync(HAND, "schedule")
        await engine.sync(HAND, "startup")
        assert post.call_count == 0
    last = _state()["last"][HAND]
    assert last["status"] == "blocked" and "since you reviewed" in last["message"] and last["trigger"] == "startup"
    assert _slug_state(client, HAND)["last_status"] == "blocked"


@pytest.mark.asyncio
async def test_a_manual_apply_of_an_adopted_preset_accepts_the_keys_it_showed_and_lets_the_timer_go_on(client):
    from vibonomics import presets as engine

    _adopted_world(client)
    changed = {**HAND_CONFIG, "reasoning": {"enabled": False}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, changed, version=5)
        post = _write_to_hand(router)
        await engine.sync(HAND, "schedule")
        assert post.call_count == 0  # blocked, until the owner has seen the new keys
        plan = _plan(client, HAND)
        assert plan["passthrough"]["reasoning"] == {"enabled": False}  # and sees them here
        assert _apply(client, plan, HAND).status_code == 200
        assert post.call_count == 1
    assert _state()["reviewed"][HAND] == passthrough_digest(changed)

    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, {**changed, "model": "x/old"}, version=6)
        post = _write_to_hand(router)
        await engine.sync(HAND, "schedule")
        assert post.call_count == 1  # reviewed now, so the timer writes again


def test_a_manual_apply_that_finds_nothing_to_write_still_counts_as_reviewing_the_keys(client):
    _adopted_world(client)
    changed = {**HAND_CONFIG, "reasoning": {"enabled": False}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, changed, version=5)
        wanted = _plan(client, HAND)["config"]
    with respx.mock(assert_all_called=False) as router:  # the preset already holds what the plan would write
        _remote(router, HAND, wanted, version=6)
        post = _write_to_hand(router)
        again = _plan(client, HAND)
        assert again["action"] == "unchanged"
        assert _apply(client, again, HAND).json()["applied"] is False
        assert post.call_count == 0
    assert _state()["reviewed"][HAND] == passthrough_digest(wanted) != passthrough_digest(HAND_CONFIG)


def test_an_ordinary_preset_is_not_held_back_by_the_review(client):
    # SLUG was created by the app, so the owner never reviewed anything of the keys it carries
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "tools": [1]}, version=2)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert _apply(client, plan).status_code == 200
        assert post.call_count == 1
    assert "reviewed" not in _state()


def test_taking_an_adopted_preset_out_of_the_list_ends_the_adoption_and_leaves_the_rest_alone(client):
    _own_both()
    state = _state()
    state["last"] = {HAND: {"at": "2026-11-01T00:00:00+00:00"}, SLUG: {"at": "2026-11-01T00:00:00+00:00"}}
    state["pins"] = {HAND: ["a"], SLUG: ["b"]}
    _set_state(state)
    _items(_item(), _adopted_item())
    current = client.get("/api/settings").json()["settings"]

    kept = [i for i in _saved_items(client) if i["slug"] != HAND]
    assert client.put("/api/settings", json={"settings": _with_items(current, kept)}).status_code == 200

    state = _state()
    assert state["owned"] == [SLUG] and set(state["owners"]) == {SLUG}
    assert HAND not in state["reviewed"] and set(state["last"]) == {SLUG} and state["pins"] == {SLUG: ["b"]}
    # and it can be adopted again
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3)
        assert _adopt(client, _inspect(client).json()["digest"]).status_code == 200


def test_taking_a_preset_the_app_made_out_of_the_list_does_not_release_it(client):
    _own_both()
    _items(_item(), _adopted_item())
    current = client.get("/api/settings").json()["settings"]

    kept = [i for i in _saved_items(client) if i["slug"] != SLUG]
    assert client.put("/api/settings", json={"settings": _with_items(current, kept)}).status_code == 200

    assert _state()["owned"] == [SLUG, HAND]  # it still exists on the account, and is still the app's


def _with_items(current: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    return {**current, "presets": {**current["presets"], "items": items}}


def test_keeping_an_adopted_preset_in_the_list_keeps_the_adoption(client):
    _adopt_hand_made(client)
    current = client.get("/api/settings").json()["settings"]
    assert client.put("/api/settings", json={"settings": current}).status_code == 200
    assert _state()["owned"] == [HAND] and _state()["reviewed"][HAND]


# ---- the live check before a write ---------------------------------------------------------------------------

# What OpenRouter lists for acme/best right now: the three providers the plan pins, and slow/fp8, which it does not.
LIVE_BEST = [
    ("novita/fp4", 0.08, 0.35),
    ("deepinfra/fp8", 0.10, 0.40),
    ("wafer/bf16", 0.12, 0.45),
    ("slow/fp8", 0.5, 2.0),
]


def _live_ep(tag: str, price_in: float, price_out: float, **fields: Any) -> dict[str, Any]:
    return {
        "provider_name": tag.split("/")[0].title(),
        "tag": tag,
        "status": 0,
        "uptime_last_30m": 99.0,
        "pricing": {"prompt": repr(price_in / 1e6), "completion": repr(price_out / 1e6)},
        **fields,
    }


def _live_response(endpoints: list[dict[str, Any]] | None = None, **data: Any) -> httpx.Response:
    rows = [_live_ep(*row) for row in LIVE_BEST] if endpoints is None else endpoints
    return httpx.Response(200, json={"data": {"id": "acme/best", "endpoints": rows, **data}})


def _live_listing(router: respx.Router, endpoints: list[dict[str, Any]] | None = None, **data: Any) -> respx.Route:
    return router.get(f"{BASE}/models/acme/best/endpoints").mock(return_value=_live_response(endpoints, **data))


def _without(tag: str) -> list[dict[str, Any]]:
    return [_live_ep(*row) for row in LIVE_BEST if row[0] != tag]


@pytest.fixture
def no_waits(monkeypatch):
    from vibonomics import http

    real_sleep = asyncio.sleep

    async def instant(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(http.asyncio, "sleep", instant)


def _errors(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in plan["validation"] if c["level"] == "error"]


def test_a_plan_carries_the_live_check_of_what_it_would_write(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        route = _live_listing(router)
        plan = _plan(client)
    assert plan["valid"] is True and plan["validated_at"]
    assert plan["validation"] == [
        {
            "level": "ok",
            "check": "listing",
            "model": "acme/best",
            "provider": None,
            "message": "4 live endpoints, pins verified",
        }
    ]
    assert route.call_count == 1 and "authorization" not in route.calls[0].request.headers  # the listing is public


def test_a_pinned_provider_that_left_the_listing_blocks_the_write_and_nothing_is_sent(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router, _without("wafer/bf16"))
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert plan["action"] == "create" and plan["valid"] is False
        assert [c["provider"] for c in _errors(plan)] == ["wafer/bf16"]
        response = _apply(client, plan)
        assert post.call_count == 0
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("Live check failed") and "wafer/bf16" in detail
    state = _slug_state(client)
    assert state["last_status"] == "blocked" and state["last_message"].startswith("Live check failed")
    assert state["last_trigger"] == "manual" and state["version"] is None
    assert client.get("/api/presets/state").json()["owned"] == []  # nothing was created, so nothing is ours


def test_an_update_is_refused_the_same_way_and_the_live_preset_is_left_as_it_was(client):
    _own()
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "tools": [{"type": "x"}]}, version=2)
        _live_listing(router, _without("novita/fp4"))
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert plan["action"] == "update" and plan["valid"] is False
        assert _apply(client, plan).status_code == 409
        assert post.call_count == 0
    assert _slug_state(client)["last_status"] == "blocked"


def test_a_model_with_no_live_listing_is_an_error_and_apply_refuses(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        router.get(f"{BASE}/models/acme/best/endpoints").mock(return_value=httpx.Response(404, json={"error": {}}))
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 0
    assert plan["valid"] is False and "no live endpoint" in _errors(plan)[0]["message"].lower()
    assert response.status_code == 409 and "no live endpoint" in response.json()["detail"].lower()


def test_unhealthy_providers_and_drifted_prices_warn_and_the_write_goes_ahead(client):
    rows = [
        _live_ep("novita/fp4", 0.08, 0.35, status=-2),
        _live_ep("deepinfra/fp8", 0.10, 0.40, uptime_last_30m=20.0),
        _live_ep("wafer/bf16", 0.12 * 1.5, 0.45),
        _live_ep("slow/fp8", 0.5, 2.0),
    ]
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router, rows)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 1
    assert plan["valid"] is True
    assert sorted((c["level"], c["check"], c["provider"]) for c in plan["validation"] if c["level"] != "ok") == [
        ("warning", "health", "deepinfra/fp8"),
        ("warning", "health", "novita/fp4"),
        ("warning", "price", "wafer/bf16"),
    ]
    assert response.status_code == 200 and response.json()["applied"] is True


def test_a_model_that_is_about_to_expire_is_a_warning_and_does_not_block(client):
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router, expiration_date=soon)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert _apply(client, plan).status_code == 200
        assert post.call_count == 1
    assert plan["valid"] is True
    assert [(c["level"], c["check"]) for c in plan["validation"] if c["level"] != "ok"] == [("warning", "expiry")]


def test_a_model_the_stored_copy_says_expires_soon_is_warned_about_though_its_listing_is_silent(client):
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    _set_extra("acme/best", {"endpoints": ENDPOINTS, "expiration_date": soon})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router)
        plan = _plan(client)
    assert plan["valid"] is True
    assert [c["check"] for c in plan["validation"] if c["level"] == "warning"] == ["expiry"]


def test_a_listing_that_cannot_be_fetched_is_a_warning_and_does_not_block_the_write(client, no_waits):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        listing = router.get(f"{BASE}/models/acme/best/endpoints").mock(side_effect=httpx.ReadTimeout("slow"))
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = _apply(client, plan)
        assert post.call_count == 1
        assert listing.call_count == 4  # two tries for the preview and two for the check at the time of writing
    assert plan["valid"] is True
    assert [(c["level"], c["check"]) for c in plan["validation"]] == [("warning", "listing")]
    assert "could not check live data for acme/best" in plan["validation"][0]["message"].lower()
    assert response.status_code == 200 and response.json()["applied"] is True


def test_the_check_is_made_again_when_the_write_is_asked_for_not_taken_from_the_preview(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        listing = _live_listing(router)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert plan["valid"] is True
        # the preview was clean; before the apply a provider leaves OpenRouter
        listing.mock(return_value=_live_response(_without("deepinfra/fp8")))
        response = _apply(client, plan)
        assert post.call_count == 0
    assert response.status_code == 409
    assert "deepinfra/fp8" in response.json()["detail"]
    assert _slug_state(client)["last_status"] == "blocked"


def test_a_check_that_passed_at_preview_and_still_passes_lets_the_write_through(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        listing = _live_listing(router)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert _apply(client, plan).status_code == 200
        assert post.call_count == 1
        assert listing.call_count == 2  # once to preview, once at the time of writing
    assert _slug_state(client)["last_status"] == "applied"


@pytest.mark.asyncio
async def test_a_scheduled_run_that_fails_the_live_check_records_blocked_and_writes_nothing(client, caplog):
    from vibonomics import presets as engine

    _settings(auto_apply=True)
    with caplog.at_level("WARNING", logger="vibonomics.presets"), respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router, _without("novita/fp4"))
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        await engine.sync(SLUG, "schedule")
        assert post.call_count == 0
    last = _state()["last"][SLUG]
    assert last["status"] == "blocked" and last["trigger"] == "schedule"
    assert last["message"].startswith("Live check failed") and "novita/fp4" in last["message"]
    assert not _state().get("owned")
    assert any("novita/fp4" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    # and the run after it, once the provider is back, writes
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        await engine.sync(SLUG, "schedule")
        assert post.call_count == 1
    assert _state()["last"][SLUG]["status"] == "applied"


def test_an_unchanged_plan_still_carries_the_live_health_of_what_is_already_written(client):
    _own()
    desired = build_config(None, ["acme/best"], ["novita/fp4", "deepinfra/fp8", "wafer/bf16"])
    rows = [_live_ep("novita/fp4", 0.08, 0.35, status=-3), *[_live_ep(*r) for r in LIVE_BEST[1:]]]
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, desired, version=2)
        _live_listing(router, rows)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert _apply(client, plan).json()["applied"] is False
        assert post.call_count == 0
    assert plan["action"] == "unchanged" and plan["valid"] is True and plan["validated_at"]
    assert [(c["level"], c["check"], c["provider"]) for c in plan["validation"] if c["level"] != "ok"] == [
        ("warning", "health", "novita/fp4")
    ]


def test_the_digest_does_not_depend_on_what_the_live_check_found(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        listing = _live_listing(router)
        clean = _plan(client)
        listing.mock(return_value=_live_response(_without("wafer/bf16")))
        broken = _plan(client)
    assert clean["valid"] is True and broken["valid"] is False
    assert clean["digest"] == broken["digest"] and clean["config"] == broken["config"]


def test_a_blocked_plan_makes_no_live_check(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "mine/by-hand"})
        listing = _live_listing(router)
        plan = _plan(client)
        assert listing.call_count == 0
    assert plan["action"] == "blocked"
    assert plan["validation"] == [] and plan["validated_at"] is None and plan["valid"] is True


def test_a_draft_is_checked_too_because_the_check_only_reads(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        listing = _live_listing(router, _without("wafer/bf16"))
        response = client.post("/api/presets/plan", json={"settings": _saved_presets(client)})
        assert listing.call_count == 1
    plan = response.json()
    assert plan["draft"] is True and plan["valid"] is False
    assert [c["provider"] for c in _errors(plan)] == ["wafer/bf16"]


def test_every_model_of_a_plan_with_several_models_is_checked_against_its_own_listing(client):
    _settings(max_models=2)
    _give_endpoints("acme/steady", [("steady-a/fp8", 0.105, 0.42)])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router)
        router.get(f"{BASE}/models/acme/steady/endpoints").mock(
            return_value=httpx.Response(200, json={"data": {"endpoints": [_live_ep("other/fp8", 0.105, 0.42)]}})
        )
        plan = _plan(client)
    # steady has nothing else pinned, so the preset could not serve it at all
    assert [(c["model"], c["check"], c["provider"]) for c in _errors(plan)] == [
        ("acme/steady", "provider", "steady-a/fp8"),
        ("acme/steady", "pins", None),
    ]
    assert plan["valid"] is False
    assert {c["model"] for c in plan["validation"]} == {"acme/best", "acme/steady"}


# ---- guardrails: the keys the owner decides besides the models -----------------------------------------------------


def _g(**fields: Any) -> PresetGuardrails:
    return PresetGuardrails(**fields)


# (value to set, the value the live preset has) for each guardrail that is a config key
SAMPLES: dict[str, tuple[Any, Any]] = {
    "temperature": (0.7, 0.3),
    "max_tokens": (4_096, 1_024),
    "repetition_penalty": (1.1, 1.3),
    "frequency_penalty": (0.5, -0.5),
    "presence_penalty": (0.2, 0.9),
    "top_p": (0.8, 0.95),
    "quantizations": (["fp8"], ["bf16"]),
    "ignore": (["slow"], ["nobody"]),
    "require_parameters": (True, False),
}
PROVIDER_KEYS = ("quantizations", "ignore", "require_parameters")


def _at(config: dict[str, Any], key: str) -> Any:
    return (config.get("provider") or {}).get(key) if key in PROVIDER_KEYS else config.get(key)


def _holding(config: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    """``config`` with ``key`` at ``value``, inside the provider block where OpenRouter keeps it."""
    if key in PROVIDER_KEYS:
        return {**config, "provider": {**config.get("provider", {}), key: value}}
    return {**config, key: value}


def test_every_set_guardrail_is_written_where_openrouter_keeps_it_and_the_token_cap_is_max_tokens():
    guard = _g(**{key: new for key, (new, _old) in SAMPLES.items()})
    config = build_config({"model": "old", "tools": [1]}, ["a/m"], ["x/fp8"], guardrails=guard)
    assert config == {
        "model": "a/m",
        "tools": [1],
        "temperature": 0.7,
        "max_tokens": 4_096,
        "repetition_penalty": 1.1,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.2,
        "top_p": 0.8,
        "provider": {
            "only": ["x/fp8"],
            "order": ["x/fp8"],
            "quantizations": ["fp8"],
            "ignore": ["slow"],
            "require_parameters": True,
        },
    }
    assert "max_completion_tokens" not in config  # OpenRouter drops that one from a preset


def test_a_guardrail_left_unset_keeps_what_the_live_preset_has_and_a_set_one_replaces_it():
    live = {
        "model": "old",
        "temperature": 0.3,
        "top_p": 0.5,
        "provider": {"quantizations": ["fp8"], "ignore": ["slow"], "require_parameters": True, "only": ["x"]},
    }
    config = build_config(live, ["a/m"], ["y/fp8"], guardrails=_g(temperature=0.8))
    assert (config["temperature"], config["top_p"]) == (0.8, 0.5)
    assert config["provider"] == {
        "quantizations": ["fp8"],
        "ignore": ["slow"],
        "require_parameters": True,
        "only": ["y/fp8"],
        "order": ["y/fp8"],
    }
    assert build_config(live, ["a/m"], ["y/fp8"], guardrails=_g()) == build_config(live, ["a/m"], ["y/fp8"])
    assert live["temperature"] == 0.3  # the base is not edited in place


def test_a_cleared_guardrail_is_taken_off_and_a_provider_block_it_emptied_goes_too():
    live = {"model": "old", "temperature": 0.3, "top_p": 0.5, "provider": {"quantizations": ["fp8"], "ignore": ["x"]}}
    config = build_config(live, ["a/m"], [], guardrails=_g(clear=["temperature", "quantizations"]))
    assert config == {"model": "a/m", "top_p": 0.5, "provider": {"ignore": ["x"]}}
    only = {"model": "old", "provider": {"quantizations": ["fp8"]}}
    assert build_config(only, ["a/m"], [], guardrails=_g(clear=["quantizations"])) == {"model": "a/m"}
    # clearing what is not there changes nothing, and a block that was already empty stays as it was
    assert build_config({"model": "a/m", "provider": {}}, ["a/m"], [], guardrails=_g(clear=["ignore"])) == {
        "model": "a/m",
        "provider": {},
    }


def test_a_guardrail_set_with_nowhere_to_go_makes_the_provider_block():
    assert build_config(None, ["m"], [], guardrails=_g(ignore=["slow"])) == {
        "model": "m",
        "provider": {"ignore": ["slow"]},
    }


def test_the_owned_view_has_a_guardrail_key_only_when_the_owner_sets_or_clears_it():
    live = {"model": "a", "temperature": 0.3, "top_p": 0.5, "provider": {"quantizations": ["fp8"], "ignore": ["x"]}}
    plain = owned_view(live)
    assert set(plain) == {"model", "models", "route", "only", "order", "sort"}
    view = owned_view(live, _g(temperature=0.7, clear=["quantizations"]), "a prompt")
    assert (view["temperature"], view["quantizations"]) == (0.3, ["fp8"])  # the live values, to be compared
    assert not {"top_p", "ignore", "system_prompt"} & set(view)
    assert owned_view(live, _g(system_prompt="mine"), "live one")["system_prompt"] == "live one"
    assert owned_view(live, _g(clear=["system_prompt"]), "")["system_prompt"] is None
    assert owned_view({"model": "a"}, _g(top_p=0.5))["top_p"] is None


def test_the_passthrough_leaves_out_what_the_app_manages_and_keeps_what_it_does_not():
    config = {
        "model": "a",
        "temperature": 0.3,
        "top_p": 0.5,
        "tools": [1],
        "provider": {
            "quantizations": ["fp8"],
            "ignore": ["slow"],
            "require_parameters": True,
            "data_collection": "deny",
        },
    }
    assert passthrough_view(config) == {
        "temperature": 0.3,
        "top_p": 0.5,
        "tools": [1],
        "provider": {
            "quantizations": ["fp8"],
            "ignore": ["slow"],
            "require_parameters": True,
            "data_collection": "deny",
        },
    }
    assert passthrough_view(config, _g(temperature=0.7, clear=["ignore"])) == {
        "top_p": 0.5,
        "tools": [1],
        "provider": {"quantizations": ["fp8"], "require_parameters": True, "data_collection": "deny"},
    }
    everything = _g(quantizations=["fp8"], ignore=["x"], require_parameters=True, clear=["top_p"])
    assert passthrough_view(config, everything) == {
        "temperature": 0.3,
        "tools": [1],
        "provider": {"data_collection": "deny"},
    }
    assert passthrough_view({"model": "a", "provider": {"ignore": ["x"]}}, _g(ignore=["y"])) == {}


def test_the_passthrough_digest_follows_an_unmanaged_system_prompt_and_ignores_a_managed_one():
    config = {"model": "a", "tools": [1], "temperature": 0.3}
    plain = passthrough_digest(config)
    assert passthrough_digest(config, None, None) == passthrough_digest(config, None, "") == plain
    assert passthrough_digest(config, None, "Be brief.") != plain
    assert passthrough_digest(config, None, "Be brief.") != passthrough_digest(config, None, "Be terse.")
    for owned in (_g(system_prompt="Mine"), _g(clear=["system_prompt"])):
        assert passthrough_digest(config, owned, "Be brief.") == passthrough_digest(config, owned, "Be terse.") == plain
    # a guardrail the owner manages is theirs, so the live value of it is not part of what they reviewed
    managed = _g(temperature=0.9)
    assert passthrough_digest(config, managed) == passthrough_digest({**config, "temperature": 1.5}, managed)
    assert passthrough_digest(config) != passthrough_digest({**config, "temperature": 1.5})


def test_the_diff_names_a_system_prompt_that_changes_with_its_text_and_not_as_json():
    config = {"model": "a"}
    assert [(c.path, c.before, c.after) for c in diff_config(config, config, "Be brief.", "Be terse.")] == [
        ("system_prompt", "Be brief.", "Be terse.")
    ]
    assert [(c.before, c.after) for c in diff_config(config, config, "Be brief.", None)] == [("Be brief.", None)]
    assert [(c.before, c.after) for c in diff_config(config, config, None, "New.")] == [(None, "New.")]
    assert diff_config(config, config, "same", "same") == [] and diff_config(config, config, None, "") == []


def test_a_cleared_guardrail_shows_in_the_diff_as_a_key_that_goes():
    before = {"model": "a", "temperature": 0.3, "provider": {"ignore": ["x"], "only": ["y"]}}
    after = build_config(before, ["a"], ["y"], guardrails=_g(clear=["temperature", "ignore"]))
    assert {c.path: (c.before, c.after) for c in diff_config(before, after)} == {
        "temperature": ("0.3", None),
        "provider.ignore": ('["x"]', None),
        "provider.order": (None, '["y"]'),
    }


def test_a_key_the_write_took_off_that_is_still_on_the_preset_is_named_among_those_not_kept():
    sent = {"model": "m", "provider": {"only": ["x"]}}
    stored = {"model": "m", "temperature": 0.3, "provider": {"only": ["x"], "ignore": ["slow"]}}
    assert unsaved_keys(sent, stored, ["temperature", "provider.ignore", "top_p"]) == ["temperature", "provider.ignore"]
    assert unsaved_keys(sent, sent, ["temperature", "provider.ignore"]) == []
    assert unsaved_keys(sent, stored) == []  # nothing was cleared, so what is extra there is not this write's


def _write(router: respx.Router) -> respx.Route:
    return router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)


def _wrote(post: respx.Route) -> dict[str, Any]:
    return json.loads(post.calls[0].request.content)


def _listed(managed: list[str], key: str) -> bool:
    return (f"provider.{key}" if key in PROVIDER_KEYS else key) in managed


@pytest.mark.parametrize("key", list(SAMPLES))
def test_a_guardrail_that_is_set_is_written_in_the_request_and_read_back(client, key):
    new, old = SAMPLES[key]
    _own_as(KEY)
    _settings(guardrails={key: new})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, _holding({"model": "acme/best"}, key, old), version=2)
        post = _write(router)
        plan = _plan(client)
        assert plan["action"] == "update"
        assert _at(plan["request"]["body"], key) == new
        assert key in {c["path"].removeprefix("provider.") for c in plan["changes"]}
        applied = _apply(client, plan).json()
        assert applied["applied"] is True and applied["unsaved"] == []
    assert _at(_wrote(post), key) == new


@pytest.mark.parametrize("key", list(SAMPLES))
def test_a_guardrail_that_is_cleared_is_left_out_of_the_request_and_read_back_as_gone(client, key):
    _new, old = SAMPLES[key]
    _own_as(KEY)
    _settings(guardrails={"clear": [key]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, _holding({"model": "acme/best"}, key, old), version=2)
        post = _write(router)
        plan = _plan(client)
        assert plan["action"] == "update"
        assert _at(plan["request"]["body"], key) is None
        assert key in {c["path"].removeprefix("provider.") for c in plan["changes"]}
        applied = _apply(client, plan).json()
        assert applied["applied"] is True and applied["unsaved"] == []
    assert _at(_wrote(post), key) is None


@pytest.mark.parametrize("key", list(SAMPLES))
def test_a_guardrail_nobody_manages_is_sent_back_untouched_and_shown_as_passthrough(client, key):
    _new, old = SAMPLES[key]
    _own_as(KEY)
    desired = build_config(None, ["acme/best"], TAGS)
    if key == "quantizations":  # that one narrows which endpoints a plan may pin, which is not what this is about
        old = ["fp4", "fp8", "bf16"]
    live = _holding(desired, key, old)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=2)
        plan = _plan(client)
    assert plan["action"] == "unchanged" and plan["changes"] == []  # nothing here is the app's to change
    assert _at(plan["config"], key) == old
    assert _at(plan["passthrough"], key) == old
    assert _listed(plan["managed_keys"], key) is False


def test_a_plan_says_which_guardrails_it_manages_beside_the_keys_it_always_decides(client):
    _own_as(KEY)
    _settings(guardrails={"temperature": 0.7, "ignore": ["slow"], "clear": ["top_p"]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "temperature": 0.3, "top_p": 0.9, "tools": [1]}, version=2)
        plan = _plan(client)
    assert plan["managed_keys"] == [
        "model",
        "models",
        "route",
        "provider.only",
        "provider.order",
        "provider.sort",
        "temperature",
        "top_p",
        "provider.ignore",
    ]
    assert plan["passthrough"] == {"tools": [1]}  # what the app sends back as it found it: not what it sets or removes
    assert {c["path"] for c in plan["changes"]} == {
        "temperature",
        "top_p",
        "provider.ignore",
        "provider.only",
        "provider.order",
    }


def test_a_managed_guardrail_that_the_live_preset_already_holds_is_not_an_update(client):
    _own_as(KEY)
    _settings(guardrails={"temperature": 0.7, "clear": ["top_p"]})
    desired = build_config({"temperature": 0.7}, ["acme/best"], TAGS)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, desired, version=2)
        assert _plan(client)["action"] == "unchanged"
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {**desired, "temperature": 0.2}, version=2)
        assert _plan(client)["action"] == "update"
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {**desired, "top_p": 0.4}, version=2)
        assert _plan(client)["action"] == "update"


def test_a_guardrail_openrouter_keeps_after_it_was_taken_off_is_reported_not_hidden(client):
    _own_as(KEY)
    _settings(guardrails={"clear": ["top_p"]})

    def keeps_top_p(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        sent.pop("messages", None)
        return httpx.Response(200, json=_preset(SLUG, {**sent, "top_p": 0.9}, version=3))

    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "top_p": 0.9}, version=2)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=keeps_top_p)
        body = _apply(client, _plan(client)).json()
    assert body["unsaved"] == ["top_p"] and "did not keep: top_p" in body["message"]


def test_a_guardrail_set_to_a_value_openrouter_stored_differently_is_reported(client):
    _own_as(KEY)
    _settings(guardrails={"temperature": 0.7})

    def stores_one(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        sent.pop("messages", None)
        return httpx.Response(200, json=_preset(SLUG, {**sent, "temperature": 1.0}, version=3))

    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, version=2)
        router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=stores_one)
        assert _apply(client, _plan(client)).json()["unsaved"] == ["temperature"]


def test_the_guardrails_of_a_draft_are_what_the_preview_shows(client):
    draft = PresetSettings(slug="fresh", enabled=True, min_context=100_000, guardrails=_g(top_p=0.4))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, "vibonomics-fresh", None)
        plan = client.post("/api/presets/plan", json={"settings": draft.model_dump(mode="json")}).json()
    assert plan["draft"] is True and plan["config"]["top_p"] == 0.4


# ---- the system prompt: always resent, or the write would null it ---------------------------------------------------


class _PromptAccount:
    """A preset on the account that keeps what a write sends, system message included, as OpenRouter does.

    ``stores`` is what the version holds after a write: the prompt as sent by default; ``dropping`` makes the account
    lose it, to test a read-back that disagrees.
    """

    def __init__(
        self,
        router: respx.Router,
        config: dict[str, Any],
        prompt: str | None,
        version: int = 2,
        dropping: bool = False,
        answer_prompt: bool = True,
    ) -> None:
        self.config, self.prompt, self.version = config, prompt, version
        self.dropping, self.answer_prompt = dropping, answer_prompt
        self.bodies: list[dict[str, Any]] = []
        router.get(f"{BASE}/presets/{SLUG}").mock(side_effect=self._get)
        router.get(f"{BASE}/presets/{SLUG}/versions").mock(return_value=httpx.Response(200, json={"data": []}))
        self.post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=self._write)

    def _get(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_preset(SLUG, self.config, version=self.version, system_prompt=self.prompt))

    def _write(self, request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        self.bodies.append(json.loads(request.content))
        messages = sent.pop("messages", [])
        said = next((m["content"] for m in messages if m.get("role") == "system"), None)
        self.config, self.version = sent, self.version + 1
        self.prompt = None if self.dropping else said
        answer = _preset(SLUG, self.config, version=self.version, system_prompt=self.prompt)
        if not self.answer_prompt:
            del answer["data"]["designated_version"]["system_prompt"]
        return httpx.Response(200, json=answer)

    def system_messages(self, body: dict[str, Any]) -> list[str]:
        return [m["content"] for m in body["messages"] if m.get("role") == "system"]


LIVE_DESIRED = build_config(None, ["acme/best"], TAGS)  # what a plan for SLUG writes when nothing else is set


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"guardrails": {"temperature": 0.7}},
        {"guardrails": {"clear": ["top_p"]}},
        {"max_models": 2},
        {"route": "sort", "sort": "latency"},
        {"guardrails": {"ignore": ["wafer"], "require_parameters": True}},
    ],
)
def test_a_write_to_a_preset_that_has_a_system_prompt_always_carries_it_unless_the_owner_changed_it(client, settings):
    _own_as(KEY)
    _settings(**settings)
    live = {"model": "acme/best", "top_p": 0.9}
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, live, "Be brief.")
        plan = _plan(client)
        assert plan["action"] == "update"
        body = _apply(client, plan).json()
    assert body["applied"] is True and body["unsaved"] == []
    assert len(account.bodies) == 1
    assert account.system_messages(account.bodies[0]) == ["Be brief."]
    assert account.bodies[0]["messages"][-1] == PLACEHOLDER_MESSAGES[0]  # the user message the create call insists on
    assert account.prompt == "Be brief."
    assert "system_backups" not in _state()  # sent back as it was: nothing was lost, so nothing is kept


def test_a_managed_system_prompt_replaces_the_live_one_as_a_system_message_and_the_old_text_is_kept(client):
    _own_as(KEY)
    _settings(guardrails={"system_prompt": "Answer in one line."})
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, LIVE_DESIRED, "Be brief.", version=2)
        plan = _plan(client)
        assert plan["action"] == "update" and "system prompt" in plan["reason"]
        assert {c["path"]: (c["before"], c["after"]) for c in plan["changes"]} == {
            "system_prompt": ("Be brief.", "Answer in one line.")
        }
        assert plan["request"]["body"]["messages"] == [
            {"role": "system", "content": "Answer in one line."},
            *PLACEHOLDER_MESSAGES,
        ]
        body = _apply(client, plan).json()
    assert body["applied"] is True and body["unsaved"] == []
    assert account.system_messages(account.bodies[0]) == ["Answer in one line."]
    backup = _state()["system_backups"][SLUG]
    assert (backup["text"], backup["version"]) == ("Be brief.", 2) and backup["at"]


def test_a_cleared_system_prompt_sends_no_system_message_and_the_old_text_is_kept(client):
    _own_as(KEY)
    _settings(guardrails={"clear": ["system_prompt"]})
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, LIVE_DESIRED, "Be brief.", version=4)
        plan = _plan(client)
        assert plan["action"] == "update"
        assert plan["request"]["body"]["messages"] == PLACEHOLDER_MESSAGES
        assert [(c["path"], c["before"], c["after"]) for c in plan["changes"]] == [("system_prompt", "Be brief.", None)]
        body = _apply(client, plan).json()
    assert body["applied"] is True and body["unsaved"] == []
    assert account.system_messages(account.bodies[0]) == [] and account.prompt is None
    assert _state()["system_backups"][SLUG]["text"] == "Be brief." and _state()["system_backups"][SLUG]["version"] == 4


def test_a_managed_system_prompt_on_a_new_preset_is_created_with_it(client):
    _settings(guardrails={"system_prompt": "Answer in one line."})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_creates_with_prompt)
        plan = _plan(client)
        assert plan["action"] == "create"
        body = _apply(client, plan).json()
        sent = json.loads(post.calls[0].request.content)
    assert body["applied"] is True and body["unsaved"] == []
    assert sent["messages"][0] == {"role": "system", "content": "Answer in one line."}
    assert "system_backups" not in _state()  # there was nothing before it to lose


def _creates_with_prompt(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)
    messages = sent.pop("messages", [])
    said = next((m["content"] for m in messages if m.get("role") == "system"), None)
    return httpx.Response(200, json=_preset(SLUG, sent, version=1, system_prompt=said))


def test_a_backup_is_made_only_when_a_write_takes_text_away_and_is_capped(client):
    _own_as(KEY)
    long = "x" * 25_000  # a prompt made in the OpenRouter UI can be longer than the one the app lets the owner set
    _settings(guardrails={"system_prompt": "New."})
    with respx.mock(assert_all_called=False) as router:
        _PromptAccount(router, LIVE_DESIRED, long, version=2)
        assert _apply(client, _plan(client)).json()["applied"] is True
    assert len(_state()["system_backups"][SLUG]["text"]) == 20_000
    # the same text again is no change: nothing is written and the backup stays what it was
    before = _state()["system_backups"][SLUG]
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, LIVE_DESIRED, "New.", version=3)
        plan = _plan(client)
        assert plan["action"] == "unchanged" and _apply(client, plan).json()["applied"] is False
        assert account.bodies == []
    assert _state()["system_backups"][SLUG] == before


def test_a_dry_run_and_a_blocked_write_leave_no_backup(client):
    _own_as(KEY)
    _settings(guardrails={"clear": ["system_prompt"]})
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, LIVE_DESIRED, "Be brief.")
        _plan(client)
        assert account.bodies == []
    assert "system_backups" not in _state()
    with respx.mock(assert_all_called=False) as router:  # a live check that fails stops the write before the backup
        account = _PromptAccount(router, LIVE_DESIRED, "Be brief.")
        _live_listing(router, _without("wafer/bf16"))
        assert _apply(client, _plan(client)).status_code == 409
        assert account.bodies == []
    assert "system_backups" not in _state()


def test_a_system_prompt_openrouter_did_not_keep_is_reported_and_not_sent_again_each_run(client):
    _own_as(KEY)
    _settings(guardrails={"system_prompt": "Answer in one line."})
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, LIVE_DESIRED, "Be brief.", dropping=True)
        body = _apply(client, _plan(client)).json()
        assert body["applied"] is True and body["unsaved"] == ["system_prompt"]
        assert "did not keep: system_prompt" in body["message"]
        again = _plan(client)
        assert again["action"] == "unchanged" and any("did not keep" in w for w in again["warnings"])
        assert len(account.bodies) == 1


def test_a_write_whose_answer_leaves_the_prompt_out_is_checked_with_a_read(client):
    _own_as(KEY)
    _settings(guardrails={"system_prompt": "Answer in one line."})
    with respx.mock(assert_all_called=False) as router:
        _PromptAccount(router, LIVE_DESIRED, "Be brief.", answer_prompt=False)
        assert _apply(client, _plan(client)).json()["unsaved"] == []
    with respx.mock(assert_all_called=False) as router:
        _PromptAccount(router, LIVE_DESIRED, "Be brief.", answer_prompt=False, dropping=True)
        assert _apply(client, _plan(client)).json()["unsaved"] == ["system_prompt"]


def test_a_write_whose_prompt_cannot_be_checked_says_so_instead_of_claiming_it_was_kept(client):
    _own_as(KEY)
    _settings(guardrails={"system_prompt": "Answer in one line."})
    with respx.mock(assert_all_called=False) as router:
        account = _PromptAccount(router, LIVE_DESIRED, "Be brief.", answer_prompt=False)
        plan = _plan(client)
        gets = {"n": 0}

        def read(request: httpx.Request) -> httpx.Response:
            gets["n"] += 1
            if account.bodies:  # after the write, the account cannot be read
                return httpx.Response(500, json={"error": "down"})
            return account._get(request)

        router.get(f"{BASE}/presets/{SLUG}").mock(side_effect=read)
        body = _apply(client, plan).json()
    assert body["applied"] is True and body["unsaved"] == []
    assert "could not be checked" in body["message"]


def test_the_plan_digest_names_the_system_prompt_that_would_be_sent(client):
    _own_as(KEY)
    digests = []
    for prompt, settings in (
        ("One.", {}),
        ("Two.", {}),
        ("One.", {"guardrails": {"system_prompt": "Mine."}}),
        ("One.", {"guardrails": {"system_prompt": "Mine too."}}),
    ):
        _settings(**settings)
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, LIVE_DESIRED, version=2, system_prompt=prompt)
            digests.append(_plan(client)["digest"])
    assert len(set(digests)) == 4  # the same version number, the same config, and a different prompt in each


def test_a_write_whose_answer_was_lost_is_claimed_only_if_the_preset_holds_the_prompt_that_was_sent(client):
    _own_as(KEY)
    _settings(guardrails={"system_prompt": "Answer in one line."})
    for holds, claimed in (("Answer in one line.", True), ("Be brief.", False), (None, False)):
        with respx.mock(assert_all_called=False) as router:
            account = _PromptAccount(router, LIVE_DESIRED, "Be brief.")
            plan = _plan(client)

            def lost(request: httpx.Request, account=account, holds=holds) -> httpx.Response:
                sent = json.loads(request.content)
                account.config, account.prompt = {k: v for k, v in sent.items() if k != "messages"}, holds
                raise httpx.ReadTimeout("read timed out", request=request)

            account.post.mock(side_effect=lost)
            response = _apply(client, plan)
        assert (response.status_code == 200) is claimed, (holds, response.text)
        if claimed:
            assert "read back" in response.json()["message"]


def test_a_template_system_prompt_is_not_copied_and_the_plan_says_how_to_give_one(client):
    _settings(template_slug="my-tools")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", {"model": "other/model"}, version=3, system_prompt="Template prompt.")
        plan = _plan(client)
    assert plan["request"]["body"]["messages"] == PLACEHOLDER_MESSAGES
    assert any("system prompt" in w and "guardrails" in w for w in plan["warnings"])
    _settings(template_slug="my-tools", guardrails={"system_prompt": "Mine."})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _remote(router, "my-tools", {"model": "other/model"}, version=3, system_prompt="Template prompt.")
        plan = _plan(client)
    assert plan["request"]["body"]["messages"][0] == {"role": "system", "content": "Mine."}
    assert not any("template has a system prompt" in w for w in plan["warnings"])


def _hand_keeps_prompts(router: respx.Router) -> respx.Route:
    """A write to HAND that the account takes with the system message it carries."""

    def write(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        messages = sent.pop("messages", [])
        said = next((m["content"] for m in messages if m.get("role") == "system"), None)
        return httpx.Response(200, json=_preset(HAND, sent, version=9, system_prompt=said))

    return router.post(f"{BASE}/presets/{HAND}/chat/completions").mock(side_effect=write)


@pytest.mark.asyncio
async def test_a_scheduled_run_resends_the_prompt_of_an_adopted_preset_and_stops_when_the_prompt_was_edited(client):
    from vibonomics import presets as engine

    _adopt_hand_made(client, system_prompt="Be brief.")
    _items(_item(), _adopted_item(enabled=True, auto_apply=True))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=3, system_prompt="Be brief.")
        post = _hand_keeps_prompts(router)
        await engine.sync(HAND, "schedule")
        assert post.call_count == 1  # nothing changed since the owner looked
        assert json.loads(post.calls[0].request.content)["messages"][0] == {"role": "system", "content": "Be brief."}
    # someone rewrites the prompt in the OpenRouter UI: it is part of what the owner agreed to hand over
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=5, system_prompt="Be rude.")
        post = _hand_keeps_prompts(router)
        await engine.sync(HAND, "schedule")
        assert post.call_count == 0
    assert _state()["last"][HAND]["status"] == "blocked"
    # and a manual apply, after the plan showed it, accepts the new text and the timer goes on
    with respx.mock(assert_all_called=False) as router:
        _remote(router, HAND, HAND_CONFIG, version=5, system_prompt="Be rude.")
        post = _hand_keeps_prompts(router)
        assert _apply(client, _plan(client, HAND), HAND).status_code == 200
        assert json.loads(post.calls[0].request.content)["messages"][0]["content"] == "Be rude."
    assert _state()["reviewed"][HAND] == passthrough_digest(HAND_CONFIG, None, "Be rude.")


@pytest.mark.asyncio
async def test_a_managed_system_prompt_on_an_adopted_preset_does_not_wait_for_a_review_of_the_live_one(client):
    from vibonomics import presets as engine

    _adopt_hand_made(client, system_prompt="Be brief.")
    _items(_item(), _adopted_item(enabled=True, auto_apply=True, guardrails={"system_prompt": "Mine."}))
    state = _state()
    state["reviewed"][HAND] = passthrough_digest(HAND_CONFIG, PresetGuardrails(system_prompt="Mine."), "Be brief.")
    _set_state(state)
    with respx.mock(assert_all_called=False) as router:  # the live prompt is changed, which the owner replaces anyway
        _remote(router, HAND, HAND_CONFIG, version=5, system_prompt="Be rude.")
        post = _hand_keeps_prompts(router)
        await engine.sync(HAND, "schedule")
        assert post.call_count == 1
        assert json.loads(post.calls[0].request.content)["messages"][0]["content"] == "Mine."


def test_taking_an_adopted_preset_out_of_the_list_forgets_its_prompt_backup_too(client):
    _own_both()
    state = _state()
    state["system_backups"] = {
        HAND: {"text": "old", "version": 1, "at": "2026-11-01T00:00:00+00:00"},
        SLUG: {"text": "o"},
    }
    _set_state(state)
    _items(_item(), _adopted_item())
    current = client.get("/api/settings").json()["settings"]
    kept = [i for i in _saved_items(client) if i["slug"] != HAND]
    assert client.put("/api/settings", json={"settings": _with_items(current, kept)}).status_code == 200
    assert set(_state()["system_backups"]) == {SLUG}


# ---- capability-aware pinning, and the quantization and ignore guardrails -------------------------------------------


def _endpoint(tag: str, price: float, params: list[str] | None, cap: int | None = None, quant="fp8") -> dict[str, Any]:
    """An endpoint as the adapter stores it, with the parameters it supports and its output cap."""
    return {
        "provider": tag.split("/")[0].title(),
        "tag": tag,
        "quantization": quant,
        "input": price,
        "output": price * 4,
        "params": params,
        "max_completion_tokens": cap,
        "uptime_1d": 99,
    }


def _stored(model_id: str, *rows: dict[str, Any]) -> None:
    _set_extra(model_id, {"endpoints": list(rows)})


def _tags_of(plan: dict[str, Any]) -> list[str]:
    return [p["tag"] for p in plan["providers"]]


def test_an_endpoint_whose_precision_is_not_listed_is_unknown_and_a_quantization_list_leaves_it_out():
    rows = [
        {"tag": "a/x", "input": 0.1, "output": 0.1, "quantization": None},
        {"tag": "b/x", "input": 0.2, "output": 0.2, "quantization": "unknown"},
        {"tag": "c/x", "input": 0.3, "output": 0.3, "quantization": "fp8"},
        {"tag": "d/x", "input": 0.4, "output": 0.4},
    ]
    assert _tags(choose_endpoints(rows, TASK, {"quantizations": ["fp8"]}, 5)) == ["c/x"]
    assert _tags(choose_endpoints(rows, TASK, {"quantizations": ["fp8", "unknown"]}, 5)) == [
        "a/x",
        "b/x",
        "c/x",
        "d/x",
    ]
    assert _tags(choose_endpoints(rows, TASK, {"quantizations": ["unknown"]}, 5)) == ["a/x", "b/x", "d/x"]
    assert len(choose_endpoints(rows, TASK, {}, 5)) == 4


def test_the_pins_are_the_endpoints_that_take_every_parameter_the_owner_sets(client):
    _stored(
        "acme/best",
        _endpoint("plain/x", 0.01, ["temperature"]),
        _endpoint("both/x", 0.02, ["temperature", "top_p"]),
        _endpoint("also/x", 0.03, ["temperature", "top_p", "tools"]),
    )
    _settings(guardrails={"temperature": 0.7, "top_p": 0.9})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["both/x", "also/x"]
    assert plan["warnings"] == [] and plan["action"] == "create"


def test_an_endpoint_that_does_not_report_its_parameters_is_still_pinned(client):
    _stored("acme/best", _endpoint("silent/x", 0.01, None), _endpoint("no/x", 0.02, ["tools"]))
    _settings(guardrails={"top_p": 0.9})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["silent/x"] and plan["warnings"] == []


def test_a_parameter_no_live_endpoint_takes_is_warned_about_and_the_preset_is_pinned_without_it(client):
    _stored("acme/best", _endpoint("a/x", 0.01, ["tools"]), _endpoint("b/x", 0.02, ["tools", "seed"]))
    _settings(guardrails={"repetition_penalty": 1.1})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["action"] == "create" and _tags_of(plan) == ["a/x", "b/x"]
    assert "repetition_penalty will be ignored for acme/best: no live endpoint supports it." in plan["warnings"]
    assert plan["config"]["repetition_penalty"] == 1.1  # still written: it is what the owner asked for


def test_each_parameter_nobody_takes_gets_its_own_warning_and_the_ones_somebody_takes_do_not(client):
    _stored("acme/best", _endpoint("a/x", 0.01, ["temperature"]), _endpoint("b/x", 0.02, ["temperature"]))
    _settings(guardrails={"temperature": 0.5, "top_p": 0.9, "presence_penalty": 0.1})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    said = [w for w in plan["warnings"] if "will be ignored" in w]
    assert said == [
        "presence_penalty will be ignored for acme/best: no live endpoint supports it.",
        "top_p will be ignored for acme/best: no live endpoint supports it.",
    ]


def test_parameters_that_are_each_taken_but_never_together_are_warned_about_as_a_set(client):
    _stored("acme/best", _endpoint("a/x", 0.01, ["temperature"]), _endpoint("b/x", 0.02, ["top_p"]))
    _settings(guardrails={"temperature": 0.5, "top_p": 0.9})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["a/x", "b/x"]
    assert any("temperature and top_p will be ignored for acme/best" in w and "at once" in w for w in plan["warnings"])


def test_with_require_parameters_a_model_no_endpoint_can_serve_blocks_the_plan_and_names_the_parameter(client):
    _stored("acme/best", _endpoint("a/x", 0.01, ["tools"]), _endpoint("b/x", 0.02, ["seed"]))
    _settings(guardrails={"top_p": 0.9, "require_parameters": True})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert _apply(client, "anything").status_code == 409
        assert post.call_count == 0
    assert plan["action"] == "blocked" and plan["request"] is None and plan["digest"] is None
    assert "acme/best" in plan["reason"] and "top_p" in plan["reason"] and "require_parameters" in plan["reason"]


def test_a_require_parameters_the_live_preset_already_has_counts_as_much_as_one_the_owner_set(client):
    _own_as(KEY)
    _stored("acme/best", _endpoint("a/x", 0.01, ["tools"]))
    _settings(guardrails={"top_p": 0.9})  # require_parameters is not managed: it is the live preset's own
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best", "provider": {"require_parameters": True}}, version=2)
        plan = _plan(client)
    assert plan["action"] == "blocked" and "top_p" in plan["reason"]


def test_require_parameters_with_every_parameter_taken_is_not_a_problem(client):
    _stored("acme/best", _endpoint("a/x", 0.01, ["temperature", "top_p"]), _endpoint("b/x", 0.02, ["tools"]))
    _settings(guardrails={"top_p": 0.9, "require_parameters": True})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["action"] == "create" and _tags_of(plan) == ["a/x"]
    assert plan["config"]["provider"]["require_parameters"] is True


def test_an_endpoint_whose_output_cap_is_below_max_tokens_is_not_pinned_and_a_model_with_none_is_warned_about(client):
    _stored(
        "acme/best",
        _endpoint("small/x", 0.01, ["max_tokens"], cap=4_096),
        _endpoint("big/x", 0.02, ["max_tokens"], cap=65_536),
    )
    _settings(guardrails={"max_tokens": 8_192})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["big/x"] and plan["config"]["max_tokens"] == 8_192
    _settings(guardrails={"max_tokens": 100_000})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["small/x", "big/x"]
    assert any("max_tokens will be ignored for acme/best" in w and "100,000" in w for w in plan["warnings"])
    _settings(guardrails={"max_tokens": 100_000, "require_parameters": True})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        blocked = _plan(client)
    assert blocked["action"] == "blocked" and "max_tokens" in blocked["reason"] and "100,000" in blocked["reason"]


def test_capability_is_judged_per_model_so_one_model_can_be_pinned_and_another_warned_about(client):
    _settings(max_models=2, guardrails={"top_p": 0.9})
    _stored("acme/best", _endpoint("good/x", 0.01, ["top_p"]))
    _stored("acme/steady", _endpoint("limited/x", 0.02, ["tools"]))
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["good/x", "limited/x"]
    assert plan["warnings"] == ["top_p will be ignored for acme/steady: no live endpoint supports it."]


def test_a_model_with_no_provider_data_is_left_unpinned_as_it_always_was_whatever_is_required(client):
    _settings(guardrails={"top_p": 0.9, "require_parameters": True})  # acme/best has the stored ENDPOINTS: no params
    _set_extra("acme/best", {})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["action"] == "create" and plan["providers"] == []
    assert any("No provider data" in w for w in plan["warnings"])


def test_a_managed_quantization_list_filters_the_pins_and_is_written(client):
    _settings(guardrails={"quantizations": ["bf16"]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["wafer/bf16"]
    assert plan["config"]["provider"]["quantizations"] == ["bf16"]


def test_a_managed_value_is_what_filters_not_the_one_the_live_preset_has(client):
    _own_as(KEY)
    live = {"model": "acme/best", "provider": {"quantizations": ["fp8"], "ignore": ["novita"]}}
    _settings(guardrails={"quantizations": ["fp4"], "clear": ["ignore"]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=2)
        plan = _plan(client)
    assert _tags_of(plan) == ["novita/fp4"]  # fp4 is the managed list, and novita is no longer ignored
    assert plan["config"]["provider"]["quantizations"] == ["fp4"] and "ignore" not in plan["config"]["provider"]


def test_a_quantization_list_the_owner_took_off_no_longer_filters_though_the_live_preset_has_it(client):
    _own_as(KEY)
    live = {"model": "acme/best", "provider": {"quantizations": ["fp8"]}}
    _settings(guardrails={"clear": ["quantizations"]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=2)
        plan = _plan(client)
    assert _tags_of(plan) == ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]
    assert "provider" in plan["config"] and "quantizations" not in plan["config"]["provider"]


def test_a_quantization_list_the_owner_does_not_manage_still_filters_as_the_live_preset_has_it(client):
    _own_as(KEY)
    live = {"model": "acme/best", "provider": {"quantizations": ["fp8"]}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live, version=2)
        plan = _plan(client)
    assert _tags_of(plan) == ["deepinfra/fp8", "slow/fp8"]
    assert plan["config"]["provider"]["quantizations"] == ["fp8"]


def test_ignored_providers_are_never_pinned_and_are_written(client):
    _settings(guardrails={"ignore": ["Novita", "wafer"]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert _tags_of(plan) == ["deepinfra/fp8", "slow/fp8"]
    assert plan["config"]["provider"]["ignore"] == ["Novita", "wafer"]


def _ids(session, settings: AppSettings, item: PresetSettings) -> tuple[list[str], Excluded]:
    ids = [p.id for p in session.query(ProviderConfig).filter_by(type="openrouter")]
    excluded = Excluded()
    return [c.model_id for c in rank(session, settings, ids, item, excluded)[0]], excluded


def test_a_model_none_of_whose_endpoints_passes_the_managed_filters_is_left_out_of_the_ranking(client):
    with session_scope() as session:
        settings = load_settings(session).model_copy(deep=True)
        item = settings.presets.items[0]
        assert "acme/best" in _ids(session, settings, item)[0]
        item.guardrails = PresetGuardrails(quantizations=["int4"])  # no endpoint of acme/best serves int4
        names, excluded = _ids(session, settings, item)
        assert "acme/best" not in names and excluded.unserved == ["acme/best"]
        assert "acme/steady" in names  # no endpoint data: nothing says it cannot be served
        item.guardrails = PresetGuardrails(ignore=["deepinfra", "wafer", "novita", "slow", "broken"])
        names, excluded = _ids(session, settings, item)
        assert "acme/best" not in names and excluded.unserved == ["acme/best"]
        item.guardrails = PresetGuardrails(quantizations=["fp8"], ignore=["deepinfra"])  # slow/fp8 still passes
        names, excluded = _ids(session, settings, item)
        assert "acme/best" in names and excluded.unserved == []
        item.guardrails = PresetGuardrails(clear=["quantizations"])  # taken off: nothing narrows the ranking
        assert _ids(session, settings, item)[1].unserved == []


def test_a_model_left_out_for_its_endpoints_is_named_in_a_warning_and_the_next_one_leads(client):
    _settings(guardrails={"quantizations": ["int4"]})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/steady"
    assert any("acme/best" in w and "quantization and ignore" in w for w in plan["warnings"])


def test_when_the_guardrails_leave_no_model_the_plan_says_so(client):
    _settings(guardrails={"quantizations": ["int4"]}, min_score=85)  # only acme/pricey (no endpoint data) is above it
    _give_endpoints("acme/pricey", [("p/fp8", 1.0, 4.0)])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["action"] == "blocked" and "No OpenRouter model passes" in plan["reason"]
    assert "quantization and ignore guardrails" in plan["reason"]


# ---- the live check with the guardrails, and the free tier -----------------------------------------------------------


def _live_with(rows: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [_live_ep(tag, 0.1, 0.4, **fields) for tag, fields in rows]


def test_a_plan_whose_live_endpoints_no_longer_pass_the_managed_quantization_is_refused_as_unservable(client):
    _settings(guardrails={"quantizations": ["bf16"]})  # the stored copy has wafer/bf16, which is what gets pinned
    live = _live_with([("wafer/bf16", {"quantization": "fp8"}), ("novita/fp4", {"quantization": "fp4"})])
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router, live)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        assert plan["valid"] is False
        assert [(c["check"], c["level"]) for c in plan["validation"]] == [("served", "error")]
        assert "will never be served acme/best" in _errors(plan)[0]["message"]
        assert _apply(client, plan).status_code == 409
        assert post.call_count == 0


def test_the_live_check_uses_the_filters_the_plan_will_write_even_when_the_owner_does_not_manage_them(client):
    _own_as(KEY)
    live_preset = {"model": "acme/best", "provider": {"ignore": ["wafer", "novita", "deepinfra", "slow"]}}
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, live_preset, version=2)
        _live_listing(router)
        plan = _plan(client)
    assert plan["valid"] is False and [c["check"] for c in _errors(plan)] == ["served"]


def test_a_pinned_endpoint_that_lacks_a_managed_parameter_is_an_error_only_with_require_parameters(client):
    _stored(
        "acme/best",
        _endpoint("novita/fp4", 0.08, ["temperature"]),
        _endpoint("deepinfra/fp8", 0.1, ["temperature"]),
    )
    live = _live_with(
        [
            ("novita/fp4", {"supported_parameters": ["temperature"]}),
            ("deepinfra/fp8", {"supported_parameters": ["tools"]}),  # the listing has moved on from the stored copy
        ]
    )
    for strict, valid in ((True, False), (False, True)):
        _settings(guardrails={"temperature": 0.5, "require_parameters": strict})
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, None)
            _live_listing(router, live)
            plan = _plan(client)
        problems = [c for c in plan["validation"] if c["check"] == "params"]
        assert plan["valid"] is valid, strict
        assert [(c["level"], c["provider"]) for c in problems] == [("error" if strict else "warning", "deepinfra/fp8")]


def test_a_managed_token_cap_above_what_a_live_endpoint_allows_is_an_error(client):
    _stored("acme/best", _endpoint("novita/fp4", 0.08, ["max_tokens"], cap=65_536))
    live = _live_with([("novita/fp4", {"max_completion_tokens": 4_096})])
    _settings(guardrails={"max_tokens": 8_192})
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router, live)
        plan = _plan(client)
    assert plan["valid"] is False and [c["check"] for c in _errors(plan)] == ["max_tokens"]


def test_a_preset_of_free_models_only_warns_in_the_plan_and_in_the_live_check(client):
    _settings(include_free=True)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        _live_listing(router)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/free:free"
    assert FREE_TIER_WARNING in plan["warnings"]
    assert "Every model here is free" in FREE_TIER_WARNING and "00:00 UTC" in FREE_TIER_WARNING
    assert plan["valid"] is True  # a warning, never a refusal
    free = [c for c in plan["validation"] if c["check"] == "free"]
    assert [(c["level"], c["message"]) for c in free] == [("warning", FREE_TIER_WARNING)]


def test_a_lineup_with_one_paid_model_does_not_warn_about_the_free_cap(client):
    _settings(include_free=True, max_models=2)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert plan["pick"]["model_id"] == "acme/free:free" and len(plan["picks"]) == 2
    assert not any("free" in w and "daily" in w for w in plan["warnings"])
    assert not [c for c in plan["validation"] if c["check"] == "free"]


def test_a_preset_with_no_free_model_has_no_free_warning(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        plan = _plan(client)
    assert not any("daily" in w for w in plan["warnings"])
