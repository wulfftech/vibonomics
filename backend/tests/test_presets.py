"""The best-value preset: ranking, the config it builds, the dry run, and the write that follows it."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete

from vibonomics import crypto
from vibonomics.app import create_app
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import BenchmarkScore, Model, ProviderConfig, Setting
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
    owner_fingerprint,
    rank,
    select_models,
    unsaved_keys,
)
from vibonomics.pricing import Task
from vibonomics.settings_store import (
    AppSettings,
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


def test_a_system_prompt_blocks_the_update_because_the_api_cannot_carry_it(client):
    with session_scope() as session:
        row = session.get(Setting, STATE_KEY) or Setting(key=STATE_KEY, value={})
        row.value = {"owned": [SLUG]}
        session.merge(row)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/best"}, system_prompt="Be brief.")
        plan = _plan(client)
    assert plan["action"] == "blocked"
    assert "system prompt" in plan["reason"]


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
