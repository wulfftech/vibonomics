"""VIBONOMICS_OPEN_READ: visitors read without logging in, and changing anything needs the password."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from vibonomics.api.auth import READ_METHODS
from vibonomics.app import _api_app, create_app
from vibonomics.config import get_config
from vibonomics.db import init_db, session_scope
from vibonomics.orm import Balance

LOGIN = {"password": "hunter2"}
NOT_AUTHENTICATED = {"detail": "Not authenticated"}


@pytest.fixture(params=["", "/vibonomics"], ids=["root", "url-base"])
def client(request, monkeypatch) -> TestClient:
    monkeypatch.setenv("VIBONOMICS_PASSWORD", "hunter2")
    monkeypatch.setenv("VIBONOMICS_OPEN_READ", "1")
    monkeypatch.setenv("VIBONOMICS_URL_BASE", request.param)
    get_config.cache_clear()
    init_db()
    yield TestClient(create_app(), base_url=f"http://testserver{request.param}")
    get_config.cache_clear()


def _logged_in(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json=LOGIN).status_code == 200
    return client


def test_visitors_can_read(client):
    for path in (
        "/api/models",
        "/api/providers",
        "/api/settings",
        "/api/system/status",
        "/api/evals/runs",
        "/api/usage",
    ):
        assert client.get(path).status_code == 200, path


def test_the_status_says_reading_is_open_and_login_still_works(client):
    assert client.get("/api/auth/status").json() == {"enabled": True, "authenticated": False, "open_read": True}
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json=LOGIN).json() == {
        "enabled": True,
        "authenticated": True,
        "open_read": True,
    }
    assert client.get("/api/auth/status").json()["authenticated"] is True
    assert client.post("/api/auth/logout").json() == {"enabled": True, "authenticated": False, "open_read": True}


# Every route that changes something, with the method that reaches it.
CHANGES = [
    ("PUT", "/api/settings"),
    ("POST", "/api/refresh"),
    ("POST", "/api/providers"),
    ("PUT", "/api/providers/1"),
    ("DELETE", "/api/providers/1"),
    ("POST", "/api/providers/test"),
    ("POST", "/api/providers/1/test"),
    ("POST", "/api/providers/refresh"),
    ("POST", "/api/providers/1/refresh"),
    ("POST", "/api/presets/list"),
    ("POST", "/api/presets/plan"),
    ("POST", "/api/presets/apply"),
    ("POST", "/api/presets/inspect"),
    ("POST", "/api/presets/adopt"),
    ("POST", "/api/evals/runs"),
    ("POST", "/api/evals/runs/1/cancel"),
    ("DELETE", "/api/evals/runs/1"),
    ("PUT", "/api/benchmarks/aliases"),
    ("DELETE", "/api/subscription/claude"),
    ("POST", "/api/subscription/claude/rotate"),
]


@pytest.mark.parametrize(("method", "path"), CHANGES, ids=[f"{m} {p}" for m, p in CHANGES])
def test_visitors_cannot_change_anything(client, method, path):
    response = client.request(method, path, json={})
    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


def test_after_login_a_change_reaches_its_route(client):
    # Whatever the route says about an empty body or a missing id, it is no longer the guard's 401.
    _logged_in(client)
    # Some routes call out with a stored key, and an earlier test may have left one in the shared database.
    with respx.mock(assert_all_called=False) as outbound:
        outbound.route().mock(return_value=httpx.Response(401, json={"error": "no network in tests"}))
        for method, path in CHANGES:
            assert client.request(method, path, json={}).status_code != 401, (method, path)


def test_the_read_only_post_is_open(client):
    body = {"models": [{"provider": "nowhere", "model": "x"}], "task": {"input_tokens": 1000, "output_tokens": 100}}
    response = client.post("/api/models/value", json=body)
    assert response.status_code == 200
    assert response.json()["models"][0]["found"] is False


def test_ingest_still_checks_its_own_token(client):
    response = client.post("/api/subscription/ingest/claude?token=wrong", json={})
    assert response.status_code == 401
    assert response.json() == {"detail": "Bad subscription ingest token"}


def test_a_route_added_later_is_protected_until_it_is_listed(client):
    # Walks every route in the API schema: anything that is not a read must refuse a visitor, except what is
    # listed as open. A new mutating route is refused without anyone remembering to protect it.
    opened = ("/api/auth/", "/api/subscription/ingest/", "/api/models/value")
    checked = 0
    for path, operations in _api_app().openapi()["paths"].items():
        if path.startswith(opened):
            continue
        for method in operations:
            if method.upper() in READ_METHODS:
                continue
            response = client.request(method.upper(), re.sub(r"\{[^}]+\}", "1", path), json={})
            assert response.status_code == 401, (method, path)
            checked += 1
    assert checked >= len(CHANGES)


def test_a_visitor_sees_which_keys_are_set_but_not_their_ends(client):
    _logged_in(client)
    created = client.post(
        "/api/providers",
        json={"type": "openrouter", "name": "Open read test", "enabled": False, "api_key": "sk-test-1234567890abcd"},
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    try:
        mine = next(p for p in client.get("/api/providers").json() if p["id"] == provider_id)
        assert mine["api_key_set"] is True
        assert mine["api_key_hint"]

        client.post("/api/auth/logout")
        theirs = next(p for p in client.get("/api/providers").json() if p["id"] == provider_id)
        assert theirs["api_key_set"] is True
        assert theirs["api_key_hint"] is None
        assert theirs["mgmt_key_hint"] is None
        assert theirs["extra_key_hint"] is None
    finally:
        _logged_in(client).delete(f"/api/providers/{provider_id}")


def test_a_visitor_does_not_get_a_keys_label_in_the_balance(client):
    # OpenRouter's key label is a masked copy of the key, which the hints beside it hide from visitors.
    _logged_in(client)
    created = client.post(
        "/api/providers", json={"type": "openrouter", "name": "Open read label test", "enabled": False}
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    with session_scope() as session:
        session.add(Balance(provider_config_id=provider_id, status="ok", key_label="sk-or-v1-abc...xyz"))
    try:
        mine = next(p for p in client.get("/api/providers").json() if p["id"] == provider_id)
        assert mine["balance"]["key_label"] == "sk-or-v1-abc...xyz"

        client.post("/api/auth/logout")
        response = client.get("/api/providers")
        theirs = next(p for p in response.json() if p["id"] == provider_id)
        assert theirs["balance"]["status"] == "ok"
        assert theirs["balance"]["key_label"] is None
        assert "sk-or-v1-abc" not in response.text
    finally:
        _logged_in(client).delete(f"/api/providers/{provider_id}")


def test_a_visitor_gets_a_base_url_without_its_credentials(client):
    _logged_in(client)
    created = client.post(
        "/api/providers",
        json={
            "type": "custom-openai",
            "name": "Open read url test",
            "enabled": False,
            "base_url": "https://admin:s3cret@gateway.example:8443/v1?token=abc123",
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    try:
        mine = next(p for p in client.get("/api/providers").json() if p["id"] == provider_id)
        assert mine["base_url"] == "https://admin:s3cret@gateway.example:8443/v1?token=abc123"

        client.post("/api/auth/logout")
        theirs = next(p for p in client.get("/api/providers").json() if p["id"] == provider_id)
        assert theirs["base_url"] == "https://gateway.example:8443/v1"
    finally:
        _logged_in(client).delete(f"/api/providers/{provider_id}")


def test_a_visitor_does_not_get_the_ingest_token(client):
    _logged_in(client)
    mine = client.get("/api/subscription/claude").json()
    token = mine["ingest_token"]
    assert token and token in mine["statusline_command"]

    client.post("/api/auth/logout")
    response = client.get("/api/subscription/claude")
    assert response.status_code == 200
    theirs = response.json()
    assert (theirs["ingest_token"], theirs["statusline_command"]) == ("", "")
    assert token not in response.text


def test_without_a_password_open_read_changes_nothing(monkeypatch):
    monkeypatch.delenv("VIBONOMICS_PASSWORD", raising=False)
    monkeypatch.delenv("VIBONOMICS_URL_BASE", raising=False)
    monkeypatch.setenv("VIBONOMICS_OPEN_READ", "1")
    get_config.cache_clear()
    init_db()
    try:
        client = TestClient(create_app())
        assert client.get("/api/auth/status").json() == {"enabled": False, "authenticated": True, "open_read": False}
        assert client.get("/api/providers").status_code == 200
    finally:
        get_config.cache_clear()


def test_without_open_read_a_visitor_gets_nothing(monkeypatch):
    monkeypatch.setenv("VIBONOMICS_PASSWORD", "hunter2")
    monkeypatch.delenv("VIBONOMICS_OPEN_READ", raising=False)
    monkeypatch.delenv("VIBONOMICS_URL_BASE", raising=False)
    get_config.cache_clear()
    init_db()
    try:
        client = TestClient(create_app())
        assert client.get("/api/auth/status").json()["open_read"] is False
        assert client.get("/api/models").status_code == 401
        assert client.post("/api/models/value", json={"models": []}).status_code == 401
    finally:
        get_config.cache_clear()


# A POST with no body is a "simple request": a page on another site can send it with a form or a no-cors fetch, and
# the browser attaches whatever cookie it holds (or the instance has no password at all). These are the ones that did
# something when sent that way.
BODYLESS_POSTS = [
    "/api/subscription/claude/rotate",
    "/api/providers/1/refresh",
    "/api/evals/runs/1/cancel",
    "/api/presets/list",
    "/api/presets/plan",
    "/api/auth/logout",
]
REFUSED = {"detail": "Cross-site requests are refused"}


@pytest.mark.parametrize("path", BODYLESS_POSTS)
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example", "Content-Type": "text/plain"},
        # Over plain HTTP a browser sends no Fetch Metadata, but it still names the page's origin.
        {"Origin": "http://evil.example", "Content-Type": "text/plain"},
    ],
    ids=["fetch-metadata", "both", "origin-only"],
)
def test_a_cross_site_post_is_refused_even_with_a_session(client, path, headers):
    _logged_in(client)
    token = client.get("/api/subscription/claude").json()["ingest_token"]

    response = client.post(path, headers=headers)
    assert response.status_code == 403
    assert response.json() == REFUSED

    assert client.get("/api/subscription/claude").json()["ingest_token"] == token  # not rotated
    assert client.get("/api/auth/status").json()["authenticated"] is True  # not logged out


@pytest.mark.parametrize(
    "headers",
    [
        {},  # curl, which is how the status-line command runs
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "http://testserver"},  # a browser that sends no Fetch Metadata, such as one on plain HTTP
        # A proxy that rewrites the Host header: the browser still says the page is the app's own.
        {"Sec-Fetch-Site": "same-origin", "Origin": "https://vibonomics.example.net"},
        # An old browser behind a proxy that rewrites Host but passes the original in X-Forwarded-Host.
        {"Origin": "https://vibonomics.example.net", "X-Forwarded-Host": "vibonomics.example.net"},
        {"Origin": "https://vibonomics.example.net", "X-Forwarded-Host": "other.example, vibonomics.example.net"},
    ],
    ids=["curl", "same-origin", "none", "origin-is-host", "proxy-rewrites-host", "forwarded-host", "forwarded-list"],
)
def test_a_post_from_the_app_itself_or_a_script_goes_through(client, headers):
    _logged_in(client)
    assert client.post("/api/subscription/claude/rotate", headers=headers).status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "http://testserver:3000", "null"])
def test_an_origin_that_is_not_this_app_is_refused_when_the_browser_sends_no_fetch_metadata(client, origin):
    _logged_in(client)
    response = client.post("/api/subscription/claude/rotate", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json() == REFUSED


def test_a_listed_host_is_accepted_as_an_origin_and_another_is_not(monkeypatch):
    monkeypatch.setenv("VIBONOMICS_PASSWORD", "hunter2")
    monkeypatch.setenv("VIBONOMICS_ALLOWED_HOSTS", "testserver, vibonomics.example.net")
    monkeypatch.delenv("VIBONOMICS_URL_BASE", raising=False)
    get_config.cache_clear()
    init_db()
    try:
        client = _logged_in(TestClient(create_app()))
        rotate = "/api/subscription/claude/rotate"
        assert client.post(rotate, headers={"Origin": "https://vibonomics.example.net"}).status_code == 200
        assert client.post(rotate, headers={"Origin": "https://evil.example"}).status_code == 403
    finally:
        get_config.cache_clear()


def test_every_route_that_changes_something_refuses_a_cross_site_request(client):
    # The same walk as above, with a session: the check does not depend on a route remembering to ask for it.
    # The ingest route is left out on purpose: it takes a secret token, which a page on another site cannot have.
    _logged_in(client)
    checked = 0
    for path, operations in _api_app().openapi()["paths"].items():
        if path.startswith("/api/subscription/ingest/"):
            continue
        for method in operations:
            if method.upper() in READ_METHODS:
                continue
            url = re.sub(r"\{[^}]+\}", "1", path)
            response = client.request(method.upper(), url, json={}, headers={"Sec-Fetch-Site": "cross-site"})
            assert response.status_code == 403, (method, path)
            checked += 1
    assert checked >= len(CHANGES)


def test_ingest_still_works_for_a_token_holder_and_is_not_blocked_by_the_cross_site_check(client):
    _logged_in(client)
    token = client.get("/api/subscription/claude").json()["ingest_token"]
    client.post("/api/auth/logout")  # the status-line command has no session

    snapshot = b'{"rate_limits": {"five_hour": {"used_percentage": 5}}}'
    headers = {"X-Vibonomics-Token": token, "Content-Type": "application/json"}
    assert client.post("/api/subscription/ingest/claude", content=snapshot, headers=headers).status_code == 202
    # Not the cross-site refusal: the route checks its token, and a page on another site has none.
    wrong = client.post("/api/subscription/ingest/claude?token=wrong", headers={"Sec-Fetch-Site": "cross-site"})
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "Bad subscription ingest token"}
