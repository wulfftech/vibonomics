"""GET /api/models/{id}/snippets: an OpenRouter model's config routed to its cheapest provider, or pinned to one."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from vibonomics.app import create_app
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import Model, ProviderConfig

ROUTER = "Routing test router"
OTHER = "Routing test other"
ENDPOINTS = [
    {"provider": "DeepInfra", "tag": "deepinfra/fp8", "input": 0.14, "output": 0.42, "discount": 0.3},
    {"provider": "Wafer", "tag": "wafer/us", "input": 0.045, "output": 0.5, "discount": 0.0},
]


@pytest.fixture
def client(monkeypatch) -> tuple[TestClient, dict[str, int]]:
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)

    async def no_catalog(force: bool = False) -> ModelsDevCatalog:
        return ModelsDevCatalog({})

    # The details endpoint loads the catalogue; without this the test needs models.dev.
    monkeypatch.setattr("vibonomics.api.models.load_catalog", no_catalog)
    get_config.cache_clear()
    init_db()
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name.in_([ROUTER, OTHER])))
        router = ProviderConfig(type="openrouter", name=ROUTER, enabled=True)
        other = ProviderConfig(type="custom-openai", name=OTHER, enabled=True, options={})
        session.add_all([router, other])
        session.flush()
        rows = {
            "flash": Model(
                provider_config_id=router.id,
                model_id="acme/flash",
                name="Flash",
                price_input=0.1,
                price_output=0.2,
                extra={"endpoints": ENDPOINTS},
            ),
            "free": Model(
                provider_config_id=router.id,
                model_id="acme/flash:free",
                name="Flash free",
                price_input=0.0,
                price_output=0.0,
                extra={"endpoints": ENDPOINTS},
            ),
            "custom": Model(
                provider_config_id=other.id, model_id="local-model", name="Local", price_input=0.1, price_output=0.2
            ),
        }
        session.add_all(rows.values())
        session.flush()
        ids = {name: row.id for name, row in rows.items()}
    yield TestClient(create_app()), ids
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name.in_([ROUTER, OTHER])))
    get_config.cache_clear()


def _curl_body(snippets: list[dict]) -> dict:
    curl = next(s for s in snippets if s["id"] == "curl")
    return json.loads(re.search(r"-d '(.*)'", curl["content"]).group(1))


def test_a_variant_goes_on_the_model_id(client):
    http, ids = client
    response = http.get(f"/api/models/{ids['flash']}/snippets", params={"variant": "floor"})
    assert response.status_code == 200
    assert _curl_body(response.json())["model"] == "acme/flash:floor"


def test_a_provider_is_pinned_with_no_fallbacks_unless_asked(client):
    http, ids = client
    url = f"/api/models/{ids['flash']}/snippets"
    strict = _curl_body(http.get(url, params={"provider": "deepinfra/fp8"}).json())
    assert strict["provider"] == {"order": ["deepinfra/fp8"], "allow_fallbacks": False}
    loose = _curl_body(http.get(url, params={"provider": "deepinfra/fp8", "fallbacks": "true"}).json())
    assert loose["provider"] == {"order": ["deepinfra/fp8"]}


def test_no_choice_is_the_default_routing(client):
    http, ids = client
    detail = http.get(f"/api/models/{ids['flash']}").json()
    routed = http.get(f"/api/models/{ids['flash']}/snippets").json()
    assert routed == detail["snippets"]
    assert "provider" not in _curl_body(routed)


@pytest.mark.parametrize(
    ("which", "params", "message"),
    [
        ("flash", {"provider": "nope/fp8"}, "Unknown provider"),
        ("flash", {"variant": "floor", "provider": "deepinfra/fp8"}, "not both"),
        ("flash", {"variant": "bogus"}, None),
        ("free", {"variant": "nitro"}, "already carries a variant"),
        ("custom", {"variant": "floor"}, "OpenRouter models only"),
    ],
)
def test_choices_that_make_no_sense_are_refused(client, which, params, message):
    http, ids = client
    response = http.get(f"/api/models/{ids[which]}/snippets", params=params)
    assert response.status_code == 422
    if message:
        assert message in response.json()["detail"]


def test_a_pinned_free_model_is_fine(client):
    http, ids = client
    response = http.get(f"/api/models/{ids['free']}/snippets", params={"provider": "wafer/us"})
    assert response.status_code == 200
    assert _curl_body(response.json())["model"] == "acme/flash:free"


def test_an_unknown_model_is_a_404(client):
    http, _ = client
    assert http.get("/api/models/999999999/snippets").status_code == 404
