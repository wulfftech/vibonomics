"""The best-value preset: ranking, the config it builds, the dry run, and the write that follows it."""

from __future__ import annotations

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
from vibonomics.settings_store import AppSettings, PresetSettings, load_settings, preset_slug, save_settings

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
        save_settings(session, AppSettings(presets=PresetSettings(**fields)))


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


def _plan(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/presets/plan")
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    return response.json()


# ---- the pieces ----------------------------------------------------------------------------------------------


def test_the_cheapest_endpoints_for_one_task_come_first_and_the_unusable_are_skipped():
    chosen = choose_endpoints(ENDPOINTS, None, TASK, {}, 3)
    assert [e["tag"] for e in chosen] == ["novita/fp4", "deepinfra/fp8", "wafer/bf16"]
    assert chosen[0]["task_cost"] == pytest.approx((600_000 * 0.08 + 20_000 * 0.35) / 1e6)
    assert [e["tag"] for e in choose_endpoints(ENDPOINTS, None, TASK, {}, 10)][-1] == "slow/fp8"


def test_the_presets_own_provider_filters_narrow_the_choice():
    only_fp8 = choose_endpoints(ENDPOINTS, None, TASK, {"quantizations": ["FP8"]}, 5)
    assert [e["tag"] for e in only_fp8] == ["deepinfra/fp8", "slow/fp8"]
    ignoring = choose_endpoints(ENDPOINTS, None, TASK, {"ignore": ["Novita", "wafer"]}, 5)
    assert [e["tag"] for e in ignoring] == ["deepinfra/fp8", "slow/fp8"]


def test_the_cache_price_counts_toward_which_endpoint_is_cheapest():
    cheap_cache = [
        {"tag": "a/x", "input": 1.0, "output": 1.0},
        {"tag": "b/x", "input": 1.2, "output": 1.0},
    ]
    assert choose_endpoints(cheap_cache, 0.0, TASK, {}, 1)[0]["tag"] == "a/x"
    assert choose_endpoints(cheap_cache, 0.0, Task(1000, 1000, 0.0), {}, 2)[0]["task_cost"] == pytest.approx(0.002)


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
    assert config["provider"] == {"allow_fallbacks": True}
    assert build_config(None, ["m"], ["x/fp8"]) == {
        "model": "m",
        "provider": {"only": ["x/fp8"], "order": ["x/fp8"], "allow_fallbacks": True},
    }


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
        ids = [p.id for p in session.query(ProviderConfig).filter_by(type="openrouter")]
        settings.presets.min_context = 0
        ranked, _task, profile = rank(session, settings, ids)
        assert profile == "agentic"
        assert [c.model_id for c in ranked] == ["acme/small", "acme/best", "acme/steady", "acme/pricey"]
        settings.presets.min_context = 100_000
        assert [c.model_id for c in rank(session, settings, ids)[0]] == ["acme/best", "acme/steady", "acme/pricey"]
        settings.presets.require_tool_call = False
        settings.presets.include_free = True
        names = [c.model_id for c in rank(session, settings, ids)[0]]
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
            "allow_fallbacks": True,
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
    assert {c["path"] for c in plan["changes"]} == {"provider.allow_fallbacks", "provider.only", "provider.order"}


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
        response = client.post("/api/presets/apply", json={"digest": plan["digest"]})
    assert response.status_code == 200, response.text
    assert KEY not in response.text
    body = response.json()
    assert body["applied"] is True and body["version"] == 1 and body["unsaved"] == []
    assert post.call_count == 1
    request = post.calls[0].request
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == plan["request"]["body"]
    state = client.get("/api/presets/state").json()
    assert state["owned"] == [SLUG]
    assert (state["slug"], state["model_id"], state["version"]) == (SLUG, "acme/best", 1)
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
        body = client.post("/api/presets/apply", json={"digest": plan["digest"]}).json()
    assert body["unsaved"] == ["cache_enabled"]
    assert "did not keep: cache_enabled" in body["message"]


def test_apply_refuses_a_plan_that_has_changed_since_it_was_reviewed(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        response = client.post("/api/presets/apply", json={"digest": "stale"})
        assert post.call_count == 0
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_apply_does_nothing_until_the_operator_allows_writes(client):
    _settings(enabled=False)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(side_effect=_echo)
        plan = _plan(client)
        response = client.post("/api/presets/apply", json={"digest": plan["digest"]})
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
        response = client.post("/api/presets/apply", json={"digest": plan["digest"]})
        assert post.call_count == 0
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["message"] == "Already up to date."
    assert client.get("/api/presets/state").json()["last_status"] == "unchanged"


def test_a_failed_write_is_reported_recorded_and_not_retried(client):
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, None)
        post = router.post(f"{BASE}/presets/{SLUG}/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": f"boom {KEY}"})
        )
        plan = _plan(client)
        response = client.post("/api/presets/apply", json={"digest": plan["digest"]})
        assert post.call_count == 1
    assert response.status_code == 502
    assert KEY not in response.text
    state = client.get("/api/presets/state").json()
    assert state["last_status"] == "failed"
    assert state["owned"] == []  # nothing was created, so nothing is ours


# ---- settings and schedule -----------------------------------------------------------------------------------


def test_a_name_gets_the_prefix_and_is_made_into_a_slug():
    assert preset_slug("best value") == "vibonomics-best-value"
    assert preset_slug("  My Coding Preset! ") == "vibonomics-my-coding-preset"
    assert preset_slug("vibonomics-fast") == "vibonomics-fast"  # not prefixed twice
    assert preset_slug("Vibonomics Fast") == "vibonomics-fast"
    for bad in ("", "   ", "!!!", "x" * 80):
        with pytest.raises(ValueError):
            preset_slug(bad)


def test_the_name_the_operator_types_is_saved_with_the_prefix(client):
    current = client.get("/api/settings").json()["settings"]
    for typed in ("", "!!!"):
        bad = {**current, "presets": {**current["presets"], "slug": typed}}
        assert client.put("/api/settings", json={"settings": bad}).status_code == 422, typed
    good = {**current, "presets": {**current["presets"], "slug": "Fast coding"}}
    saved = client.put("/api/settings", json={"settings": good})
    assert saved.status_code == 200
    assert saved.json()["settings"]["presets"]["slug"] == "vibonomics-fast-coding"


def test_up_to_three_models_and_ten_providers_are_allowed_and_no_more(client):
    # OpenRouter refuses a preset that lists a fourth model: HTTP 400 "A preset can have at most 3 models".
    current = client.get("/api/settings").json()["settings"]
    ok = {**current, "presets": {**current["presets"], "max_models": 3, "max_providers": 10}}
    assert client.put("/api/settings", json={"settings": ok}).status_code == 200
    for field, over in (("max_models", 4), ("max_providers", 11)):
        for value in (0, over):
            bad = {**current, "presets": {**current["presets"], field: value}}
            assert client.put("/api/settings", json={"settings": bad}).status_code == 422, (field, value)


def test_the_scheduled_update_can_run_every_thirty_minutes_and_no_more_often(client):
    current = client.get("/api/settings").json()["settings"]
    for hours, status in ((0.5, 200), (0.25, 422)):
        body = {**current, "presets": {**current["presets"], "interval_hours": hours}}
        assert client.put("/api/settings", json={"settings": body}).status_code == status, hours
    assert PresetSettings(enabled=True, auto_apply=True, interval_hours=0.5).interval_hours == 0.5


@pytest.mark.asyncio
async def test_the_scheduler_runs_the_preset_every_thirty_minutes():
    from vibonomics import scheduler

    try:
        scheduler.start(AppSettings())
        scheduler.reschedule(AppSettings(presets=PresetSettings(enabled=True, auto_apply=True, interval_hours=0.5)))
        job = scheduler._scheduler.get_job("preset-sync")
        assert job is not None and job.trigger.interval.total_seconds() == 30 * 60
    finally:
        scheduler.shutdown()


def test_it_is_off_until_the_operator_turns_it_on():
    assert AppSettings().presets.enabled is False
    assert AppSettings().presets.auto_apply is False


@pytest.mark.asyncio
async def test_the_scheduler_adds_and_removes_the_preset_job():
    from vibonomics import scheduler

    try:
        scheduler.start(AppSettings())
        assert scheduler._scheduler.get_job("preset-sync") is None
        scheduler.reschedule(AppSettings(presets=PresetSettings(enabled=True, auto_apply=False)))
        assert scheduler._scheduler.get_job("preset-sync") is None
        scheduler.reschedule(AppSettings(presets=PresetSettings(enabled=True, auto_apply=True, interval_hours=12)))
        job = scheduler._scheduler.get_job("preset-sync")
        assert job is not None and job.trigger.interval.total_seconds() == 12 * 3600
        scheduler.reschedule(AppSettings(presets=PresetSettings(enabled=False, auto_apply=True)))
        assert scheduler._scheduler.get_job("preset-sync") is None
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
    # only acme/best has endpoint data, so the providers are its four cheapest
    assert [p["tag"] for p in plan["providers"]] == ["novita/fp4", "deepinfra/fp8", "wafer/bf16", "slow/fp8"]
    assert all(p["models"] == ["acme/best"] for p in plan["providers"])
    assert plan["runners_up"] == []


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
        for key, value in patch.items():
            setattr(settings.presets, key, value)
        ids = [p.id for p in session.query(ProviderConfig).filter_by(type="openrouter")]
        return [c.model_id for c in rank(session, settings, ids)[0]]


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
    on = {**current, "presets": {**current["presets"], "max_task_cost": 0.05, "min_value": 2000}}
    saved = client.put("/api/settings", json={"settings": on}).json()["settings"]["presets"]
    assert (saved["max_task_cost"], saved["min_value"]) == (0.05, 2000)
    off = {**current, "presets": {**current["presets"], "max_task_cost": None, "min_value": None}}
    cleared = client.put("/api/settings", json={"settings": off}).json()["settings"]["presets"]
    assert cleared["max_task_cost"] is None and cleared["min_value"] is None
    for field in ("max_task_cost", "min_value"):
        bad = {**current, "presets": {**current["presets"], field: -1}}
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
    return client.get("/api/settings").json()["settings"]["presets"]


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
        response = client.post("/api/presets/apply", json={"digest": draft["digest"]})
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
        plan = client.post("/api/presets/plan").json()
        assert post.call_count == 0
    assert plan["action"] == "blocked"
    assert "different OpenRouter key or account" in plan["reason"]
    assert plan["request"] is None and plan["digest"] is None
    assert OTHER_KEY not in json.dumps(plan) and KEY not in json.dumps(plan)


def test_a_preset_made_under_the_key_in_use_is_still_ours(client):
    _own_as(KEY)
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/steady"}, version=3)
        plan = _plan(client)
    assert plan["action"] == "update"
    assert plan["digest"]


def test_a_slug_recorded_before_accounts_were_tracked_is_trusted_once_and_then_tied_to_the_key(client, caplog):
    _own()  # the form the live instance holds: a bare slug and no record
    with caplog.at_level("INFO", logger="vibonomics.presets"), respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/steady"}, version=3)
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
        _remote(router, SLUG, {"model": "acme/steady"}, version=4, id="p-1", created_at="2026-11-01T00:00:00+00:00")
        assert _plan(client)["action"] == "update"
    # a creation time alone is enough to tell them apart
    _own_as(KEY, created_at="2026-11-01T00:00:00Z")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "by/hand"}, version=1, created_at="2026-11-04T00:00:00Z")
        assert _plan(client)["action"] == "blocked"


def test_a_response_with_no_id_or_creation_time_leaves_the_key_to_decide(client):
    _own_as(KEY, preset_id="p-1", created_at="2026-11-01T00:00:00Z")
    with respx.mock(assert_all_called=False) as router:
        _remote(router, SLUG, {"model": "acme/steady"}, version=2)  # the live preset names neither
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
        assert client.post("/api/presets/apply", json={"digest": plan["digest"]}).status_code == 200
    state = _state()
    assert state["owned"] == [SLUG]
    owner = state["owners"][SLUG]
    assert owner["fingerprint"] == owner_fingerprint(BASE, KEY)
    assert (owner["version_at_create"], owner["preset_id"], owner["created_at"]) == (1, "p-1", "2026-11-05T00:00:00Z")
    assert KEY not in json.dumps(state)


def test_the_digest_names_the_account_and_the_version_it_was_planned_against(client):
    config = {"model": "acme/best"}
    assert digest_of(SLUG, config, "aaaa", 1) != digest_of(SLUG, config, "bbbb", 1)
    assert digest_of(SLUG, config, "aaaa", 1) != digest_of(SLUG, config, "aaaa", 2)
    _own_as(KEY)
    digests = []
    for version in (2, 3):
        with respx.mock(assert_all_called=False) as router:
            _remote(router, SLUG, {"model": "acme/steady"}, version=version)
            digests.append(_plan(client)["digest"])
    assert digests[0] != digests[1]  # someone made a version in between, so the reviewed plan is stale
