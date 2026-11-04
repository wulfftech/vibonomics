"""A free OpenCode Console model is OpenCode-client-only; the API says so, with what the docs say about its data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from vibonomics.app import create_app
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import Model, ProviderConfig
from vibonomics.providers.opencode import FREE_DATA_USE

CONSOLE = "Restricted test console"
GO = "Restricted test go"
NAMES = [CONSOLE, GO]


@pytest.fixture
def client(monkeypatch) -> tuple[TestClient, dict[str, int]]:
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)

    async def no_catalog(force: bool = False) -> ModelsDevCatalog:
        return ModelsDevCatalog({})

    monkeypatch.setattr("vibonomics.api.models.load_catalog", no_catalog)
    get_config.cache_clear()
    init_db()
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name.in_(NAMES)))
        console = ProviderConfig(type="opencode", name=CONSOLE, enabled=True)
        go = ProviderConfig(type="opencode-go", name=GO, enabled=True)
        session.add_all([console, go])
        session.flush()
        rows = {
            "restricted": Model(
                provider_config_id=console.id,
                model_id="big-pickle",
                name="Big Pickle",
                price_input=0.0,
                price_output=0.0,
                is_free=True,
                extra={"client_restricted": "opencode", "data_use": FREE_DATA_USE["big-pickle"]},
            ),
            "paid": Model(
                provider_config_id=console.id, model_id="glm-5.3", name="GLM 5.3", price_input=1.0, price_output=3.0
            ),
            "go-free": Model(
                provider_config_id=go.id,
                model_id="beam-free",
                name="Beam Free",
                price_input=0.0,
                price_output=0.0,
                is_free=True,
                extra={"data_use": FREE_DATA_USE["beam-free"]},
            ),
        }
        session.add_all(rows.values())
        session.flush()
        ids = {name: row.id for name, row in rows.items()}
    yield TestClient(create_app()), ids
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name.in_(NAMES)))
    get_config.cache_clear()


def test_the_model_list_marks_the_restricted_row_only(client):
    http, ids = client
    rows = {row["id"]: row for row in http.get("/api/models").json()["models"]}
    assert rows[ids["restricted"]]["client_restricted"] == "opencode"
    assert rows[ids["paid"]]["client_restricted"] is None
    assert rows[ids["go-free"]]["client_restricted"] is None
    # The fields the table already read are untouched.
    assert rows[ids["restricted"]]["is_free"] is True
    assert rows[ids["restricted"]]["testable"] is False


def test_the_detail_carries_the_restriction_and_the_data_use_note(client):
    http, ids = client
    detail = http.get(f"/api/models/{ids['restricted']}").json()
    assert detail["row"]["client_restricted"] == "opencode"
    assert detail["data_use"] == FREE_DATA_USE["big-pickle"]
    paid = http.get(f"/api/models/{ids['paid']}").json()
    assert paid["row"]["client_restricted"] is None
    assert paid["data_use"] is None


def test_a_go_free_model_has_a_data_note_but_no_restriction(client):
    http, ids = client
    detail = http.get(f"/api/models/{ids['go-free']}").json()
    assert detail["row"]["client_restricted"] is None
    assert detail["data_use"] == FREE_DATA_USE["beam-free"]
    assert not any("403" in (s["notes"] or "") for s in detail["snippets"])


def test_the_detail_snippets_warn_every_harness_but_opencode(client):
    http, ids = client
    snippets = {s["id"]: s for s in http.get(f"/api/models/{ids['restricted']}").json()["snippets"]}
    assert "403" not in (snippets["opencode"]["notes"] or "")
    others = [s for s in snippets.values() if s["id"] != "opencode"]
    assert others
    assert all("HTTP 403" in s["notes"] for s in others)
    plain = http.get(f"/api/models/{ids['paid']}").json()["snippets"]
    assert not any("403" in (s["notes"] or "") for s in plain)
