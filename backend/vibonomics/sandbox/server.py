"""Sandbox sidecar: a tiny HTTP service that executes model-written code.

Run it as a separate container with no secrets mounted. The compose default gives it no network at all
and serves it on a Unix socket in a volume shared with the app (``VIBONOMICS_SANDBOX_SOCKET`` here,
``VIBONOMICS_SANDBOX_URL=unix:///…`` in the app). Without a socket it listens on TCP port
``VIBONOMICS_SANDBOX_PORT`` (2290), for an internal-only network.

It only ever touches a fresh temp directory, so it works with a read-only root filesystem plus a
tmpfs at ``/tmp``. The compose service starts it as root with three capabilities and
``VIBONOMICS_SANDBOX_RUN_UIDS``, so each run executes under an unprivileged uid of its own (see
``executor.py``); without that variable it drops to ``VIBONOMICS_SANDBOX_UID`` (65534) and all runs share it.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import executor

log = logging.getLogger(__name__)

MAX_REQUEST_BYTES = executor.MAX_WORKSPACE_BYTES
MAX_TIMEOUT = 120.0
MAX_CONCURRENT = 2

_semaphore: asyncio.Semaphore | None = None


class RunRequest(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)
    command: list[str] = Field(min_length=1, max_length=20)
    timeout: float = Field(30.0, ge=1, le=MAX_TIMEOUT)
    cwd: str | None = None


class RunResponse(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    error_kind: str | None = None


def _check_token(token: str | None) -> None:
    expected = os.environ.get("VIBONOMICS_SANDBOX_TOKEN")
    if not expected:
        return
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Bad sandbox token")


def create_app() -> FastAPI:
    app = FastAPI(title="Vibonomics sandbox", version="1", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run", response_model=RunResponse)
    async def run(body: RunRequest, x_sandbox_token: str | None = Header(default=None)) -> RunResponse:
        global _semaphore
        _check_token(x_sandbox_token)
        if executor.workspace_size(body.files) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Workspace too large")
        if _semaphore is None:
            _semaphore = asyncio.Semaphore(min(MAX_CONCURRENT, len(executor.run_uids()) or MAX_CONCURRENT))
        async with _semaphore:
            result = await asyncio.to_thread(executor.run, body.files, body.command, body.timeout, cwd=body.cwd)
        return RunResponse(**result.to_dict())

    return app


def _socket_path() -> str | None:
    return os.environ.get("VIBONOMICS_SANDBOX_SOCKET") or None


def _port() -> int:
    return int(os.environ.get("VIBONOMICS_SANDBOX_PORT", "2290"))


def check() -> int:
    """Container healthcheck: GET /health over whichever transport the sidecar listens on."""
    import httpx

    socket_path = _socket_path()
    transport = httpx.HTTPTransport(uds=socket_path) if socket_path else None
    url = "http://sandbox/health" if socket_path else f"http://127.0.0.1:{_port()}/health"
    try:
        with httpx.Client(transport=transport, timeout=3) as client:
            client.get(url).raise_for_status()
    except httpx.HTTPError as exc:
        print(f"sandbox unhealthy: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    import uvicorn

    if "--check" in sys.argv[2:]:
        sys.exit(check())
    logging.basicConfig(level="INFO")
    if os.name == "posix" and os.geteuid() == 0:
        if executor.run_uids():
            # Root with CAP_SETUID/SETGID/CHOWN and nothing else, so each run can be started under a uid of its
            # own. Refuse to serve if that does not work, rather than leave every task ungraded.
            problem = executor.selftest()
            if problem:
                log.critical("Run uids %s do not work: %s", executor.run_uids(), problem)
                sys.exit(1)
            log.info("Running as root so each run starts under its own uid (%s)", executor.run_uids())
        else:
            try:  # without run uids, keep a manual `docker run` as an unprivileged user
                os.setuid(int(os.environ.get("VIBONOMICS_SANDBOX_UID", "65534")))
            except (OSError, ValueError):
                log.warning("Could not drop privileges; the sandbox is running as root")
    socket_path = _socket_path()
    if socket_path:
        uvicorn.run(create_app(), uds=socket_path, log_level="info")
    else:
        uvicorn.run(create_app(), host="0.0.0.0", port=_port(), log_level="info")
