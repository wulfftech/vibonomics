"""Claude subscription usage normalisation and the ingest endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibonomics.api import subscriptions as api
from vibonomics.db import init_db
from vibonomics.subscriptions import normalize_statusline, normalize_usage

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


def test_normalize_statusline():
    data = normalize_statusline(STATUSLINE)
    assert [w["key"] for w in data["windows"]] == ["five_hour", "seven_day", "spend_limit"]
    five = data["windows"][0]
    assert five["used_percentage"] == 23.5
    assert five["resets_at"].startswith("2025-02-01")
    assert data["spend_limit"]["used_usd"] == 314.12
    assert data["source"] == "claude-code"
    assert data["model"] == "Opus"


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
        json=STATUSLINE,
    )
    assert response.status_code == 202
    assert response.json()["windows"] == 3

    latest = client.get("/api/subscription/claude").json()["snapshot"]
    assert latest is not None
    assert [w["key"] for w in latest["windows"]] == ["five_hour", "seven_day", "spend_limit"]
    assert latest["spend_limit"]["limit_usd"] == 500


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


def test_clear_snapshot():
    client = _client()
    config = client.get("/api/subscription/claude").json()
    client.post(f"/api/subscription/ingest/claude?token={config['ingest_token']}", json=STATUSLINE)
    assert client.get("/api/subscription/claude").json()["snapshot"] is not None
    assert client.delete("/api/subscription/claude").status_code == 204
    assert client.get("/api/subscription/claude").json()["snapshot"] is None
