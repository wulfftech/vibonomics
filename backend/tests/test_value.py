"""POST /api/models/value: prices, scores and the best-value pick for the models a client names."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from vibonomics.app import create_app
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import BenchmarkScore, Model, ProviderConfig
from vibonomics.pricing import Task, effective_input_price, task_cost, value_of

PROVIDER = "Value test router"
REFRESHED = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)

# model id: (input, output, score or None, extra fields)
MODELS: dict[str, tuple[float, float, float | None, dict]] = {
    "acme/cheap-60:batch": (0.1, 0.6, 60, {}),
    "acme/mid-64:batch": (0.375, 2.25, 64, {}),
    "acme/big-90": (1.0, 5.0, 90, {"discount": 0.5, "list_input": 2.0, "list_output": 10.0}),
    "acme/weak-40": (0.02, 0.05, 40, {}),
    "acme/gone": (0.1, 0.1, 70, {"active": False}),
    "acme/unscored": (0.1, 0.1, None, {}),
    "acme/free-61:free": (0.0, 0.0, 61, {"is_free": True}),
    "acme/better-70:batch": (0.05, 0.25, 70, {}),
    "acme/pricier-70": (1.0, 1.0, 70, {}),
    "acme/direct": (1.0, 4.0, 66, {"extra": {"batch_discount": 0.5}}),
    "acme/free-95:free": (0.0, 0.0, 95, {"is_free": True, "extra": {"client_restricted": "opencode"}}),
}


def _canonical(model_id: str) -> str:
    return "value-test/" + model_id.split("/")[1].split(":")[0]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    get_config.cache_clear()
    init_db()
    canonicals = [_canonical(m) for m in MODELS]
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name == PROVIDER))
        session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id.in_(canonicals)))
        provider = ProviderConfig(type="openrouter", name=PROVIDER, enabled=True, last_refresh_at=REFRESHED)
        session.add(provider)
        session.flush()
        for model_id, (price_in, price_out, score, extra) in MODELS.items():
            fields = {"active": True, "is_free": False, **extra}
            if fields.get("discount"):
                fields["discount_since"] = datetime.now(UTC)
            session.add(
                Model(
                    provider_config_id=provider.id,
                    model_id=model_id,
                    name=model_id,
                    canonical_id=_canonical(model_id) if score is not None else None,
                    price_input=price_in,
                    price_output=price_out,
                    **fields,
                )
            )
            if score is not None:
                session.add(
                    BenchmarkScore(
                        source="epoch",
                        metric="eci",
                        dimension="intelligence",
                        source_model=f"value-test-{model_id}",
                        canonical_id=_canonical(model_id),
                        value=score,
                        percentile=score,
                    )
                )
    yield TestClient(create_app())
    with session_scope() as session:
        session.execute(delete(ProviderConfig).where(ProviderConfig.name == PROVIDER))
        session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id.in_(canonicals)))
    get_config.cache_clear()


def _ask(client: TestClient, models: list[str], **body) -> dict:
    refs = [m if isinstance(m, dict) else {"provider": PROVIDER, "model": m} for m in models]
    response = client.post("/api/models/value", json={"models": refs, **body})
    assert response.status_code == 200, response.text
    return response.json()


TASK = {"input_tokens": 30_000, "output_tokens": 10_000}


def test_ranks_the_named_models_and_picks_the_best_value(client):
    named = [
        "acme/cheap-60:batch",
        "acme/mid-64:batch",
        "acme/big-90",
        "acme/weak-40",
        "acme/gone",
        "acme/unscored",
        "acme/not-listed",
        {"provider": "ollama", "model": "qwen3.5:4b"},
    ]
    data = _ask(client, named, task=TASK, min_score=55)
    rows = data["models"]
    assert [r["model"] for r in rows] == [n if isinstance(n, str) else n["model"] for n in named]
    by = {r["model"]: r for r in rows}

    assert data["pick"]["model"] == "acme/cheap-60:batch"
    assert [(m, by[m]["rank"]) for m in ("acme/cheap-60:batch", "acme/mid-64:batch", "acme/big-90")] == [
        ("acme/cheap-60:batch", 1),
        ("acme/mid-64:batch", 2),
        ("acme/big-90", 3),
    ]
    cheap = by["acme/cheap-60:batch"]
    # 30k at $0.10 plus 10k at $0.60, per million
    assert cheap["task_cost"] == pytest.approx(0.009)
    assert cheap["value"] == pytest.approx(60 / 0.009, abs=0.1)
    assert cheap["batch"] is True and cheap["eligible"] is True
    assert cheap["prices_as_of"].startswith("2026-10-28T09:00")

    big = by["acme/big-90"]
    assert big["batch"] is False
    assert (big["discount"], big["list_price"]["input"], big["new_discount"]) == (0.5, 2.0, True)

    assert by["acme/weak-40"]["note"] == "Scores 40, under the floor of 55"
    assert by["acme/gone"]["note"] == f"No longer in {PROVIDER}'s model list"
    assert by["acme/unscored"]["note"] == "No composite score"
    assert (by["acme/not-listed"]["found"], by["acme/not-listed"]["note"]) == (False, f"Not in {PROVIDER}'s model list")
    assert (by["qwen3.5:4b"]["found"], by["qwen3.5:4b"]["note"]) == (False, "No enabled provider called ollama")
    assert not any(
        r["eligible"] for r in rows if r["model"] not in ("acme/cheap-60:batch", "acme/mid-64:batch", "acme/big-90")
    )
    assert data["reason"].startswith("acme/cheap-60:batch gives the best value of the 3 that qualify: 60 for $0.0090")
    assert "Next is acme/mid-64:batch" in data["reason"]
    assert data["task"] == {"profile": None, "input_tokens": 30_000, "output_tokens": 10_000, "cache_hit_rate": 0.0}
    assert data["alternatives"] == []


def test_without_a_floor_the_cheapest_score_wins(client):
    data = _ask(client, ["acme/cheap-60:batch", "acme/weak-40"], task=TASK)
    assert data["pick"]["model"] == "acme/weak-40"


def test_a_profile_sets_the_task_size(client):
    settings = client.get("/api/settings").json()["settings"]["scoring"]
    profile = next(p for p in settings["profiles"] if p["id"] == "balanced")
    data = _ask(client, ["acme/cheap-60:batch"], profile="balanced")
    assert data["task"] == {
        "profile": "balanced",
        "input_tokens": profile["task_input_tokens"],
        "output_tokens": profile["task_output_tokens"],
        "cache_hit_rate": profile["cache_hit_rate"],
    }
    default = _ask(client, ["acme/cheap-60:batch"])
    assert default["task"]["profile"] == settings["default_profile"]


def test_a_dimension_without_scores_picks_nothing(client):
    data = _ask(client, ["acme/cheap-60:batch", "acme/big-90"], task=TASK, dimension="coding", min_score=50)
    assert data["pick"] is None
    assert {r["note"] for r in data["models"]} == {"No coding score"}
    assert data["reason"] == "None of these models has a current price and a coding score at or above 50."


def test_a_free_model_is_costed_like_the_ui(client):
    data = _ask(client, ["acme/cheap-60:batch", "acme/free-61:free"], task=TASK)
    free = data["models"][1]
    assert (free["task_cost"], free["is_free"]) == (0.0, True)
    assumed = 40_000 * data["free_model_cost"] / 1e6
    assert free["value"] == pytest.approx(61 / assumed, rel=1e-6)
    assert data["pick"]["model"] == "acme/free-61:free"
    assert "61 for nothing" in data["reason"]


def test_an_offer_with_a_zero_price_that_is_not_flagged_free_can_win(client):
    # A subscription plan, such as the GLM Coding Plan, has no per-token price but is not marked free. Its value is
    # infinite, which the response leaves out; describing the pick must not need it.
    canonical = "value-test/plan-80"
    with session_scope() as session:
        provider = session.scalars(select(ProviderConfig).where(ProviderConfig.name == PROVIDER)).one()
        session.add(
            Model(
                provider_config_id=provider.id,
                model_id="acme/plan-80",
                name="acme/plan-80",
                canonical_id=canonical,
                price_input=0.0,
                price_output=0.0,
                active=True,
                is_free=False,
            )
        )
        session.add(
            BenchmarkScore(
                source="epoch",
                metric="eci",
                dimension="intelligence",
                source_model="value-test-acme/plan-80",
                canonical_id=canonical,
                value=80,
                percentile=80,
            )
        )
    try:
        data = _ask(client, ["acme/plan-80", "acme/cheap-60:batch"], task=TASK)
    finally:
        with session_scope() as session:
            session.execute(delete(BenchmarkScore).where(BenchmarkScore.canonical_id == canonical))
    assert data["pick"]["model"] == "acme/plan-80"
    assert (data["pick"]["task_cost"], data["pick"]["value"]) == (0.0, None)
    assert "80 for nothing" in data["reason"]


def test_alternatives_score_as_well_and_give_better_value(client):
    data = _ask(client, ["acme/cheap-60:batch", "acme/mid-64:batch"], task=TASK, min_score=55, alternatives=5)
    alts = data["alternatives"]
    # better-70 beats the pick on both counts; pricier-70 scores more but gives less value, and free offers stay out
    assert [a["model"] for a in alts] == ["acme/better-70:batch"]
    assert (alts[0]["note"], alts[0]["eligible"], alts[0]["rank"]) == ("Not in the request", False, None)


def test_alternatives_with_no_pick_are_the_best_values_over_the_floor(client):
    data = _ask(client, ["acme/weak-40"], task=TASK, min_score=65, alternatives=2)
    assert data["pick"] is None
    assert [a["model"] for a in data["alternatives"]] == ["acme/better-70:batch", "acme/pricier-70"]


def test_a_direct_offer_reports_its_batch_discount(client):
    data = _ask(client, ["acme/direct"], task=TASK)
    assert (data["models"][0]["batch"], data["models"][0]["batch_discount"]) == (False, 0.5)


def test_providers_match_by_type_as_well_as_name(client):
    data = _ask(client, [{"provider": "OpenRouter", "model": "acme/cheap-60:batch"}], task=TASK)
    assert data["models"][0]["found"] is True
    assert data["models"][0]["provider_name"] == PROVIDER


def test_a_model_only_one_client_may_call_is_not_ranked_or_picked_by_default(client):
    # The free OpenCode Console tier answers 403 to every client but OpenCode, which a program calling this is not.
    data = _ask(client, ["acme/cheap-60:batch", "acme/free-95:free"], task=TASK)
    by = {r["model"]: r for r in data["models"]}
    restricted = by["acme/free-95:free"]
    assert restricted["client_restricted"] == "opencode"
    assert (restricted["eligible"], restricted["rank"]) == (False, None)
    assert "opencode" in restricted["note"] and "403" in restricted["note"]
    assert by["acme/cheap-60:batch"]["client_restricted"] is None
    assert data["pick"]["model"] == "acme/cheap-60:batch"
    assert "free-95" not in data["reason"]


def test_a_program_can_opt_in_to_models_only_one_client_may_call(client):
    data = _ask(client, ["acme/cheap-60:batch", "acme/free-95:free"], task=TASK, include_client_restricted=True)
    assert data["pick"]["model"] == "acme/free-95:free"
    assert data["models"][1]["client_restricted"] == "opencode" and data["models"][1]["rank"] == 1


def test_a_model_named_twice_is_ranked_once_against_the_others(client):
    named = [
        "acme/cheap-60:batch",
        "acme/mid-64:batch",
        "acme/cheap-60:batch",
        {"provider": "OpenRouter", "model": "acme/cheap-60:batch"},  # the same model reached by the provider's type
    ]
    data = _ask(client, named, task=TASK)
    rows = data["models"]
    assert [r["model"] for r in rows] == ["acme/cheap-60:batch", "acme/mid-64:batch"] + ["acme/cheap-60:batch"] * 2
    assert [r["rank"] for r in rows] == [1, 2, None, None]
    assert [r["eligible"] for r in rows] == [True, True, False, False]
    assert rows[2]["note"] == "Named more than once in the request" and rows[2]["found"] is True
    assert "best value of the 2 that qualify" in data["reason"]
    assert "Next is acme/mid-64:batch" in data["reason"]


def test_a_tiny_task_never_costs_zero_while_its_value_is_finite(client):
    data = _ask(client, ["acme/cheap-60:batch"], task={"input_tokens": 1, "output_tokens": 0})
    row = data["models"][0]
    assert row["task_cost"] == pytest.approx(1e-7)
    assert row["value"] == pytest.approx(row["score"] / row["task_cost"], rel=1e-3)
    # ... and an ordinary cost keeps its figures
    assert _ask(client, ["acme/big-90"], task=TASK)["models"][0]["task_cost"] == pytest.approx(0.08)


def test_bad_requests(client):
    one = [{"provider": PROVIDER, "model": "acme/cheap-60:batch"}]
    assert client.post("/api/models/value", json={"models": []}).status_code == 422
    assert client.post("/api/models/value", json={"models": one, "profile": "nope"}).status_code == 422
    empty = {"input_tokens": 0, "output_tokens": 0}
    assert client.post("/api/models/value", json={"models": one, "task": empty}).status_code == 422


def test_a_body_over_the_cap_is_refused_and_not_parsed(client):
    # The one POST an open-read visitor may call: nobody should be able to make it hold or echo megabytes.
    body = {"models": [{"provider": PROVIDER, "model": "acme/cheap-60:batch"}], "task": TASK, "pad": "x" * 200_000}
    response = client.post("/api/models/value", json=body)
    assert response.status_code == 413
    assert len(response.content) < 500
    assert "x" * 100 not in response.text


def test_a_body_just_under_the_cap_still_works(client):
    body = {"models": [{"provider": PROVIDER, "model": "acme/cheap-60:batch"}], "task": TASK, "pad": "x" * 100_000}
    assert client.post("/api/models/value", json=body).status_code == 200


def test_a_streamed_body_is_cut_off_at_the_cap_not_read_to_the_end(client):
    chunk = b"x" * 65_536
    pulled = 0
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal pulled
        if pulled >= 20:  # 1.3 MB on offer
            return {"type": "http.disconnect"}
        pulled += 1
        return {"type": "http.request", "body": chunk, "more_body": pulled < 20}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/models/value",
        "raw_path": b"/api/models/value",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],  # no content-length
    }
    asyncio.run(create_app()(scope, receive, send))
    assert sent[0]["status"] == 413
    assert pulled <= 4, "the body was read well past the cap"


def test_a_validation_error_does_not_echo_the_input(client):
    echoed = "P" * 5_000
    response = client.post("/api/models/value", json={"models": [{"provider": echoed, "model": "m"}]})
    assert response.status_code == 422
    assert echoed not in response.text
    assert '"input"' not in response.text
    assert len(response.content) < 1_000
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "models", 0, "provider"]


def test_malformed_json_gets_a_short_422(client):
    response = client.post(
        "/api/models/value", content=b'{"models": [' + b"zzzz" * 20_000, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "zzzz" not in response.text


def test_a_body_with_hundreds_of_errors_gets_a_bounded_answer(client):
    refs = [{"provider": "", "model": ""}] * 200
    response = client.post("/api/models/value", json={"models": refs})
    assert response.status_code == 422
    assert len(response.json()["detail"]) <= 20
    assert len(response.content) < 5_000


def test_the_request_body_is_still_in_the_api_schema():
    schema = create_app().openapi()
    post = schema["paths"]["/api/models/value"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ValueRequest"}
    assert {"ValueRequest", "ValueModelRef", "ValueTaskIn"} <= set(schema["components"]["schemas"])


def test_the_password_guard_covers_it(client, monkeypatch):
    monkeypatch.setenv("VIBONOMICS_PASSWORD", "hunter2")
    get_config.cache_clear()
    guarded = TestClient(create_app())
    response = guarded.post("/api/models/value", json={"models": [{"provider": PROVIDER, "model": "x"}]})
    assert response.status_code == 401


# The same cases as frontend/src/lib/pricing.test.ts, so the two copies of the maths stay in step.


def test_effective_input_price():
    assert effective_input_price(3, 0.3, 0) == 3
    assert effective_input_price(3, 0.3, 0.5) == pytest.approx(1.65)
    assert effective_input_price(2, None, 0.8) == 2
    assert effective_input_price(None, None, 0) is None


def test_task_cost():
    task = Task(30_000, 10_000)
    assert task_cost(3, 15, None, task) == pytest.approx(0.24)
    assert task_cost(0, 0, None, task, is_free=True) == 0
    assert task_cost(1, None, None, task) is None
    assert task_cost(None, None, None, task) is None


def test_value_of():
    assert value_of(80, 0.4) == 200
    assert value_of(80, 0) == math.inf
    assert value_of(None, 0.4) is None
    assert value_of(80, None) is None
