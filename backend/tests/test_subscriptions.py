"""Claude subscription usage normalisation and the ingest endpoint."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibonomics.api import subscriptions as api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import SubscriptionSnapshot, utcnow
from vibonomics.subscriptions import latest_snapshot, normalize_statusline, normalize_usage

STATUSLINE = {
    "session_id": "abc123",
    "version": "2.1.310",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
        "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600},
        "spend_limit": {
            "used_percentage": 62.8,
            "resets_at": 1740787200,
            "used_usd": 314.12,
            "limit_usd": 500,
            "period": "monthly",
        },
    },
}

USAGE = {
    "subscription_type": "max",
    "rate_limits_available": True,
    "rate_limits": {
        "five_hour": {"utilization": 0.25, "resets_at": "2026-10-25T18:00:00Z"},
        "seven_day": {"utilization": 0.5, "resets_at": "2026-10-30T00:00:00Z"},
        "seven_day_opus": {"utilization": 0.75, "resets_at": "2026-10-30T00:00:00Z"},
        "model_scoped": [{"display_name": "Fable", "utilization": 0.1, "resets_at": "2026-10-30T00:00:00Z"}],
        "extra_usage": {
            "is_enabled": True,
            "monthly_limit": 2000,
            "used_credits": 1250,
            "utilization": 0.625,
            "currency": "USD",
        },
    },
}


SESSIONS = {
    "cost": {
        "total_cost_usd": 0.01234,
        "total_duration_ms": 45000,
        "total_api_duration_ms": 2300,
        "total_lines_added": 156,
        "total_lines_removed": 23,
    },
    "context_window": {
        "total_input_tokens": 15500,
        "total_output_tokens": 1200,
        "context_window_size": 200000,
        "used_percentage": 8,
        "remaining_percentage": 92,
    },
    "prompt_cache": {"hit_ratio": 0.91},
}


def test_normalize_statusline():
    data = normalize_statusline(STATUSLINE)
    assert [w["key"] for w in data["windows"]] == ["five_hour", "seven_day", "spend_limit"]
    five = data["windows"][0]
    assert five["used_percentage"] == 23.5
    assert five["resets_at"].startswith("2025-02-01")
    assert data["spend_limit"]["used_usd"] == 314.12
    assert data["source"] == "claude-code"
    assert data["model"] == "Opus"
    # Older Claude Code versions send no cost or context data.
    assert data["session"] is None


def test_normalize_statusline_session_stats():
    data = normalize_statusline({**STATUSLINE, **SESSIONS})
    session = data["session"]
    assert session is not None
    assert session["cost_usd"] == 0.0123
    assert session["duration_ms"] == 45000
    assert session["lines_added"] == 156
    assert session["context_used_percentage"] == 8.0
    assert session["context_input_tokens"] == 15500
    assert session["context_size"] == 200000
    assert session["cache_hit_ratio"] == 0.91


def test_normalize_usage_credits_and_model_windows():
    data = normalize_usage(USAGE)
    keys = [w["key"] for w in data["windows"]]
    assert keys == ["five_hour", "seven_day", "seven_day_opus", "model:Fable"]
    assert data["windows"][0]["used_percentage"] == 25.0
    assert data["plan"] == "max"
    credits = data["extra_usage"]
    assert credits is not None
    assert credits["used"] == 12.5  # cents → dollars
    assert credits["limit"] == 20.0
    assert credits["currency"] == "USD"


def test_normalize_usage_without_credits():
    data = normalize_usage({"rate_limits": {"five_hour": {"utilization": 0.1, "resets_at": None}}})
    assert data["extra_usage"] is None
    assert data["windows"][0]["resets_at"] is None


def test_normalize_usage_accepts_windows_at_the_top_level():
    """Some versions of the undocumented endpoint drop the rate_limits wrapper."""
    data = normalize_usage(
        {
            "five_hour": {"utilization": 0.4, "resets_at": "2026-10-25T18:00:00Z"},
            "seven_day": {"utilization": 0.8, "resets_at": None},
        }
    )
    assert [(w["key"], w["used_percentage"]) for w in data["windows"]] == [("five_hour", 40.0), ("seven_day", 80.0)]


def test_normalize_usage_accepts_a_limits_array():
    """The shape other usage monitors read: a list of group/kind/percent entries, percent already 0-100."""
    data = normalize_usage(
        {
            "limits": [
                {"group": "session", "percent": 42, "resets_at": "2026-10-28T00:00:00Z"},
                {"group": "weekly", "kind": "weekly_all", "percent": 12.5, "resets_at": None},
                {"group": "monthly", "percent": 99},
            ]
        }
    )
    assert [(w["key"], w["used_percentage"]) for w in data["windows"]] == [("five_hour", 42.0), ("seven_day", 12.5)]


def _client() -> TestClient:
    init_db()
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_ingest_and_read_back():
    client = _client()
    config = client.get("/api/subscription/claude").json()
    assert config["ingest_token"]
    assert config["statusline_command"].startswith("sh -c")
    assert "api/subscription/ingest/claude" in config["statusline_command"]

    response = client.post(
        f"/api/subscription/ingest/claude?token={config['ingest_token']}",
        json={**STATUSLINE, **SESSIONS},
    )
    assert response.status_code == 202
    assert response.json()["windows"] == 3

    latest = client.get("/api/subscription/claude").json()["snapshot"]
    assert latest is not None
    assert [w["key"] for w in latest["windows"]] == ["five_hour", "seven_day", "spend_limit"]
    assert latest["spend_limit"]["limit_usd"] == 500
    assert latest["session"]["cost_usd"] == 0.0123
    assert latest["session"]["context_used_percentage"] == 8.0


def test_ingest_rejects_bad_token_and_payload():
    client = _client()
    client.get("/api/subscription/claude")
    assert client.post("/api/subscription/ingest/claude?token=wrong", json=STATUSLINE).status_code == 401
    config = client.get("/api/subscription/claude").json()
    response = client.post(
        f"/api/subscription/ingest/claude?token={config['ingest_token']}",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_a_request_without_the_token_is_refused_before_its_body_is_read():
    client = _client()
    big = "x" * (api.MAX_INGEST_BYTES + 1)
    # Refused as unauthorised, not as too large: the oversized body was never looked at.
    assert client.post("/api/subscription/ingest/claude?token=wrong", content=big).status_code == 401
    assert client.post("/api/subscription/ingest/claude", content=big).status_code == 401


def test_a_body_over_the_cap_is_refused_even_with_the_token_and_even_without_a_length():
    client = _client()
    token = client.get("/api/subscription/claude").json()["ingest_token"]
    url = f"/api/subscription/ingest/claude?token={token}"
    assert client.post(url, content="x" * (api.MAX_INGEST_BYTES + 1)).status_code == 413

    def chunks():  # a streamed body has no Content-Length to check up front
        for _ in range(5):
            yield b"x" * (api.MAX_INGEST_BYTES // 4)

    assert client.post(url, content=chunks()).status_code == 413


def test_clear_snapshot():
    client = _client()
    config = client.get("/api/subscription/claude").json()
    client.post(f"/api/subscription/ingest/claude?token={config['ingest_token']}", json=STATUSLINE)
    assert client.get("/api/subscription/claude").json()["snapshot"] is not None
    assert client.delete("/api/subscription/claude").status_code == 204
    assert client.get("/api/subscription/claude").json()["snapshot"] is None


def test_saving_settings_keeps_the_ingest_token():
    from vibonomics.api import settings as settings_api

    init_db()
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(settings_api.router)
    client = TestClient(app)
    token = client.get("/api/subscription/claude").json()["ingest_token"]

    settings = client.get("/api/settings").json()["settings"]
    assert settings["subscriptions"]["claude_ingest_token_enc"] is None
    # What the Settings page sends: its own sections, nothing about subscriptions.
    body = {key: settings[key] for key in ("general", "refresh", "highlight", "scoring", "benchmarks", "evals")}
    assert client.put("/api/settings", json={"settings": body}).status_code == 200
    # A stale or forged value in the body is ignored as well.
    body["subscriptions"] = {"claude_ingest_token_enc": "stale"}
    assert client.put("/api/settings", json={"settings": body}).status_code == 200

    assert client.get("/api/subscription/claude").json()["ingest_token"] == token
    assert client.post(f"/api/subscription/ingest/claude?token={token}", json=STATUSLINE).status_code == 202


def test_spend_limit_without_a_percentage():
    limits = {"five_hour": {"used_percentage": 10}, "spend_limit": {"used_usd": 12.5, "limit_usd": 100}}
    data = normalize_statusline({"rate_limits": limits})
    assert data["spend_limit"]["used_percentage"] == 12.5
    assert data["windows"][-1] == {
        "key": "spend_limit",
        "label": "Gateway spend limit",
        "used_percentage": 12.5,
        "resets_at": None,
    }

    # Nothing to derive it from: keep the spend block, but no window without a percentage.
    data = normalize_statusline({"rate_limits": {"spend_limit": {"used_usd": 12.5}}})
    assert data["spend_limit"]["used_usd"] == 12.5
    assert data["windows"] == []

    client = _client()
    token = client.get("/api/subscription/claude").json()["ingest_token"]
    body = {"rate_limits": {"spend_limit": {"used_usd": 3}}}
    assert client.post(f"/api/subscription/ingest/claude?token={token}", json=body).status_code == 202
    response = client.get("/api/subscription/claude")
    assert response.status_code == 200
    assert response.json()["snapshot"]["spend_limit"]["used_usd"] == 3


@pytest.mark.parametrize("value", [0.5, 1, 1.5, 23.5, 100])
def test_status_line_percentages_are_already_percent(value):
    """Claude Code documents used_percentage as 0-100 everywhere, so 1 is one percent and never a whole window."""
    data = normalize_statusline(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": value},
                "seven_day": {"used_percentage": value},
                "spend_limit": {"used_percentage": value},
            },
            "context_window": {"used_percentage": value},
        }
    )
    assert [w["used_percentage"] for w in data["windows"]] == [float(value)] * 3
    assert data["spend_limit"]["used_percentage"] == float(value)
    assert data["session"]["context_used_percentage"] == float(value)


def test_a_status_line_that_mixes_small_and_large_percentages_agrees_with_itself():
    data = normalize_statusline(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": 1},
                "seven_day": {"used_percentage": 0.5},
                "spend_limit": {"used_percentage": 23.5},
            },
            "context_window": {"used_percentage": 1.5},
        }
    )
    assert {w["key"]: w["used_percentage"] for w in data["windows"]} == {
        "five_hour": 1.0,
        "seven_day": 0.5,
        "spend_limit": 23.5,
    }
    assert data["session"]["context_used_percentage"] == 1.5


def _post(client: TestClient, body: bytes | dict) -> object:
    token = client.get("/api/subscription/claude").json()["ingest_token"]
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return client.post(
        "/api/subscription/ingest/claude",
        content=content,
        headers={"X-Vibonomics-Token": token, "Content-Type": "application/json"},
    )


def test_ingest_keeps_model_session_and_version_as_short_strings():
    client = _client()
    body = {
        "model": {"display_name": {"x": 1}, "id": "claude-opus-5-5"},
        "version": 3,
        "session_id": "s" * 400_000,
        "rate_limits": {"five_hour": {"used_percentage": 5}},
    }
    assert _post(client, body).status_code == 202
    snapshot = client.get("/api/subscription/claude").json()["snapshot"]
    assert snapshot["model"] == "claude-opus-5-5"
    assert snapshot["version"] == "3"
    with session_scope() as session:
        stored = latest_snapshot(session, "claude")[1]
    assert len(stored["session_id"]) <= 64
    assert len(json.dumps(stored)) < 2_000


def test_ingest_drops_a_model_and_version_that_are_not_text():
    client = _client()
    body = {"model": {"display_name": {"x": 1}}, "version": ["2", 1], "session_id": {"a": 1}}
    assert _post(client, body).status_code == 202
    response = client.get("/api/subscription/claude")
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert (snapshot["model"], snapshot["version"]) == (None, None)


HUGE = "9" * 400  # a valid JSON integer too large for a float


@pytest.mark.parametrize(
    "body",
    [
        b'{"cost": {"total_duration_ms": 1e999, "total_cost_usd": 1e999, "total_lines_added": -1e999}}',
        b'{"context_window": {"used_percentage": 1e999, "context_window_size": 1e999, "total_input_tokens": 1e999}}',
        b'{"rate_limits": {"five_hour": {"used_percentage": -1e999}, "spend_limit": {"used_usd": 1e999}}}',
        f'{{"cost": {{"total_lines_added": {HUGE}, "total_cost_usd": {HUGE}}}}}'.encode(),
        f'{{"rate_limits": {{"five_hour": {{"used_percentage": 5, "resets_at": {HUGE}}}}}}}'.encode(),
        '{"rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": "²"}}}'.encode(),
    ],
    ids=["duration", "context", "windows", "big-ints", "big-reset", "odd-digit"],
)
def test_ingest_ignores_numbers_that_are_not_finite_or_too_big(body):
    client = _client()
    assert _post(client, body).status_code == 202
    response = client.get("/api/subscription/claude")
    assert response.status_code == 200
    session = response.json()["snapshot"]["session"]
    assert session is None or all(value in (None, 0.0) for value in session.values())


@pytest.mark.parametrize(
    "body",
    [b"[" * 100_000, b'{"a":' * 100_000, b'{"a": ' + b"9" * 5000 + b"}"],
    ids=["deep-list", "deep-object", "5000-digit-int"],
)
def test_ingest_answers_400_for_json_it_cannot_parse(body):
    client = _client()
    assert _post(client, body).status_code == 400


def test_the_read_survives_a_snapshot_stored_before_ingest_cleaned_it():
    client = _client()
    bad = {
        "windows": [{"key": "five_hour", "label": "5-hour session", "used_percentage": 5.0, "resets_at": None}],
        "model": {"display_name": {"x": 1}},
        "version": 3,
        "session": {"cost_usd": "lots"},
        "source": "claude-code",
    }
    with session_scope() as session:
        session.add(SubscriptionSnapshot(kind="claude", received_at=utcnow(), payload=bad))
    try:
        response = client.get("/api/subscription/claude")
        assert response.status_code == 200
        assert response.json()["ingest_token"]
    finally:
        with session_scope() as session:
            for row in session.query(SubscriptionSnapshot).filter(SubscriptionSnapshot.payload == bad):
                session.delete(row)


def test_a_token_with_characters_outside_ascii_is_a_401_not_a_500():
    client = _client()
    client.get("/api/subscription/claude")  # make sure a token exists, so the comparison runs
    for response in (
        client.post("/api/subscription/ingest/claude?token=%C3%A9", json={}),
        # Header values reach the app as latin-1 text, so a raw byte such as \xe9 is a non-ASCII str there.
        client.post("/api/subscription/ingest/claude", json={}, headers={"X-Vibonomics-Token": b"\xe9"}),
        client.post("/api/subscription/ingest/claude", json={}, headers={"X-Vibonomics-Token": "café".encode()}),
    ):
        assert response.status_code == 401
        assert response.json() == {"detail": "Bad subscription ingest token"}
