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


def test_a_preset_lineup_saved_under_the_old_limit_of_ten_loads_as_three_not_one():
    stored = AppSettings().model_dump(mode="json")
    stored["presets"].update({"max_models": 7, "slug": "vibonomics-keep", "max_providers": 2})
    _store(stored)

    with session_scope() as s:
        loaded = load_settings(s)

    assert (loaded.presets.max_models, loaded.presets.slug, loaded.presets.max_providers) == (3, "vibonomics-keep", 2)


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
