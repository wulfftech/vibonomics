"""Keeping model-written code away from the app: the Unix-socket sidecar and the sandbox-network guard."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from vibonomics import sandbox_guard
from vibonomics.config import get_config
from vibonomics.db import init_db
from vibonomics.evals import sandbox_client
from vibonomics.sandbox import server
from vibonomics.settings_store import AppSettings

# -- the sidecar on a Unix socket ------------------------------------------------------------


@pytest.fixture
def socket_sidecar(monkeypatch):
    """A real sidecar served on a Unix socket, as the compose default runs it."""
    path = str(Path(tempfile.mkdtemp(prefix="vibonomics-sock-")) / "sandbox.sock")
    sidecar = uvicorn.Server(uvicorn.Config(server.create_app(), uds=path, log_level="warning"))
    thread = threading.Thread(target=sidecar.run, daemon=True)
    thread.start()
    for _ in range(100):
        if sidecar.started:
            break
        time.sleep(0.05)
    monkeypatch.setenv("VIBONOMICS_SANDBOX_SOCKET", path)
    yield path
    sidecar.should_exit = True
    thread.join(timeout=5)


def test_code_runs_through_the_socket(socket_sidecar, monkeypatch):
    monkeypatch.setenv("VIBONOMICS_SANDBOX_URL", f"unix://{socket_sidecar}")
    get_config.cache_clear()
    try:
        settings = AppSettings()
        settings.evals.sandbox_mode = "sidecar"
        result = asyncio.run(sandbox_client.run_code({"main.py": "print('hi')\n"}, ["python", "main.py"], 20, settings))
    finally:
        get_config.cache_clear()
    assert result is not None
    assert (result.exit_code, result.stdout.strip()) == (0, "hi")


def test_healthcheck_uses_the_socket(socket_sidecar):
    assert server.check() == 0


def test_healthcheck_fails_without_a_sidecar(monkeypatch):
    monkeypatch.setenv("VIBONOMICS_SANDBOX_SOCKET", str(Path(tempfile.mkdtemp()) / "missing.sock"))
    assert server.check() == 1


@pytest.mark.parametrize("url", ["unix:///run/x/sandbox.sock", "unix:/run/x/sandbox.sock"])
def test_unix_urls(url):
    transport, base = sandbox_client.sidecar_transport(url)
    assert transport is not None
    assert base == "http://sandbox"


# -- the guard for a sidecar reached over TCP ------------------------------------------------

APP_ON_SANDBOX_NET = ipaddress.ip_address("172.30.0.3")


@pytest.fixture
def two_networks(monkeypatch):
    """The compose TCP layout: the sidecar on an internal network, the default route on another."""
    routes = {"172.30.0.2": "172.30.0.3", "192.0.2.1": "172.18.0.5"}

    def resolve(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.30.0.2", port))]

    monkeypatch.setattr(sandbox_guard.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(sandbox_guard, "_local_address", lambda _f, target, _p: ipaddress.ip_address(routes[target]))
    return routes


def test_finds_the_address_facing_the_sidecar(two_networks):
    assert sandbox_guard.sandbox_facing_addresses("http://sandbox:2290") == {APP_ON_SANDBOX_NET}


def test_never_guards_a_shared_interface(two_networks):
    two_networks["192.0.2.1"] = "172.30.0.3"  # the default route leaves through the sidecar's network
    assert sandbox_guard.sandbox_facing_addresses("http://sandbox:2290") == set()


def test_never_guards_loopback_or_a_socket():
    assert sandbox_guard.sandbox_facing_addresses("http://127.0.0.1:2290") == set()
    assert sandbox_guard.sandbox_facing_addresses("unix:///run/x/sandbox.sock") == set()


@pytest.fixture
def sidecar_down_for(monkeypatch):
    """Run ``keep_trying`` with refresh failing ``misses`` times, then succeeding. Returns the sleeps."""

    def run(misses: int) -> list[float]:
        delays: list[float] = []
        outcomes = iter([False] * misses + [True])

        async def fake_sleep(delay):
            delays.append(delay)

        async def fake_refresh():
            return next(outcomes)

        monkeypatch.setattr(sandbox_guard.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(sandbox_guard, "refresh", fake_refresh)
        asyncio.run(sandbox_guard.keep_trying())
        return delays

    return run


def test_keeps_trying_past_the_old_five_minute_limit(sidecar_down_for):
    delays = sidecar_down_for(100)  # the old loop gave up after 60 tries, at 300 s
    assert len(delays) == 101
    assert delays[:6] == [5, 10, 20, 40, 60, 60]
    assert max(delays) == 60
    assert sum(delays[:60]) > 300


def test_says_once_that_the_guard_is_still_off(sidecar_down_for, caplog):
    with caplog.at_level("WARNING", logger="vibonomics.sandbox_guard"):
        sidecar_down_for(100)
    assert sum("still off" in r.getMessage() for r in caplog.records) == 1


def test_stops_as_soon_as_the_sidecar_resolves(sidecar_down_for, caplog):
    with caplog.at_level("WARNING", logger="vibonomics.sandbox_guard"):
        delays = sidecar_down_for(2)
    assert delays == [5, 10, 20]
    assert not caplog.records


def test_requests_arriving_on_the_sandbox_network_are_refused(monkeypatch):
    init_db()
    monkeypatch.setattr(sandbox_guard, "_refused", frozenset({APP_ON_SANDBOX_NET}))
    from vibonomics.app import create_app

    app = create_app()
    from_sandbox = TestClient(app, base_url=f"http://{APP_ON_SANDBOX_NET}")
    for path in ("/api/system/status", "/api/providers/1/test", "/ping"):
        response = from_sandbox.get(path)
        assert response.status_code == 403, path
        assert response.json() == {"detail": "Requests from the sandbox network are refused"}
    assert TestClient(app, base_url="http://172.18.0.5").get("/ping").text == "pong"
