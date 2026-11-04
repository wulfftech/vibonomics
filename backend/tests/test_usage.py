"""OpenRouter spend history: fetching it, keeping it day by day, and summarising cost, tokens and efficiency."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from vibonomics import crypto, events, jobs, usage
from vibonomics.app import create_app
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import ProviderConfig, RefreshRun, UsageDay, UsageKey, utcnow

BASE = "https://openrouter.ai/api/v1"
MGMT = "sk-or-mgmt-test-1234567890"


def _raw(day: str, model: str, upstream: str, usage_usd: float, req: int, prompt: int, completion: int, **more):
    """An /activity row as OpenRouter sends it, with the date as a timestamp."""
    return {
        "date": f"{day} 00:00:00",
        "model": model,
        "model_permaslug": model + "-20260101",
        "endpoint_id": f"ep-{model}-{upstream}",
        "provider_name": upstream,
        "usage": usage_usd,
        "byok_usage_inference": 0,
        "requests": req,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": more.get("reasoning", 0),
        "cached_tokens": more.get("cached", 0),
    }


AGGREGATE = [
    _raw("2026-10-30", "acme/m1", "Up1", 1.0, 10, 1_000_000, 100_000, reasoning=20_000, cached=900_000),
    _raw("2026-10-30", "acme/m2", "Up2", 0.5, 5, 200_000, 50_000),
    _raw("2026-11-01", "acme/m1", "Up1", 2.0, 20, 2_000_000, 200_000, reasoning=40_000, cached=1_800_000),
]
KEY_A = [
    _raw("2026-10-30", "acme/m1", "Up1", 0.6, 6, 600_000, 60_000, reasoning=12_000, cached=540_000),
    _raw("2026-11-01", "acme/m1", "Up1", 2.0, 20, 2_000_000, 200_000, reasoning=40_000, cached=1_800_000),
]
KEY_C = [_raw("2026-10-30", "acme/m2", "Up2", 0.3, 3, 120_000, 30_000)]


def _key(digest: str, name: str, used_days_ago: int | None, **more):
    used = None if used_days_ago is None else (utcnow() - timedelta(days=used_days_ago)).isoformat()
    return {
        "hash": digest,
        "name": name,
        "label": "sk-or-v1-abc...xyz",
        "disabled": False,
        "last_used_at": used,
        "usage": more.get("total", 10.0),
        "usage_daily": more.get("today", 0.0),
        "usage_monthly": more.get("month", 0.0),
    }


KEYS = [
    _key("hashA", "Recall", 1, today=0.25, total=2.6),
    _key("hashB", "Old key", 60),
    _key("hashC", "Continue", 3, today=0.05, total=0.3),
]


def _clean(session) -> None:
    session.execute(delete(UsageDay))
    session.execute(delete(UsageKey))
    session.execute(delete(ProviderConfig).where(ProviderConfig.type == "openrouter"))


def _provider(session, name: str = "Spend test", mgmt: bool = True) -> int:
    row = ProviderConfig(
        type="openrouter", name=name, enabled=True, mgmt_key_enc=crypto.encrypt(MGMT) if mgmt else None
    )
    session.add(row)
    session.flush()
    return row.id


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    monkeypatch.delenv("VIBONOMICS_OPENROUTER_MANAGEMENT_KEY", raising=False)
    monkeypatch.setattr(events, "_loop", None)
    get_config.cache_clear()
    init_db()
    with session_scope() as session:
        _clean(session)
    yield
    with session_scope() as session:
        _clean(session)
    get_config.cache_clear()


def _store(provider_id: int) -> None:
    account = usage.Account(
        keys=[
            usage.KeyInfo(k["hash"], k["name"], False, usage._time(k["last_used_at"]), k["usage"], k["usage_daily"], 0)
            for k in KEYS
        ],
        aggregate=usage.parse_rows(AGGREGATE),
        per_key={"hashA": usage.parse_rows(KEY_A), "hashC": usage.parse_rows(KEY_C)},
    )
    usage.persist(provider_id, account)


@pytest.fixture
def client() -> TestClient:
    with session_scope() as session:
        pid = _provider(session)
    _store(pid)
    return TestClient(create_app())


# ---- parsing and fetching ------------------------------------------------------------------------------------


def test_activity_rows_are_dated_by_day_and_endpoints_of_one_provider_are_summed():
    rows = usage.parse_rows(
        [
            _raw("2026-11-03", "acme/m1", "Up1", 1.0, 2, 100, 50, cached=40),
            {**_raw("2026-11-03", "acme/m1", "Up1", 0.5, 1, 10, 5), "endpoint_id": "second-endpoint"},
            {"date": "garbage", "model": "x"},
            {"date": "2026-11-03 00:00:00", "model": ""},
            "not a row",
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row.date, row.model, row.upstream) == ("2026-11-03", "acme/m1", "Up1")
    assert (row.requests, row.prompt_tokens, row.completion_tokens, row.cached_tokens) == (3, 110, 55, 40)
    assert row.usage == pytest.approx(1.5)
    assert usage.parse_rows(None) == []


def _mock_openrouter(router: respx.Router, *, key_pages: bool = True) -> dict[str, respx.Route]:
    def activity(request: httpx.Request) -> httpx.Response:
        digest = request.url.params.get("api_key_hash")
        data = {"hashA": KEY_A, "hashC": KEY_C}.get(digest, []) if digest else AGGREGATE
        return httpx.Response(200, json={"data": data})

    def keys(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        return httpx.Response(200, json={"data": KEYS if offset == 0 or not key_pages else []})

    return {
        "activity": router.get(f"{BASE}/activity").mock(side_effect=activity),
        "keys": router.get(f"{BASE}/keys").mock(side_effect=keys),
    }


async def test_a_fetch_reads_the_account_and_each_recently_used_key():
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_openrouter(router)
        account = await usage.fetch_account(BASE, MGMT)
        asked = [call.request.url.params.get("api_key_hash") for call in routes["activity"].calls]
        sent = routes["activity"].calls[0].request.headers["authorization"]
    assert sent == f"Bearer {MGMT}"
    assert [k.name for k in account.keys] == ["Recall", "Old key", "Continue"]
    assert sorted(account.per_key) == ["hashA", "hashC"]  # the key unused for 60 days is not asked about
    assert sorted(a for a in asked if a) == ["hashA", "hashC"] and asked.count(None) == 1
    assert sum(r.usage for r in account.aggregate) == pytest.approx(3.5)
    assert account.errors == []


async def test_a_server_that_ignores_the_offset_does_not_loop_forever():
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_openrouter(router, key_pages=False)  # always the same page
        account = await usage.fetch_account(BASE, MGMT)
        assert routes["keys"].call_count == 2
    assert len(account.keys) == 3


async def test_a_key_that_fails_is_reported_and_the_rest_are_kept():
    with respx.mock(assert_all_called=False) as router:
        _mock_openrouter(router)

        def flaky(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("api_key_hash") == "hashC":
                return httpx.Response(403, json={"error": "no"})
            data = KEY_A if request.url.params.get("api_key_hash") else AGGREGATE
            return httpx.Response(200, json={"data": data})

        router.get(f"{BASE}/activity").mock(side_effect=flaky)
        account = await usage.fetch_account(BASE, MGMT)
    assert sorted(account.per_key) == ["hashA"]
    assert len(account.errors) == 1 and "Continue" in account.errors[0]
    assert MGMT not in account.errors[0]


async def test_a_failure_to_read_the_account_itself_raises():
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/activity").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
        with pytest.raises(usage.FetchError, match="HTTP 401"):
            await usage.fetch_account(BASE, MGMT)


# ---- keeping the history -------------------------------------------------------------------------------------


def _days(session, provider_id: int, key_hash: str = "") -> dict[tuple[str, str], UsageDay]:
    rows = session.scalars(
        select(UsageDay).where(UsageDay.provider_config_id == provider_id, UsageDay.key_hash == key_hash)
    )
    return {(r.date, r.model): r for r in rows}


def test_each_fetch_replaces_the_days_it_returns_and_keeps_the_rest():
    with session_scope() as session:
        pid = _provider(session)
    _store(pid)
    revised = [_raw("2026-11-01", "acme/m1", "Up1", 2.5, 25, 2_500_000, 250_000)]  # one day, revised upward
    usage.persist(pid, usage.Account(aggregate=usage.parse_rows(revised)))
    with session_scope() as session:
        days = _days(session, pid)
        assert days[("2026-11-01", "acme/m1")].usage == pytest.approx(2.5)
        assert ("2026-10-30", "acme/m1") in days  # a day this fetch did not return stays
        assert len(days) == 3
        assert len(_days(session, pid, "hashA")) == 2  # per-key rows are a separate set


def test_the_keys_are_kept_with_their_names_and_updated_in_place():
    with session_scope() as session:
        pid = _provider(session)
    _store(pid)
    renamed = usage.KeyInfo("hashA", "Recall v2", True, None, 3.0, 0.0, 0.0)
    usage.persist(pid, usage.Account(keys=[renamed]))
    with session_scope() as session:
        keys = {k.key_hash: k for k in session.scalars(select(UsageKey))}
        assert sorted(keys) == ["hashA", "hashB", "hashC"]
        assert (keys["hashA"].name, keys["hashA"].disabled, keys["hashA"].usage_total) == ("Recall v2", True, 3.0)


# ---- the refresh job -----------------------------------------------------------------------------------------


async def test_the_job_stores_accounts_with_a_management_key_and_skips_the_rest():
    with session_scope() as session:
        withkey = _provider(session, "With key")
        _provider(session, "Without key", mgmt=False)
    with respx.mock(assert_all_called=False) as router:
        _mock_openrouter(router)
        stats = await jobs._refresh_usage(0)
    assert (stats["ok"], stats["failed"], stats["skipped"]) == (1, 0, 1)
    assert stats["rows"] == len(AGGREGATE) + len(KEY_A) + len(KEY_C)
    with session_scope() as session:
        assert len(_days(session, withkey)) == 3


async def test_a_failing_account_is_counted_and_named_and_the_key_is_not_in_the_message():
    with session_scope() as session:
        _provider(session)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/activity").mock(return_value=httpx.Response(500, text=f"boom {MGMT}"))
        stats = await jobs._refresh_usage(0)
    assert stats["failed"] == 1 and stats["ok"] == 0
    assert "Spend test" in stats["errors"][0]
    assert MGMT not in stats["errors"][0]


async def test_run_job_records_a_usage_run():
    with session_scope() as session:
        _provider(session)
    with respx.mock(assert_all_called=False) as router:
        _mock_openrouter(router)
        await jobs.run_job("usage", "manual")
    with session_scope() as session:
        run = session.scalars(select(RefreshRun).where(RefreshRun.kind == "usage")).first()
        assert run is not None and run.status == "ok"


# ---- the summary ---------------------------------------------------------------------------------------------


def _get(client: TestClient, **params) -> dict:
    response = client.get("/api/usage", params=params)
    assert response.status_code == 200, response.text
    assert MGMT not in response.text
    return response.json()


def test_totals_add_up_and_efficiency_is_cost_per_million_tokens(client):
    data = _get(client)
    totals = data["totals"]
    assert totals["cost"] == pytest.approx(3.5)
    assert totals["requests"] == 35
    assert totals["prompt_tokens"] == 3_200_000 and totals["completion_tokens"] == 350_000
    assert totals["tokens"] == 3_550_000  # prompt plus completion; reasoning is inside completion
    assert totals["reasoning_tokens"] == 60_000
    assert totals["cost_per_mtok"] == pytest.approx(3.5 / 3_550_000 * 1e6)
    assert totals["cost_per_request"] == pytest.approx(0.1)
    assert totals["cache_hit"] == pytest.approx(2_700_000 / 3_200_000)
    assert (data["since"], data["until"]) == ("2026-10-30", "2026-11-01")


def test_the_daily_series_has_no_gaps(client):
    daily = _get(client)["daily"]
    assert [d["date"] for d in daily] == ["2026-10-30", "2026-10-31", "2026-11-01"]
    assert [d["cost"] for d in daily] == [pytest.approx(1.5), 0, pytest.approx(2.0)]
    assert daily[1]["cost_per_mtok"] is None and daily[1]["requests"] == 0


def test_models_and_providers_are_ranked_by_spend(client):
    data = _get(client)
    assert [(m["name"], m["cost"]) for m in data["models"]] == [("acme/m1", pytest.approx(3.0)), ("acme/m2", 0.5)]
    assert [u["name"] for u in data["upstreams"]] == ["Up1", "Up2"]


def test_the_window_counts_back_from_the_latest_stored_day(client):
    data = _get(client, days=1)
    assert (data["since"], data["until"]) == ("2026-11-01", "2026-11-01")
    assert data["totals"]["cost"] == pytest.approx(2.0)
    assert len(data["daily"]) == 1


def test_spend_is_split_by_key_with_a_remainder_for_what_no_key_accounts_for(client):
    data = _get(client)
    by_name = {k["name"]: k for k in data["keys"]}
    assert by_name["Recall"]["cost"] == pytest.approx(2.6)
    assert by_name["Continue"]["cost"] == pytest.approx(0.3)
    assert by_name["Old key"]["cost"] == 0  # listed, but nothing in the window
    assert [k["name"] for k in data["keys"]][:2] == ["Recall", "Continue"]
    assert data["other_keys"]["cost"] == pytest.approx(0.6)
    assert data["other_keys"]["requests"] == 6


def test_one_key_on_its_own(client):
    data = _get(client, key="hashA")
    assert data["totals"]["cost"] == pytest.approx(2.6)
    assert data["totals"]["requests"] == 26
    assert data["other_keys"] is None  # one key is not a split of anything
    assert [k["name"] for k in data["keys"]][:2] == ["Recall", "Continue"]  # but the picker keeps its options
    assert [m["name"] for m in data["models"]] == ["acme/m1"]


def test_accounts_are_kept_apart_and_can_be_added_together(client):
    with session_scope() as session:
        other = _provider(session, "Second account")
        pid = session.scalars(select(ProviderConfig.id).where(ProviderConfig.name == "Spend test")).one()
    usage.persist(
        other,
        usage.Account(
            aggregate=usage.parse_rows([_raw("2026-11-01", "acme/m3", "Up3", 10.0, 100, 1_000_000, 100_000)])
        ),
    )
    both = _get(client)
    assert both["totals"]["cost"] == pytest.approx(13.5)
    assert [a["name"] for a in both["accounts"]] == ["Spend test", "Second account"]
    assert _get(client, provider_id=pid)["totals"]["cost"] == pytest.approx(3.5)
    assert _get(client, provider_id=other)["totals"]["cost"] == pytest.approx(10.0)


def test_an_account_without_a_management_key_says_so(client):
    with session_scope() as session:
        _provider(session, "Plain key", mgmt=False)
    accounts = {a["name"]: a for a in _get(client)["accounts"]}
    assert accounts["Spend test"]["has_management_key"] is True
    assert accounts["Plain key"]["has_management_key"] is False
    assert accounts["Plain key"]["last_synced"] is None
    empty = _get(client, provider_id=accounts["Plain key"]["id"])
    assert empty["totals"]["cost"] == 0 and empty["daily"] == [] and empty["since"] is None


def test_today_so_far_comes_from_the_keys_and_only_when_it_was_fetched_today(client):
    data = _get(client)
    assert data["today_usd"] == pytest.approx(0.3)
    assert data["today_as_of"] is not None
    with session_scope() as session:
        for key in session.scalars(select(UsageKey)):
            key.fetched_at = utcnow() - timedelta(days=1)
    stale = _get(client)
    assert stale["today_usd"] is None and stale["today_as_of"] is None


def test_the_models_beyond_the_top_are_folded_into_one_row(client):
    many = [_raw("2026-11-01", f"acme/model-{i:02d}", "Up1", 0.01 * (i + 1), 1, 1000, 100) for i in range(35)]
    with session_scope() as session:
        pid = session.scalars(select(ProviderConfig.id).where(ProviderConfig.name == "Spend test")).one()
    usage.persist(pid, usage.Account(aggregate=usage.parse_rows(many)))
    models = _get(client)["models"]
    assert len(models) == usage.MODELS_SHOWN + 1
    assert models[-1]["name"].startswith("Other models (")
