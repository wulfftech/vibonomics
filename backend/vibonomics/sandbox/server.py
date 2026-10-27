"""Sandbox sidecar: a tiny HTTP service that executes model-written code.

Run it as a separate container on an internal-only network with no secrets mounted:

    docker compose up            # the "sandbox" service; VIBONOMICS_SANDBOX_URL=http://sandbox:2290

It only ever touches a fresh temp directory, so it works with a read-only root filesystem plus a
tmpfs at ``/tmp``.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import executor

log = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 2 * 1024 * 1024
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
        size = sum(len(v.encode("utf-8", errors="replace")) for v in body.files.values())
        if size > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Workspace too large")
        if _semaphore is None:
            _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        async with _semaphore:
            result = await asyncio.to_thread(executor.run, body.files, body.command, body.timeout, cwd=body.cwd)
        return RunResponse(**result.to_dict())

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level="INFO")
    port = int(os.environ.get("VIBONOMICS_SANDBOX_PORT", "2290"))
    if os.name == "posix" and os.geteuid() == 0:
        try:  # the compose service drops privileges; this keeps a manual `docker run` safer too
            os.setuid(int(os.environ.get("VIBONOMICS_SANDBOX_UID", "65534")))
        except (OSError, ValueError):
            log.warning("Could not drop privileges; the sandbox is running as root")
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="info")
