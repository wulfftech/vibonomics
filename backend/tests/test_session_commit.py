"""A write that fails to commit must reach the client as an error, and nothing may announce it first."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vibonomics import events, scheduler
from vibonomics.api import settings as settings_api
from vibonomics.api import subscriptions as subscriptions_api
from vibonomics.db import get_session, init_db, session_scope
from vibonomics.orm import ProviderConfig, SubscriptionSnapshot
from vibonomics.settings_store import AppSettings, load_settings, save_settings


@pytest.fixture(autouse=True)
def database():
    init_db()
    with session_scope() as s:
        save_settings(s, AppSettings())


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(subscriptions_api.router)
    return app


def _fail_every_commit(monkeypatch) -> None:
    def commit(self: Session) -> None:
        raise IntegrityError("COMMIT", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(Session, "commit", commit)


def _settings_body(client: TestClient, **changes: object) -> dict:
    current = client.get("/api/settings").json()["settings"]
    return {"settings": {**current, "general": {**current["general"], **changes}}}


def test_a_commit_that_fails_once_the_route_has_returned_is_an_error_not_a_200():
    app = FastAPI()

    @app.post("/write")
    def write(session: Session = Depends(get_session)) -> dict[str, bool]:
        # NOT NULL is checked when the commit flushes, which is after this function has returned.
        session.add(ProviderConfig(type=None, name="never saved"))
        return {"saved": True}

    response = TestClient(app, raise_server_exceptions=False).post("/write")

    assert response.status_code == 500
    assert "saved" not in response.text


def test_a_settings_save_that_fails_to_commit_is_an_error_and_announces_nothing(monkeypatch):
    client = TestClient(_app(), raise_server_exceptions=False)
    body = _settings_body(client, instance_name="Never saved")
    announced: list[str] = []
    rescheduled: list[AppSettings] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: announced.append(name))
    monkeypatch.setattr(scheduler, "reschedule", rescheduled.append)
    _fail_every_commit(monkeypatch)

    response = client.put("/api/settings", json=body)

    assert response.status_code == 500
    assert announced == []
    assert rescheduled == []
    monkeypatch.undo()
    with session_scope() as s:
        assert load_settings(s).general.instance_name == "Vibonomics"


def test_a_settings_save_is_stored_before_the_event_and_the_reschedule(monkeypatch):
    client = TestClient(_app())
    body = _settings_body(client, instance_name="Stored first")
    seen: dict[str, str] = {}

    def stored_name() -> str:
        with session_scope() as s:  # another connection: it sees only what has been committed
            return load_settings(s).general.instance_name

    monkeypatch.setattr(events, "publish", lambda name, data=None: seen.setdefault(f"event {name}", stored_name()))
    monkeypatch.setattr(scheduler, "reschedule", lambda _s: seen.setdefault("reschedule", stored_name()))

    assert client.put("/api/settings", json=body).status_code == 200

    assert seen == {"event settings.changed": "Stored first", "reschedule": "Stored first"}


def _snapshots() -> int:
    with session_scope() as s:
        return len(s.scalars(select(SubscriptionSnapshot).where(SubscriptionSnapshot.kind == "claude")).all())


def _add_snapshot() -> None:
    with session_scope() as s:
        s.add(SubscriptionSnapshot(kind="claude", payload={}))


def test_clearing_the_snapshots_is_stored_before_the_event(monkeypatch):
    _add_snapshot()
    client = TestClient(_app())
    seen: list[int] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: seen.append(_snapshots()))

    assert client.delete("/api/subscription/claude").status_code == 204

    assert seen == [0]
    assert _snapshots() == 0


def test_clearing_the_snapshots_when_the_commit_fails_is_an_error_and_announces_nothing(monkeypatch):
    _add_snapshot()
    client = TestClient(_app(), raise_server_exceptions=False)
    announced: list[str] = []
    monkeypatch.setattr(events, "publish", lambda name, data=None: announced.append(name))
    _fail_every_commit(monkeypatch)

    response = client.delete("/api/subscription/claude")

    assert response.status_code == 500
    assert announced == []
    monkeypatch.undo()
    assert _snapshots() >= 1
