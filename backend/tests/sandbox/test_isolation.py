"""Keeping model-written code away from the app: the sidecar on a Unix socket."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from vibonomics.config import get_config
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
