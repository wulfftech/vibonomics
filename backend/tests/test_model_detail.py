"""GET /api/models/{id}: the details modal needs the catalogue for a description at most, and must not stall the app."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from vibonomics.api import models as models_api
from vibonomics.app import create_app
from vibonomics.catalog.modelsdev import ModelsDevCatalog
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.http import FetchError
from vibonomics.orm import Model, ProviderConfig

PROVIDER = "Model detail test provider"
CATALOG = ModelsDevCatalog(
    {
        "acme": {
            "id": "acme",
            "models": {"acme-1": {"id": "acme-1", "name": "Acme One", "canonical_model_id": "acme/one"}},
        }
    }
)


@pytest.fixture
def seeded(monkeypatch) -> int:
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    get_config.cache_clear()
    init_db()
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name == PROVIDER))
        provider = ProviderConfig(type="custom-openai", name=PROVIDER, enabled=True, options={})
        session.add(provider)
        session.flush()
        model = Model(
            provider_config_id=provider.id,
            model_id="acme-1",
            name="Acme One",
            canonical_id="acme/one",
            price_input=1.0,
            price_output=2.0,
        )
        session.add(model)
        session.flush()
        model_id = model.id
    yield model_id
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name == PROVIDER))
    get_config.cache_clear()


def _stub_catalog(monkeypatch, result) -> None:
    async def load_catalog(force: bool = False):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(models_api, "load_catalog", load_catalog)


def test_the_details_open_without_a_description_when_the_catalogue_cannot_be_fetched(monkeypatch, seeded):
    _stub_catalog(monkeypatch, FetchError("GET https://models.dev/api.json: ConnectError: no route"))

    response = TestClient(create_app()).get(f"/api/models/{seeded}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["row"]["id"] == seeded
    assert detail["description"] is None
    assert detail["snippets"]


def test_the_catalogue_still_supplies_the_description_when_it_is_there(monkeypatch, seeded):
    _stub_catalog(monkeypatch, CATALOG)

    assert TestClient(create_app()).get(f"/api/models/{seeded}").json()["description"] == "Acme One"


def test_the_details_open_when_the_catalogue_is_the_empty_one_a_dead_network_gives(monkeypatch, seeded):
    _stub_catalog(monkeypatch, ModelsDevCatalog({}, degraded="models.dev: no route"))

    response = TestClient(create_app()).get(f"/api/models/{seeded}")

    assert response.status_code == 200
    assert response.json()["description"] is None


def test_an_unknown_model_is_still_a_404(monkeypatch, seeded):
    _stub_catalog(monkeypatch, CATALOG)

    assert TestClient(create_app()).get("/api/models/999999999").status_code == 404


def test_the_scoring_work_runs_off_the_event_loop(monkeypatch, seeded):
    """compute_scores walks every benchmark row; on the loop it would hold up every other request."""
    _stub_catalog(monkeypatch, CATALOG)
    on_the_loop: list[bool] = []
    real = models_api.compute_scores

    def watched(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_the_loop.append(False)
        else:
            on_the_loop.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(models_api, "compute_scores", watched)

    assert TestClient(create_app()).get(f"/api/models/{seeded}").status_code == 200
    assert on_the_loop == [False]


def test_other_offers_with_no_price_sort_last_not_as_the_cheapest(monkeypatch, seeded):
    _stub_catalog(monkeypatch, CATALOG)
    names = ["Other offers test A", "Other offers test B", "Other offers test C"]
    prices = [(None, None), (1.0, 2.0), (0.5, 0.5)]
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name.in_(names)))
        for name, (price_in, price_out) in zip(names, prices, strict=True):
            provider = ProviderConfig(type="custom-openai", name=name, enabled=True, options={})
            session.add(provider)
            session.flush()
            session.add(
                Model(
                    provider_config_id=provider.id,
                    model_id="acme-1",
                    name="Acme One",
                    canonical_id="acme/one",
                    price_input=price_in,
                    price_output=price_out,
                )
            )
    try:
        offers = TestClient(create_app()).get(f"/api/models/{seeded}").json()["other_offers"]
    finally:
        with session_scope() as session:
            session.execute(delete(ProviderConfig).where(ProviderConfig.name.in_(names)))
    assert [o["provider_name"] for o in offers] == ["Other offers test C", "Other offers test B", "Other offers test A"]
