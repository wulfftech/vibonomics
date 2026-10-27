"""Run model-written code through whichever sandbox the operator configured."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from ..config import get_config
from ..sandbox import executor
from ..sandbox.executor import ExecResult
from ..settings_store import AppSettings

log = logging.getLogger(__name__)


def mode_for(settings: AppSettings) -> str:
    from ..api.settings import effective_sandbox_mode

    return effective_sandbox_mode(settings)


async def run_code(
    files: dict[str, str],
    command: list[str],
    timeout: float,
    settings: AppSettings,
    *,
    cwd: str | None = None,
) -> ExecResult | None:
    """Return the result, or None when code execution is disabled."""
    mode = mode_for(settings)
    if mode == "disabled":
        return None
    if mode == "sidecar":
        url = (get_config().sandbox_url or "").rstrip("/")
        if not url:
            return None
        headers = {}
        token = os.environ.get("VIBONOMICS_SANDBOX_TOKEN")
        if token:
            headers["X-Sandbox-Token"] = token
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 20, connect=10)) as client:
                response = await client.post(
                    f"{url}/run",
                    json={"files": files, "command": command, "timeout": timeout, "cwd": cwd},
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
            return ExecResult(
                exit_code=data.get("exit_code"),
                stdout=str(data.get("stdout") or ""),
                stderr=str(data.get("stderr") or ""),
                timed_out=bool(data.get("timed_out")),
                duration_ms=int(data.get("duration_ms") or 0),
            )
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Sandbox sidecar call failed: %s", exc)
            return ExecResult(None, "", f"Sandbox unavailable: {type(exc).__name__}: {exc}", False, 0)
    return await asyncio.to_thread(executor.run, files, command, timeout, cwd=cwd)
