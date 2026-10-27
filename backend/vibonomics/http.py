"""Shared outbound HTTP client and small helpers for fetchers."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"Vibonomics/{__version__} (+self-hosted LLM analysis)"

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class FetchError(Exception):
    """A fetch failed in a way worth surfacing to the operator (status code, auth, parse)."""


async def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    retries: int = 2,
) -> Any:
    resp = await request("GET", url, headers=headers, params=params, timeout=timeout, retries=retries)
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url}: response was not JSON") from exc


async def get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    retries: int = 2,
) -> str:
    resp = await request("GET", url, headers=headers, params=params, timeout=timeout, retries=retries)
    return resp.text


async def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    timeout: float | None = None,
    retries: int = 2,
) -> httpx.Response:
    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise FetchError(f"{method} {_redact(url)}: {type(exc).__name__}: {exc}") from exc
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            await asyncio.sleep(2.0 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            body = resp.text[:300].replace("\n", " ")
            raise FetchError(f"{method} {_redact(url)}: HTTP {resp.status_code} {body}")
        return resp
    raise FetchError(f"{method} {_redact(url)}: {last_exc}")


def _redact(url: str) -> str:
    # Some APIs (Gemini) accept ?key=...; never let it reach logs or the UI.
    if "key=" in url:
        head, _, _tail = url.partition("?")
        return head + "?…"
    return url


async def cached_json(
    url: str,
    cache_file: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> Any:
    """Fetch JSON, falling back to the last good copy on disk when the network fails."""
    try:
        data = await get_json(url, headers=headers, timeout=timeout)
    except FetchError:
        if cache_file.exists():
            log.warning("Using cached copy of %s", url)
            return json.loads(cache_file.read_text(encoding="utf-8"))
        raise
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(cache_file)
    except OSError as exc:  # cache is best-effort
        log.warning("Could not cache %s: %s", url, exc)
    return data
