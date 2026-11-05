"""Loading the models.dev catalogue: a bad answer must not replace the good copy, and no answer must not be a crash."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from vibonomics import http
from vibonomics.catalog import modelsdev
from vibonomics.http import FetchError

GOOD = {
    "acme": {
        "id": "acme",
        "models": {"acme-1": {"id": "acme-1", "name": "Acme One", "canonical_model_id": "acme/acme-1"}},
    }
}


async def _no_wait(_seconds: float) -> None:
    return None


@pytest.fixture(autouse=True)
async def isolated(monkeypatch, tmp_path):
    """A cache directory of its own, no catalogue in memory, a client per test and no retry sleeps."""
    monkeypatch.setattr(modelsdev, "get_config", lambda: SimpleNamespace(cache_dir=tmp_path))
    monkeypatch.setattr(modelsdev, "_cache", None)
    monkeypatch.setattr(http, "_client", None)
    monkeypatch.setattr(http.asyncio, "sleep", _no_wait)
    yield
    await http.close_client()


def _cache_file(tmp_path):
    return tmp_path / "models.dev.json"


@respx.mock
async def test_a_good_answer_is_used_and_kept(tmp_path):
    respx.get(modelsdev.URL).mock(return_value=httpx.Response(200, json=GOOD))

    catalog = await modelsdev.load_catalog()

    assert catalog.degraded is None
    assert catalog.model("acme", "acme-1")["name"] == "Acme One"
    assert json.loads(_cache_file(tmp_path).read_text(encoding="utf-8")) == GOOD


@respx.mock
@pytest.mark.parametrize(
    "answer",
    [
        {"error": "rate limited"},
        {"status": 503, "message": "try later"},
        [],
        {},
        "oops",
        {"acme": {"id": "acme"}},
    ],
    ids=["error-object", "status-object", "list", "empty", "string", "provider-without-models"],
)
async def test_a_200_that_is_not_a_catalogue_does_not_replace_the_good_copy(tmp_path, answer):
    _cache_file(tmp_path).write_text(json.dumps(GOOD), encoding="utf-8")
    respx.get(modelsdev.URL).mock(return_value=httpx.Response(200, json=answer))

    catalog = await modelsdev.load_catalog()

    assert catalog.degraded is None
    assert catalog.model("acme", "acme-1") is not None
    assert json.loads(_cache_file(tmp_path).read_text(encoding="utf-8")) == GOOD


@respx.mock
async def test_a_bad_answer_with_no_copy_to_fall_back_on_is_not_cached(tmp_path):
    respx.get(modelsdev.URL).mock(return_value=httpx.Response(200, json={"error": "rate limited"}))

    catalog = await modelsdev.load_catalog()

    assert catalog.degraded is not None
    assert not _cache_file(tmp_path).exists()


@respx.mock
async def test_loads_that_overlap_share_one_fetch_and_one_catalogue():
    release = asyncio.Event()

    async def held(_request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=GOOD)

    route = respx.get(modelsdev.URL).mock(side_effect=held)
    callers = [asyncio.create_task(modelsdev.load_catalog()) for _ in range(4)]
    await asyncio.wait(callers, timeout=0.05)  # asyncio.sleep is stubbed out here, so it cannot yield

    release.set()
    catalogues = await asyncio.gather(*callers)

    assert route.call_count == 1
    assert all(c is catalogues[0] for c in catalogues)
    assert catalogues[0].degraded is None


@respx.mock
async def test_a_copy_poisoned_by_an_earlier_version_is_not_a_fallback(tmp_path):
    _cache_file(tmp_path).write_text(json.dumps({"error": "rate limited"}), encoding="utf-8")
    respx.get(modelsdev.URL).mock(side_effect=httpx.ConnectError("no route"))

    catalog = await modelsdev.load_catalog()

    assert catalog.degraded is not None
    assert catalog.data == {}


@respx.mock
async def test_a_copy_that_is_not_json_is_not_a_fallback(tmp_path):
    _cache_file(tmp_path).write_text("{not json", encoding="utf-8")
    respx.get(modelsdev.URL).mock(side_effect=httpx.ConnectError("no route"))

    catalog = await modelsdev.load_catalog()

    assert catalog.degraded is not None


@respx.mock
async def test_unreachable_with_no_copy_gives_an_empty_catalogue_that_says_why():
    respx.get(modelsdev.URL).mock(side_effect=httpx.ConnectError("no route"))

    catalog = await modelsdev.load_catalog()

    assert catalog.data == {}
    assert "models.dev" in catalog.degraded or "no route" in catalog.degraded
    assert catalog.provider("acme") is None
    assert catalog.model("acme", "acme-1") is None
    assert catalog.canonical_for("acme", "acme-1") is None
    assert catalog.canonical_index() == {}
    assert catalog.guess_canonical("acme-1") is None


@respx.mock
async def test_an_empty_catalogue_refuses_to_list_models_rather_than_list_none():
    """Adapters build their model list from models(); an empty list would read as 'the provider has no models'."""
    respx.get(modelsdev.URL).mock(side_effect=httpx.ConnectError("no route"))
    catalog = await modelsdev.load_catalog()

    with pytest.raises(FetchError, match=r"models\.dev"):
        catalog.models("acme")


@respx.mock
async def test_the_failure_is_not_retried_on_every_call_but_is_after_a_while(monkeypatch):
    route = respx.get(modelsdev.URL).mock(side_effect=httpx.ConnectError("no route"))
    clock = [1000.0]
    monkeypatch.setattr(modelsdev.time, "monotonic", lambda: clock[0])

    first = await modelsdev.load_catalog()
    again = await modelsdev.load_catalog()
    assert again is first
    calls = route.call_count

    clock[0] += modelsdev._RETRY_SECONDS + 1
    route.side_effect = None
    route.mock(return_value=httpx.Response(200, json=GOOD))
    recovered = await modelsdev.load_catalog()

    assert calls >= 1
    assert route.call_count > calls
    assert recovered.degraded is None
    assert recovered.model("acme", "acme-1") is not None


@respx.mock
async def test_a_models_refresh_goes_ahead_without_models_dev_and_loses_nothing(monkeypatch):
    """One provider needs no catalogue to list its models, another is built from it: the first is refreshed, the
    second says why it was not, and keeps the models it has instead of reading as empty."""
    from sqlalchemy import select

    from vibonomics import events, jobs
    from vibonomics.db import init_db, session_scope
    from vibonomics.orm import Model, ProviderConfig, RefreshRun

    init_db()
    monkeypatch.setattr(events, "_loop", None)
    with session_scope() as s:
        live = ProviderConfig(
            type="custom-openai", name="Offline test live", enabled=True, base_url="https://llm.example/v1"
        )
        built = ProviderConfig(type="nvidia", name="Offline test built", enabled=True)
        s.add_all([live, built])
        s.flush()
        s.add(Model(provider_config_id=built.id, model_id="kept", name="Kept", active=True))
        live_id, built_id = live.id, built.id
    respx.get(modelsdev.URL).mock(side_effect=httpx.ConnectError("no route"))
    respx.get("https://llm.example/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "local-1"}]})
    )

    await jobs.run_job("models", "test", {live_id, built_id})

    with session_scope() as s:
        run = s.scalars(select(RefreshRun).where(RefreshRun.kind == "models").order_by(RefreshRun.id.desc())).first()
        assert run.status == "partial"
        assert run.stats["ok"] == 1 and run.stats["failed"] == 1
        assert "models.dev is unavailable" in run.message
        assert [m.model_id for m in s.scalars(select(Model).where(Model.provider_config_id == live_id))] == ["local-1"]
        kept = s.scalars(select(Model).where(Model.provider_config_id == built_id)).all()
        assert [(m.model_id, m.active) for m in kept] == [("kept", True)]
        for provider_id in (live_id, built_id):
            s.delete(s.get(ProviderConfig, provider_id))


@respx.mock
async def test_a_forced_reload_that_fails_keeps_the_catalogue_already_in_memory():
    route = respx.get(modelsdev.URL).mock(return_value=httpx.Response(200, json=GOOD))
    good = await modelsdev.load_catalog()
    route.mock(side_effect=httpx.ConnectError("no route"))

    # No copy on disk to fall back on (a read-only or wiped cache directory), but one in memory.
    modelsdev.get_config().cache_dir.joinpath("models.dev.json").unlink()
    again = await modelsdev.load_catalog(force=True)

    assert again is good
    assert again.degraded is None
