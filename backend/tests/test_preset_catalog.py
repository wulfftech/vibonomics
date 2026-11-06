"""The preset catalogue: a local copy of every preset on the owner's OpenRouter account, kept fresh on a timer."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete

from vibonomics import app as app_module
from vibonomics import crypto, events, preset_catalog, scheduler
from vibonomics.app import create_app
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import ProviderConfig, Setting
from vibonomics.presets import STATE_KEY
from vibonomics.settings_store import AppSettings, GeneralSettings, RefreshSettings

BASE = "https://openrouter.ai/api/v1"
KEY = "sk-or-test-key-1234567890"
CATALOG_KEY = preset_catalog.CATALOG_KEY

ALPHA = {
    "model": "acme/a",
    "models": ["acme/a", "acme/b", "acme/c"],
    "route": "sort",
    "temperature": 0.2,
    "max_tokens": 100,
    "provider": {
        "only": ["deepinfra/fp8", "novita/fp4"],
        "order": ["novita/fp4", "deepinfra/fp8"],
        "sort": "price",
        "data_collection": "deny",
        "ignore": ["slow"],
    },
}


def _clean(session) -> None:
    session.execute(delete(ProviderConfig).where(ProviderConfig.type == "openrouter"))
    session.execute(delete(Setting).where(Setting.key.in_([CATALOG_KEY, STATE_KEY])))


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    monkeypatch.delenv("VIBONOMICS_OPENROUTER_API_KEY", raising=False)
    get_config.cache_clear()
    init_db()
    with session_scope() as session:
        _clean(session)
        session.add(
            ProviderConfig(type="openrouter", name="Catalogue test", enabled=True, api_key_enc=crypto.encrypt(KEY))
        )
    yield
    with session_scope() as session:
        _clean(session)
    get_config.cache_clear()


def _remove_key() -> None:
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"api_key_enc": None})


def _own(*slugs: str) -> None:
    with session_scope() as session:
        row = session.get(Setting, STATE_KEY)
        if row is None:
            session.add(Setting(key=STATE_KEY, value={"owned": list(slugs)}))
        else:
            row.value = {**row.value, "owned": list(slugs)}


def _row(slug: str, status: str = "active", **extra: Any) -> dict[str, Any]:
    return {"slug": slug, "name": f"Name of {slug}", "status": status, "updated_at": "2026-11-04T07:36:33Z", **extra}


def _detail(
    slug: str, config: dict[str, Any], version: int = 1, system_prompt: str | None = None, **data: Any
) -> dict[str, Any]:
    live = {"version": version, "config": config, "system_prompt": system_prompt}
    return {
        "data": {
            "slug": slug,
            "name": f"Name of {slug}",
            "status": "active",
            "updated_at": "2026-11-04T07:36:33Z",
            "designated_version": live,
            **data,
        }
    }


def _serve(router: respx.Router, rows: list[dict[str, Any]], details: dict[str, Any]) -> respx.Route:
    """The account: its list, and for each slug a detail body or the status code its read fails with."""
    listing = router.get(f"{BASE}/presets").mock(
        return_value=httpx.Response(200, json={"data": rows, "total_count": len(rows)})
    )
    for slug, what in details.items():
        if isinstance(what, int):
            reply = httpx.Response(what, json={"error": {"message": f"nope {KEY}"}})
        else:
            reply = httpx.Response(200, json=what)
        router.get(f"{BASE}/presets/{slug}").mock(return_value=reply)
    return listing


def _entries() -> dict[str, dict[str, Any]]:
    return {p["slug"]: p for p in preset_catalog.read_catalog()["presets"]}


# ---- what a sync stores ---------------------------------------------------------------------------------------


async def test_a_sync_stores_each_preset_with_its_models_providers_and_the_keys_it_passes_through():
    rows = [_row("alpha"), _row("beta"), _row("gamma")]
    details = {
        "alpha": _detail("alpha", ALPHA, version=4, id="pre_alpha"),
        "beta": _detail("beta", {"model": "acme/z"}, system_prompt="be brief"),
        # a model listed twice, a provider block with nothing the app owns, and a non-list `only`
        "gamma": _detail(
            "gamma", {"model": "acme/g", "models": ["acme/g"], "provider": {"only": "x", "quantizations": ["fp8"]}}
        ),
    }
    with respx.mock(assert_all_called=False) as router:
        _serve(router, rows, details)
        await preset_catalog.sync()
        assert {c.request.headers["authorization"] for c in router.calls} == {f"Bearer {KEY}"}
        assert {c.request.url.host for c in router.calls} == {"openrouter.ai"}

    stored = preset_catalog.read_catalog()
    assert stored["error"] is None
    assert stored["synced_at"]
    found = _entries()
    assert set(found) == {"alpha", "beta", "gamma"}

    alpha = found["alpha"]
    assert alpha["name"] == "Name of alpha"
    assert alpha["status"] == "active"
    assert alpha["version"] == 4
    assert alpha["updated_at"] == "2026-11-04T07:36:33Z"
    assert alpha["models"] == ["acme/a", "acme/b", "acme/c"]
    assert alpha["providers"] == ["deepinfra/fp8", "novita/fp4"]
    assert alpha["config"] == ALPHA
    assert alpha["has_system_prompt"] is False
    assert alpha["passthrough_keys"] == ["max_tokens", "provider.data_collection", "provider.ignore", "temperature"]
    assert "managed" not in alpha  # worked out when it is read

    beta = found["beta"]
    assert (beta["models"], beta["providers"], beta["passthrough_keys"]) == (["acme/z"], [], [])
    assert beta["has_system_prompt"] is True

    gamma = found["gamma"]
    assert gamma["models"] == ["acme/g"]
    assert gamma["providers"] == []
    assert gamma["passthrough_keys"] == ["provider.quantizations"]


async def test_archived_presets_are_listed_with_their_status():
    rows = [_row("live"), _row("gone", "archived")]
    details = {
        "live": _detail("live", {"model": "acme/a"}),
        "gone": _detail("gone", {"model": "acme/a"}, status="archived"),
    }
    with respx.mock(assert_all_called=False) as router:
        _serve(router, rows, details)
        await preset_catalog.sync()

    found = _entries()
    assert {s: p["status"] for s, p in found.items()} == {"live": "active", "gone": "archived"}


async def test_the_list_row_fills_in_what_the_detail_leaves_out():
    detail = _detail("bare", {"model": "acme/a"})
    for key in ("name", "status", "updated_at"):
        del detail["data"][key]
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("bare", "archived")], {"bare": detail})
        await preset_catalog.sync()

    bare = _entries()["bare"]
    assert (bare["name"], bare["status"], bare["updated_at"]) == ("Name of bare", "archived", "2026-11-04T07:36:33Z")


async def test_the_openrouter_id_is_carried_as_preset_id_and_is_null_when_there_is_none():
    rows = [_row("detail-id"), _row("row-id", id=42), _row("neither"), _row("both", id="from-the-row")]
    details = {
        "detail-id": _detail("detail-id", {"model": "acme/a"}, id="pre_123"),
        "row-id": _detail("row-id", {"model": "acme/a"}),
        "neither": _detail("neither", {"model": "acme/a"}),
        "both": _detail("both", {"model": "acme/a"}, id="from-the-detail"),
    }
    with respx.mock(assert_all_called=False) as router:
        _serve(router, rows, details)
        await preset_catalog.sync()

    found = _entries()
    assert found["detail-id"]["preset_id"] == "pre_123"
    assert found["row-id"]["preset_id"] == "42"  # a string, whatever OpenRouter sends
    assert found["neither"]["preset_id"] is None
    assert found["both"]["preset_id"] == "from-the-detail"


async def test_a_detail_with_no_live_version_is_stored_with_nothing_to_show_not_dropped():
    detail = {"data": {"slug": "empty", "name": "Empty", "status": "active"}}
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("empty")], {"empty": detail})
        await preset_catalog.sync()

    empty = _entries()["empty"]
    assert (empty["version"], empty["models"], empty["config"], empty["passthrough_keys"]) == (None, [], {}, [])


# ---- failures ---------------------------------------------------------------------------------------------------


async def test_a_detail_read_that_fails_keeps_the_previous_entry_and_reports_a_partial_sync():
    rows = [_row("alpha"), _row("beta")]
    with respx.mock(assert_all_called=False) as router:
        _serve(
            router, rows, {"alpha": _detail("alpha", ALPHA, version=1), "beta": _detail("beta", {"model": "acme/z"})}
        )
        await preset_catalog.sync()
    before = _entries()

    with respx.mock(assert_all_called=False) as router:
        _serve(
            router,
            [_row("alpha"), _row("beta", "archived")],
            {"alpha": _detail("alpha", ALPHA, version=2), "beta": 403},
        )
        await preset_catalog.sync()

    after = _entries()
    assert after["alpha"]["version"] == 2
    # the last copy stays, but the name and status the list just gave are newer than it
    assert after["beta"] == {**before["beta"], "status": "archived"}
    error = preset_catalog.read_catalog()["error"]
    assert "partial" in error
    assert "beta" in error
    assert "1 of 2" in error
    assert KEY not in error


async def test_a_preset_that_has_never_been_read_gets_a_minimal_entry_from_the_list_row():
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("new", id="pre_new")], {"new": 403})
        await preset_catalog.sync()

    assert _entries()["new"] == {
        "slug": "new",
        "preset_id": "pre_new",
        "name": "Name of new",
        "status": "active",
        "version": None,
        "updated_at": "2026-11-04T07:36:33Z",
        "models": [],
        "providers": [],
        "config": {},
        "has_system_prompt": False,
        "passthrough_keys": [],
    }
    assert "partial" in preset_catalog.read_catalog()["error"]


async def test_a_list_that_fails_keeps_the_catalogue_and_the_key_stays_out_of_the_error():
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        await preset_catalog.sync()
    before = preset_catalog.read_catalog()

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/presets").mock(return_value=httpx.Response(403, json={"error": f"forbidden for {KEY}"}))
        await preset_catalog.sync()

    after = preset_catalog.read_catalog()
    assert after["presets"] == before["presets"]
    assert after["synced_at"] == before["synced_at"]  # nothing was synced
    assert "403" in after["error"]
    assert KEY not in after["error"]


async def test_an_answer_that_is_not_a_list_of_presets_is_an_error_not_an_empty_account():
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        await preset_catalog.sync()
        router.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={"data": {"oops": True}}))
        await preset_catalog.sync()

    assert set(_entries()) == {"alpha"}
    assert preset_catalog.read_catalog()["error"]


async def test_with_no_key_the_error_is_stored_and_nothing_is_requested():
    _remove_key()
    with respx.mock(assert_all_called=False) as router:
        await preset_catalog.sync()
        assert router.calls.call_count == 0

    stored = preset_catalog.read_catalog()
    assert stored["error"] == "Add an OpenRouter API key under Settings, Providers first."
    assert stored["presets"] == []


async def test_losing_the_key_keeps_what_was_synced_and_says_why_it_is_stale(monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        await preset_catalog.sync()
    before = preset_catalog.read_catalog()
    _remove_key()
    told: list[Any] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: told.append(name))

    with respx.mock(assert_all_called=False) as router:
        await preset_catalog.sync()
        await preset_catalog.sync()  # the same answer again is not news
        assert router.calls.call_count == 0

    after = preset_catalog.read_catalog()
    assert after["presets"] == before["presets"]
    assert after["synced_at"] == before["synced_at"]
    assert "API key" in after["error"]
    assert told == ["presets.changed"]


async def test_a_gateway_override_on_the_provider_is_not_where_the_key_goes():
    with session_scope() as session:
        session.query(ProviderConfig).filter_by(type="openrouter").update({"base_url": "https://gateway.example/v1"})
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        await preset_catalog.sync()
        assert {c.request.url.host for c in router.calls} == {"openrouter.ai"}


# ---- limits and single flight ---------------------------------------------------------------------------------------


async def test_it_reads_at_most_a_hundred_presets_six_at_a_time():
    rows = [_row(f"p{i:03}") for i in range(130)]
    active = peak = 0

    async def detail(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        slug = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json=_detail(slug, {"model": "acme/a"}))

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={"data": rows, "total_count": 130}))
        reads = router.get(re.compile(rf"{re.escape(BASE)}/presets/p\d+$")).mock(side_effect=detail)
        await preset_catalog.sync()
        assert reads.call_count == 100

    assert 1 < peak <= 6
    assert len(_entries()) == 100
    error = preset_catalog.read_catalog()["error"]
    assert "100" in error and "130" in error


async def test_a_slug_listed_twice_is_read_once_and_a_list_shorter_than_the_account_is_said_so():
    with respx.mock(assert_all_called=False) as router:
        listing = _serve(router, [_row("alpha"), _row("alpha"), _row("beta")], {"alpha": _detail("alpha", ALPHA)})
        listing.mock(
            return_value=httpx.Response(
                200, json={"data": [_row("alpha"), _row("alpha"), _row("beta")], "total_count": 9}
            )
        )
        router.get(f"{BASE}/presets/beta").mock(return_value=httpx.Response(200, json=_detail("beta", {"model": "m"})))
        await preset_catalog.sync()
        assert router.calls.call_count == 3  # the list, alpha, beta

    assert set(_entries()) == {"alpha", "beta"}
    assert "9 presets" in preset_catalog.read_catalog()["error"]


async def test_five_syncs_at_once_make_one_set_of_requests():
    rows = [_row("alpha"), _row("beta")]
    gate = asyncio.Event()

    async def listing(request: httpx.Request) -> httpx.Response:
        await gate.wait()  # holds the first run open while the others arrive
        return httpx.Response(200, json={"data": rows})

    with respx.mock(assert_all_called=False) as router:
        listed = router.get(f"{BASE}/presets").mock(side_effect=listing)
        alpha = router.get(f"{BASE}/presets/alpha").mock(return_value=httpx.Response(200, json=_detail("alpha", ALPHA)))
        beta = router.get(f"{BASE}/presets/beta").mock(
            return_value=httpx.Response(200, json=_detail("beta", {"model": "acme/z"}))
        )
        runs = [asyncio.create_task(preset_catalog.sync()) for _ in range(5)]
        await asyncio.sleep(0.05)
        assert preset_catalog.is_syncing()
        gate.set()
        results = await asyncio.gather(*runs)
        assert (listed.call_count, alpha.call_count, beta.call_count) == (1, 1, 1)

    assert all(r == results[0] for r in results)
    assert not preset_catalog.is_syncing()
    assert set(_entries()) == {"alpha", "beta"}


async def test_a_sync_that_fails_outright_still_clears_the_syncing_flag(monkeypatch):
    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(preset_catalog, "load_access", broken)
    with pytest.raises(RuntimeError):
        await preset_catalog.sync()
    assert not preset_catalog.is_syncing()


async def test_a_finished_sync_announces_it_once(monkeypatch):
    told: list[tuple[str, Any]] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: told.append((name, data)))
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        await asyncio.gather(preset_catalog.sync(), preset_catalog.sync())

    assert [name for name, _data in told] == ["presets.changed"]
    # the event goes out after the catalogue is stored, because the client refetches when it arrives
    assert set(_entries()) == {"alpha"}


async def test_the_event_waits_for_the_stored_catalogue(monkeypatch):
    seen: list[set[str]] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: seen.append(set(_entries())))
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        await preset_catalog.sync()

    assert seen == [{"alpha"}]


# ---- the routes -----------------------------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_before_the_first_sync_the_catalogue_is_empty(client):
    response = client.get("/api/presets/catalog")
    assert response.status_code == 200
    assert response.json() == {"synced_at": None, "syncing": False, "error": None, "presets": []}


def test_the_sync_route_runs_a_sync_and_returns_the_catalogue(client):
    _own("alpha")
    with respx.mock(assert_all_called=False) as router:
        _serve(
            router,
            [_row("alpha", id="pre_a"), _row("beta")],
            {"alpha": _detail("alpha", ALPHA, version=3), "beta": _detail("beta", {"model": "acme/z"})},
        )
        response = client.post("/api/presets/sync")
        assert response.status_code == 200, response.text
        assert KEY not in response.text
        assert router.calls.call_count == 3

    body = response.json()
    assert body["syncing"] is False
    assert body["error"] is None
    assert body["synced_at"]
    by_slug = {p["slug"]: p for p in body["presets"]}
    assert set(by_slug) == {"alpha", "beta"}
    assert by_slug["alpha"]["managed"] is True
    assert by_slug["beta"]["managed"] is False
    assert by_slug["alpha"]["preset_id"] == "pre_a"
    assert by_slug["beta"]["preset_id"] is None
    assert by_slug["alpha"]["passthrough_keys"] == [
        "max_tokens",
        "provider.data_collection",
        "provider.ignore",
        "temperature",
    ]
    assert set(by_slug["alpha"]) == {
        "slug",
        "preset_id",
        "name",
        "status",
        "version",
        "updated_at",
        "models",
        "providers",
        "config",
        "has_system_prompt",
        "passthrough_keys",
        "managed",
    }
    assert client.get("/api/presets/catalog").json() == body


def test_managed_follows_what_the_app_owns_at_the_moment_of_reading(client):
    with respx.mock(assert_all_called=False) as router:
        _serve(router, [_row("alpha")], {"alpha": _detail("alpha", ALPHA)})
        client.post("/api/presets/sync")
    assert client.get("/api/presets/catalog").json()["presets"][0]["managed"] is False

    _own("alpha")  # no new sync: the catalogue is the same and only the state moved
    assert client.get("/api/presets/catalog").json()["presets"][0]["managed"] is True


def test_the_sync_route_reports_a_missing_key_in_the_body_not_as_a_failure(client):
    _remove_key()
    with respx.mock(assert_all_called=False) as router:
        response = client.post("/api/presets/sync")
        assert router.calls.call_count == 0
    assert response.status_code == 200
    assert response.json()["error"] == "Add an OpenRouter API key under Settings, Providers first."


def test_the_catalogue_says_when_a_sync_is_running(client):
    async def probe() -> bool:
        gate = asyncio.Event()

        async def listing(request: httpx.Request) -> httpx.Response:
            await gate.wait()
            return httpx.Response(200, json={"data": []})

        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE}/presets").mock(side_effect=listing)
            task = asyncio.create_task(preset_catalog.sync())
            await asyncio.sleep(0.05)
            running = preset_catalog.view()["syncing"]
            gate.set()
            await task
        return running

    assert asyncio.run(probe()) is True
    assert client.get("/api/presets/catalog").json()["syncing"] is False


# ---- the timer and the start-up run -------------------------------------------------------------------------------


@pytest.fixture
async def running():
    scheduler.start(AppSettings())
    yield
    scheduler.shutdown()


def _timers() -> dict[str, Any]:
    assert scheduler._scheduler is not None
    return {job.id: job.next_run_time for job in scheduler._scheduler.get_jobs()}


async def test_there_is_a_catalogue_job_every_thirty_minutes(running):
    job = scheduler._scheduler.get_job("preset-catalog-sync")  # type: ignore[union-attr]
    assert job is not None
    assert job.trigger.interval == timedelta(minutes=30)


async def test_a_settings_save_that_changes_nothing_leaves_the_catalogue_timer_alone(running):
    before = _timers()["preset-catalog-sync"]

    for settings in (
        AppSettings(),
        AppSettings(general=GeneralSettings(instance_name="Renamed")),
        AppSettings(refresh=RefreshSettings(models_interval_hours=3)),
    ):
        await asyncio.sleep(0.05)  # a restarted timer would start from a later "now"
        scheduler.reschedule(settings)
        assert _timers()["preset-catalog-sync"] == before

    await asyncio.sleep(0.05)
    scheduler.start(AppSettings())
    assert _timers()["preset-catalog-sync"] == before


async def test_the_job_runs_the_catalogue_sync(running, monkeypatch):
    calls: list[str] = []

    async def fake_sync() -> dict:
        calls.append("sync")
        return {}

    monkeypatch.setattr(preset_catalog, "sync", fake_sync)
    job = scheduler._scheduler.get_job("preset-catalog-sync")  # type: ignore[union-attr]
    await job.func(*job.args)

    assert calls == ["sync"]


async def test_a_failing_catalogue_job_does_not_raise_out_of_the_scheduler(monkeypatch):
    async def broken() -> dict:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(preset_catalog, "sync", broken)

    await scheduler.sync_catalogue()


async def test_the_job_without_a_key_makes_no_request():
    _remove_key()
    with respx.mock(assert_all_called=False) as router:
        await scheduler.sync_catalogue()
        assert router.calls.call_count == 0


async def test_startup_syncs_the_catalogue_after_everything_else(monkeypatch):
    order: list[object] = []

    async def fake_run_job(kind: str, trigger: str = "manual", provider_ids=None) -> None:
        order.append(kind)

    async def fake_catch_up(settings: AppSettings) -> None:
        order.append("preset")

    async def fake_sync() -> dict:
        order.append("catalogue")
        return {}

    monkeypatch.setattr(app_module, "run_job", fake_run_job)
    monkeypatch.setattr(scheduler, "catch_up_preset", fake_catch_up)
    monkeypatch.setattr(preset_catalog, "sync", fake_sync)

    await app_module._startup_work(AppSettings(), delay=0)
    assert order == [*app_module.KINDS, "preset", "catalogue"]

    order.clear()
    await app_module._startup_work(AppSettings(refresh=RefreshSettings(refresh_on_startup=False)), delay=0)
    assert order == ["preset", "catalogue"]


async def test_a_catalogue_sync_that_raises_at_startup_does_not_escape(monkeypatch):
    async def fake_run_job(kind: str, trigger: str = "manual", provider_ids=None) -> None:
        return None

    async def fake_catch_up(settings: AppSettings) -> None:
        return None

    async def broken() -> dict:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app_module, "run_job", fake_run_job)
    monkeypatch.setattr(scheduler, "catch_up_preset", fake_catch_up)
    monkeypatch.setattr(preset_catalog, "sync", broken)

    await app_module._startup_work(AppSettings(), delay=0)
