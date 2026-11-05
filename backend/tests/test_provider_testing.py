"""Provider testing: unsaved drafts and per-field overrides."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibonomics.api import providers as api
from vibonomics.db import init_db, session_scope
from vibonomics.orm import ProviderConfig
from vibonomics.providers.registry import ADAPTERS


@pytest.fixture(scope="module", autouse=True)
def _database():
    init_db()


def _client(monkeypatch) -> TestClient:
    async def fake_run(resolved, settings):
        return api.TestResult(
            ok=True,
            message=f"{resolved.api_key}|{resolved.mgmt_key}|{resolved.extra_key}|{resolved.base_url}",
        )

    monkeypatch.setattr(api, "_run_test", fake_run)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _seed() -> int:
    with session_scope() as session:
        row = ProviderConfig(
            type="openrouter",
            name="Draft test provider",
            enabled=True,
            api_key_enc=api.crypto.encrypt("stored-key"),
            mgmt_key_enc=None,
            extra_key_enc=None,
            base_url=None,
            options={},
        )
        session.add(row)
        session.flush()
        return row.id


def test_draft_provider_test_uses_typed_values(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/api/providers/test",
        json={
            "type": "anthropic",
            "name": "Draft",
            "base_url": "https://example.test",
            "api_key": "sk-ant-new",
            "extra_key": "oauth-token",
            "options": {},
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True
    assert "oauth-token" in result["message"]
    assert "sk-ant-new" in result["message"]
    assert "https://example.test" in result["message"]


def test_saved_provider_test_honours_overrides(monkeypatch):
    provider_id = _seed()
    client = _client(monkeypatch)

    saved = client.post(f"/api/providers/{provider_id}/test")
    assert saved.json()["message"].split("|")[0] == "stored-key"

    typed = client.post(f"/api/providers/{provider_id}/test", json={"api_key": "typed-key"})
    assert typed.json()["message"].split("|")[0] == "typed-key"

    cleared = client.post(f"/api/providers/{provider_id}/test", json={"api_key": ""})
    assert cleared.json()["message"].split("|")[0] == "None"


def test_a_saved_key_is_not_sent_to_a_different_host(monkeypatch):
    provider_id = _seed()
    client = _client(monkeypatch)

    elsewhere = client.post(f"/api/providers/{provider_id}/test", json={"base_url": "https://attacker.example/v1"})
    message = elsewhere.json()["message"]
    key, _mgmt, _extra, url = message.split(" The saved key")[0].split("|")
    assert (key, url) == ("None", "https://attacker.example/v1")
    assert "The saved key was not sent to a different host" in message

    # With a key typed into the same request, it is the user's to send wherever they like.
    typed = client.post(
        f"/api/providers/{provider_id}/test", json={"base_url": "https://attacker.example/v1", "api_key": "typed"}
    )
    assert typed.json()["message"].split("|")[0] == "typed"
    assert "not sent" not in typed.json()["message"]


def test_a_saved_key_still_goes_to_the_same_host_under_another_path(monkeypatch):
    with session_scope() as session:
        row = ProviderConfig(
            type="custom-openai",
            name="Same host",
            enabled=True,
            api_key_enc=api.crypto.encrypt("stored-key"),
            base_url="https://llm.internal.example/v1",
            options={},
        )
        session.add(row)
        session.flush()
        provider_id = row.id
    client = _client(monkeypatch)
    same_host = client.post(f"/api/providers/{provider_id}/test", json={"base_url": "https://llm.internal.example/v2"})
    assert same_host.json()["message"].split("|")[0] == "stored-key"
    other_scheme = client.post(
        f"/api/providers/{provider_id}/test", json={"base_url": "http://llm.internal.example/v1"}
    )
    assert other_scheme.json()["message"].split("|")[0] == "None"


def test_blank_base_url_means_the_default_host_and_keeps_the_key(monkeypatch):
    provider_id = _seed()
    client = _client(monkeypatch)
    response = client.post(f"/api/providers/{provider_id}/test", json={"base_url": ""})
    assert response.json()["message"].split("|")[0] == "stored-key"


def test_draft_requires_custom_base_url(monkeypatch):
    client = _client(monkeypatch)
    response = client.post("/api/providers/test", json={"type": "custom-openai", "api_key": "x"})
    assert response.status_code == 400


def test_override_is_non_destructive():
    base = api.ResolvedProvider(
        id=1,
        type="openrouter",
        name="x",
        api_key="stored",
        mgmt_key=None,
        base_url=None,
        options={"a": "1"},
    )
    overridden = api._override(base, api.ProviderTestOverride(api_key="new", options={"b": "2"}))
    assert overridden.api_key == "new"
    assert overridden.options == {"a": "1", "b": "2"}
    # The dataclass mutates in place; the caller builds it fresh per request.


def test_refresh_targets_one_provider(monkeypatch):
    """The card's refresh button must not re-fetch every provider."""
    provider_id = _seed()
    calls: list[tuple[str, set[int] | None]] = []

    async def fake_run_job(kind, trigger="manual", provider_ids=None):
        calls.append((kind, provider_ids))

    monkeypatch.setattr(api, "run_job", fake_run_job)
    client = _client(monkeypatch)

    response = client.post(f"/api/providers/{provider_id}/refresh")
    assert response.status_code == 202
    assert calls == [("models", {provider_id}), ("balances", {provider_id})]


def test_refresh_batch_covers_every_id_in_one_run(monkeypatch):
    """A card showing two providers sends both ids once; two calls would drop one on the job lock."""
    first, second = _seed(), _seed()
    calls: list[tuple[str, set[int] | None]] = []

    async def fake_run_job(kind, trigger="manual", provider_ids=None):
        calls.append((kind, provider_ids))

    monkeypatch.setattr(api, "run_job", fake_run_job)
    client = _client(monkeypatch)

    response = client.post("/api/providers/refresh", json={"provider_ids": [first, second]})
    assert response.status_code == 202
    assert calls == [("models", {first, second}), ("balances", {first, second})]


def test_refresh_unknown_provider_is_404(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/api/providers/999999/refresh").status_code == 404
    assert client.post("/api/providers/refresh", json={"provider_ids": [999999]}).status_code == 404


def test_refresh_batch_rejects_an_empty_list(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/api/providers/refresh", json={"provider_ids": []}).status_code == 422


def test_a_key_with_a_stray_newline_never_reaches_the_message(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from vibonomics import http
    from vibonomics.catalog.modelsdev import ModelsDevCatalog

    class Ok(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def log_message(self, *args):
            pass

    async def no_wait(_seconds):
        return None

    async def empty_catalog():
        return ModelsDevCatalog({})

    monkeypatch.setattr(http.asyncio, "sleep", no_wait)
    # The shared client belongs to the event loop that first used it; each phase below runs on its own.
    monkeypatch.setattr(http, "_client", None)
    monkeypatch.setattr(api, "load_catalog", empty_catalog)
    server = HTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    secret = "sk-live-0123456789abcdef"
    try:
        # Trimmed where keys are resolved, so the request goes out and succeeds.
        app = FastAPI()
        app.include_router(api.router)
        result = TestClient(app).post(
            "/api/providers/test",
            json={"type": "custom-openai", "base_url": base_url, "api_key": f"{secret}\n"},
        )
        assert result.json()["ok"] is True, result.json()

        # And a header httpx refuses is not quoted back in the error.
        with pytest.raises(http.FetchError) as caught:
            http._client = None
            asyncio.run(http.get_json(f"{base_url}/models", headers={"Authorization": f"Bearer {secret}\r\n"}))
        assert secret not in str(caught.value)
        assert "Illegal header value" in str(caught.value)
    finally:
        server.shutdown()


# -- the draft test never uses environment keys, and never sends a key anywhere the form did not name -----------------


@pytest.fixture
def wire(monkeypatch):
    """The real ``_run_test`` and adapters, with every outbound request recorded and answered by a stub."""
    import httpx
    import respx

    from vibonomics import http
    from vibonomics.catalog.modelsdev import ModelsDevCatalog

    async def empty_catalog(force: bool = False):
        return ModelsDevCatalog({})

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(api, "load_catalog", empty_catalog)
    monkeypatch.setattr(http.asyncio, "sleep", no_wait)
    # The shared client belongs to the event loop that first used it.
    monkeypatch.setattr(http, "_client", None)
    app = FastAPI()
    app.include_router(api.router)
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, json={"data": []}))
        yield TestClient(app), router.calls


def _sent(calls) -> list[str]:
    return [f"{call.request.url}\n{dict(call.request.headers)}\n{call.request.content!r}" for call in calls]


@pytest.mark.parametrize("ptype", sorted(ADAPTERS))
def test_a_draft_test_never_sends_an_environment_key(monkeypatch, wire, ptype):
    client, calls = wire
    spec = ADAPTERS[ptype].spec
    monkeypatch.setenv(spec.env_key, "sk-test-env-api")
    monkeypatch.setenv(spec.env_mgmt_key, "sk-test-env-mgmt")
    monkeypatch.setenv(spec.env_extra_key, "sk-test-env-extra")

    response = client.post("/api/providers/test", json={"type": ptype, "base_url": "https://attacker.example/v1"})

    assert response.status_code == 200
    assert not [text for text in _sent(calls) if "sk-test-env" in text], _sent(calls)
    assert "sk-test-env" not in response.text


def test_the_key_typed_into_the_form_is_the_key_tested_even_with_an_environment_key(monkeypatch, wire):
    client, calls = wire
    monkeypatch.setenv("VIBONOMICS_CUSTOM_OPENAI_API_KEY", "sk-test-env-api")

    response = client.post(
        "/api/providers/test",
        json={"type": "custom-openai", "base_url": "https://llm.example/v1", "api_key": "  sk-test-typed \n"},
    )

    assert response.json()["ok"] is True, response.json()
    assert len(calls) == 1
    assert calls[0].request.headers["authorization"] == "Bearer sk-test-typed"
    assert calls[0].request.url.host == "llm.example"


def test_a_draft_with_no_key_sends_nothing_to_a_foreign_host(monkeypatch, wire):
    client, calls = wire
    monkeypatch.setenv("VIBONOMICS_GROQ_API_KEY", "sk-test-env-api")

    response = client.post("/api/providers/test", json={"type": "groq", "base_url": "https://attacker.example/v1"})

    assert response.json()["ok"] is True
    assert "No API key set" in response.json()["message"]
    assert len(calls) == 0


def test_a_saved_providers_environment_key_stays_home_when_the_test_names_another_host(monkeypatch, wire):
    client, calls = wire
    monkeypatch.setenv("VIBONOMICS_GROQ_API_KEY", "sk-test-env-api")
    with session_scope() as session:
        row = ProviderConfig(type="groq", name="Env-locked groq", enabled=True, options={})
        session.add(row)
        session.flush()
        provider_id = row.id

    response = client.post(f"/api/providers/{provider_id}/test", json={"base_url": "https://attacker.example/v1"})

    assert "not sent to a different host" in response.json()["message"]
    assert len(calls) == 0
