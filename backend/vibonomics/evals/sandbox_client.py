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


def sidecar_transport(url: str) -> tuple[httpx.AsyncHTTPTransport | None, str]:
    """The transport and base URL for the sidecar. ``unix:///path`` is a sidecar with no network,
    reached through its socket on a shared volume; anything else is plain HTTP."""
    if url.startswith("unix:"):
        path = "/" + url.removeprefix("unix:").lstrip("/")
        return httpx.AsyncHTTPTransport(uds=path), "http://sandbox"
    return None, url


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
        transport, base = sidecar_transport(url)
        try:
            limits = httpx.Timeout(timeout + 20, connect=10)
            async with httpx.AsyncClient(timeout=limits, transport=transport) as client:
                response = await client.post(
                    f"{base}/run",
                    json={"files": files, "command": command, "timeout": timeout, "cwd": cwd},
                    headers=headers,
                )
                if response.status_code == 413:
                    # The sidecar refuses an oversized workspace before running anything: the files' fault.
                    return ExecResult(None, "", "Workspace too large", False, 0, error_kind=executor.WORKSPACE_ERROR)
                response.raise_for_status()
                data = response.json()
            return ExecResult(
                exit_code=data.get("exit_code"),
                stdout=str(data.get("stdout") or ""),
                stderr=str(data.get("stderr") or ""),
                timed_out=bool(data.get("timed_out")),
                duration_ms=int(data.get("duration_ms") or 0),
                error_kind=data.get("error_kind"),
            )
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Sandbox sidecar call failed: %s", exc)
            return ExecResult(None, "", f"Sandbox unavailable: {type(exc).__name__}: {exc}", False, 0)
    return await asyncio.to_thread(executor.run, files, command, timeout, cwd=cwd)
