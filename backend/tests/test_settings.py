"""Settings validation: bounds, usage profiles, and stored values that a stricter release no longer accepts."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vibonomics.api import settings as settings_api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import Setting
from vibonomics.settings_store import (
    AppSettings,
    PresetSettings,
    RefreshSettings,
    ScoringSettings,
    UsageProfile,
    load_settings,
    save_settings,
)


@pytest.fixture(autouse=True)
def database():
    init_db()
    with session_scope() as s:
        save_settings(s, AppSettings())


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_api.router)
    return TestClient(app)


def _profile(**changes: object) -> dict:
    return {
        "id": "mine",
        "name": "Mine",
        "input_weight": 3,
        "output_weight": 1,
        "task_input_tokens": 30_000,
        "task_output_tokens": 10_000,
        **changes,
    }


# ---- J-10 -----------------------------------------------------------------------------------------------------


def test_intervals_and_weights_must_be_finite_and_bounded():
    for field, value in (
        ("models_interval_hours", float("inf")),
        ("models_interval_hours", float("nan")),
        ("models_interval_hours", 1e12),
        ("balances_interval_minutes", 1e12),
        ("benchmarks_interval_hours", 1e12),
        ("usage_interval_hours", float("inf")),
    ):
        with pytest.raises(ValidationError):
            RefreshSettings(**{field: value})
    for field in ("intelligence_weight", "coding_weight", "agentic_weight", "own_tests_weight", "free_model_cost"):
        for value in (float("inf"), float("nan"), 1e12):
            with pytest.raises(ValidationError):
                ScoringSettings(**{field: value})
    for field in ("input_weight", "output_weight"):
        with pytest.raises(ValidationError):
            UsageProfile(**_profile(**{field: float("inf")}))


def test_the_bounds_leave_every_sensible_setting_valid():
    refresh = RefreshSettings(
        models_interval_hours=0,
        balances_interval_minutes=1,
        benchmarks_interval_hours=24 * 365,
        usage_interval_hours=0.5,
    )
    assert (refresh.models_interval_hours, refresh.benchmarks_interval_hours) == (0, 24 * 365)
    assert RefreshSettings(balances_interval_minutes=60 * 24 * 365).balances_interval_minutes == 60 * 24 * 365
    scoring = ScoringSettings(intelligence_weight=0, coding_weight=5, free_model_cost=0)
    assert scoring.coding_weight == 5


# ---- V-6 ------------------------------------------------------------------------------------------------------


def test_a_usage_profile_needs_some_input_or_output_tokens():
    with pytest.raises(ValidationError, match="some input or output tokens"):
        UsageProfile(**_profile(task_input_tokens=0, task_output_tokens=0))
    assert UsageProfile(**_profile(task_input_tokens=0)).task_output_tokens == 10_000
    assert UsageProfile(**_profile(task_output_tokens=0)).task_input_tokens == 30_000


def test_a_save_with_an_empty_usage_profile_is_refused(client):
    current = client.get("/api/settings").json()["settings"]
    scoring = {
        **current["scoring"],
        "profiles": [*current["scoring"]["profiles"], _profile(task_input_tokens=0, task_output_tokens=0)],
    }

    response = client.put("/api/settings", json={"settings": {**current, "scoring": scoring}})

    assert response.status_code == 422
    assert "some input or output tokens" in response.text


def test_a_save_with_a_normal_extra_usage_profile_is_kept(client):
    current = client.get("/api/settings").json()["settings"]
    scoring = {**current["scoring"], "profiles": [*current["scoring"]["profiles"], _profile()]}

    response = client.put("/api/settings", json={"settings": {**current, "scoring": scoring}})

    assert response.status_code == 200
    assert "mine" in [p["id"] for p in response.json()["settings"]["scoring"]["profiles"]]


# ---- stricter validation must not stop an app that already holds the old values ---------------------------------


def _store(document: dict) -> None:
    with session_scope() as s:
        row = s.get(Setting, "app")
        assert row is not None
        row.value = document


def test_a_stored_value_that_no_longer_validates_falls_back_without_losing_the_rest():
    stored = AppSettings().model_dump(mode="json")
    stored["general"]["instance_name"] = "Kept"
    stored["refresh"]["models_interval_hours"] = 1e12
    stored["scoring"]["coding_weight"] = float("inf")
    stored["scoring"]["profiles"].insert(1, _profile(task_input_tokens=0, task_output_tokens=0))
    stored["benchmarks"]["sources"]["livebench"] = {"enabled": True, "api_key_enc": "encrypted-key"}
    stored["subscriptions"]["claude_ingest_token_enc"] = "encrypted-token"
    _store(stored)

    with session_scope() as s:
        loaded = load_settings(s)

    assert loaded.general.instance_name == "Kept"
    assert loaded.refresh.models_interval_hours == RefreshSettings().models_interval_hours
    assert loaded.scoring.coding_weight == ScoringSettings().coding_weight
    assert [p.id for p in loaded.scoring.profiles] == ["agentic", "balanced", "output"]
    assert loaded.benchmarks.sources["livebench"].api_key_enc == "encrypted-key"
    assert loaded.subscriptions.claude_ingest_token_enc == "encrypted-token"


def _single_preset(**changes: object) -> dict:
    """The section as a release with one preset stored it: that preset's own fields, and nothing around them."""
    new = {"blocked_models", "blocked_providers", "adopted", "route", "sort"}
    return PresetSettings(**changes).model_dump(mode="json", exclude=new)


def _stored_presets(section: object) -> AppSettings:
    stored = AppSettings().model_dump(mode="json")
    stored["presets"] = section
    _store(stored)
    with session_scope() as s:
        return load_settings(s)


def test_a_preset_lineup_saved_under_the_old_limit_of_ten_loads_as_three_not_one():
    old = _single_preset(slug="vibonomics-keep", max_providers=2) | {"max_models": 7}

    (item,) = _stored_presets(old).presets.items

    assert (item.max_models, item.slug, item.max_providers) == (3, "vibonomics-keep", 2)


def test_the_one_preset_an_older_release_stored_becomes_the_first_item_with_every_field_kept():
    old = _single_preset(
        enabled=True,
        auto_apply=True,
        interval_hours=6,
        slug="vibonomics-mine",
        template_slug="my-tools",
        profile="balanced",
        dimension="coding",
        min_score=72,
        require_tool_call=False,
        min_context=64_000,
        max_task_cost=0.5,
        min_value=900,
        include_free=True,
        switch_margin_pct=4,
        max_models=2,
        max_providers=5,
    )

    loaded = _stored_presets(old).presets

    (item,) = loaded.items
    assert (
        item.model_dump(mode="json", exclude={"blocked_models", "blocked_providers", "adopted", "route", "sort"}) == old
    )
    assert (item.route, item.sort, item.adopted, item.blocked_models, item.blocked_providers) == (
        "fallback",
        None,
        False,
        [],
        [],
    )
    assert (loaded.blocked_models, loaded.blocked_providers) == ([], [])


def test_a_stored_preset_nobody_ever_set_up_becomes_no_item():
    assert _stored_presets(_single_preset()).presets.items == []
    # but one that was only tuned, not yet switched on, is the operator's work and is kept
    (item,) = _stored_presets(_single_preset(min_score=80)).presets.items
    assert item.min_score == 80 and item.enabled is False


def test_one_bad_value_in_one_preset_leaves_the_rest_of_it_and_the_other_presets_alone():
    good = PresetSettings(slug="vibonomics-good", min_score=70).model_dump(mode="json")
    bad = PresetSettings(slug="vibonomics-bad", max_providers=4).model_dump(mode="json") | {"min_score": 500}
    lost = PresetSettings(slug="vibonomics-lost").model_dump(mode="json") | {"interval_hours": "soon"}
    shared = {"blocked_models": ["openai/*"], "blocked_providers": ["deepinfra"]}

    loaded = _stored_presets({"items": [good, bad, lost], **shared}).presets

    by_slug = {i.slug: i for i in loaded.items}
    assert list(by_slug) == ["vibonomics-good", "vibonomics-bad", "vibonomics-lost"]
    assert by_slug["vibonomics-good"].min_score == 70
    assert (by_slug["vibonomics-bad"].min_score, by_slug["vibonomics-bad"].max_providers) == (60, 4)
    assert by_slug["vibonomics-lost"].interval_hours == 24
    assert (loaded.blocked_models, loaded.blocked_providers) == (["openai/*"], ["deepinfra"])


def test_an_item_whose_slug_is_no_good_is_dropped_not_given_the_default_name():
    good = PresetSettings(slug="vibonomics-good").model_dump(mode="json")
    bad = good | {"slug": 7}

    assert [i.slug for i in _stored_presets({"items": [good, bad]}).presets.items] == ["vibonomics-good"]


def test_two_presets_with_the_same_slug_are_refused_in_a_save_and_trimmed_when_loaded(client):
    current = client.get("/api/settings").json()["settings"]
    twice = [PresetSettings(slug="Same name").model_dump(mode="json")] * 2
    response = client.put("/api/settings", json={"settings": {**current, "presets": {"items": twice}}})
    assert response.status_code == 422
    assert "share the slug" in response.text

    # the same stored by an older bug, or by hand, must not reset the section: the first one is kept
    other = PresetSettings(slug="Other").model_dump(mode="json")
    loaded = _stored_presets({"items": [twice[0], other, PresetSettings(slug="vibonomics-same-name").model_dump()]})
    assert [i.slug for i in loaded.presets.items] == ["vibonomics-same-name", "vibonomics-other"]


def test_at_most_twenty_presets_can_be_managed(client):
    current = client.get("/api/settings").json()["settings"]

    def items(count: int) -> list[dict]:
        return [PresetSettings(slug=f"p{n}").model_dump(mode="json") for n in range(count)]

    assert (
        client.put("/api/settings", json={"settings": {**current, "presets": {"items": items(20)}}}).status_code == 200
    )
    over = client.put("/api/settings", json={"settings": {**current, "presets": {"items": items(21)}}})
    assert over.status_code == 422
    # stored beyond the limit, the first twenty load
    assert len(_stored_presets({"items": items(25)}).presets.items) == 20


def test_an_adopted_preset_keeps_its_name_as_typed_and_an_ordinary_one_gets_the_prefix():
    assert PresetSettings(slug="Team-Fast_1.0", adopted=True).slug == "Team-Fast_1.0"
    assert PresetSettings(slug="Team Fast").slug == "vibonomics-team-fast"
    for bad in ("", "has space", "-leading", "x" * 101):
        with pytest.raises(ValidationError):
            PresetSettings(slug=bad, adopted=True)


def test_blocklist_entries_are_trimmed_and_a_repeat_in_another_case_is_one_entry():
    item = PresetSettings(
        blocked_models=[" OpenAI/* ", "", "openai/*", "*:free"], blocked_providers=["DeepInfra", "deepinfra"]
    )

    assert item.blocked_models == ["OpenAI/*", "*:free"]
    assert item.blocked_providers == ["DeepInfra"]
    with pytest.raises(ValidationError):
        PresetSettings(blocked_models=["x" * 101])


def test_a_stored_document_that_is_not_settings_at_all_loads_as_the_defaults():
    _store({"refresh": 5, "scoring": "nope", "general": ["x"]})

    with session_scope() as s:
        assert load_settings(s) == AppSettings()


def test_an_infinite_weight_in_a_save_is_refused_and_nothing_is_stored(client):
    tolerant = TestClient(client.app, raise_server_exceptions=False)
    current = client.get("/api/settings").json()["settings"]
    current["scoring"]["coding_weight"] = float("inf")

    # Python's json writes (and reads) Infinity, which a browser's JSON.stringify would never send. FastAPI then
    # fails to render its own 422, which echoes the value, so the status is 500; what matters is that it is refused.
    response = tolerant.put(
        "/api/settings", content=json.dumps({"settings": current}), headers={"content-type": "application/json"}
    )

    assert response.status_code >= 400
    assert client.get("/api/settings").json()["settings"]["scoring"]["coding_weight"] == 0.35
