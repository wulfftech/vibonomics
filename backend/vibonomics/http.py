"""Shared outbound HTTP client and small helpers for fetchers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"Vibonomics/{__version__} (+self-hosted LLM analysis)"

_client: httpx.AsyncClient | None = None

_SECRET_HEADER = re.compile(r"auth|key|token|secret|cookie|credential|password", re.IGNORECASE)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_FIRST_ORIGIN = "vibonomics.first_origin"


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port or _DEFAULT_PORTS.get(url.scheme)


async def drop_secrets_on_redirect(request: httpx.Request) -> None:
    """Strip key and token headers from a request that a redirect has sent to another origin.

    httpx drops only ``Authorization`` there, so ``x-api-key`` and ``x-goog-api-key`` would go to whatever host a
    base URL override (or a hostile endpoint) redirects to. The first request of a chain records its origin in
    its extensions, which every redirected request in the chain inherits. An http to https upgrade of the same
    host keeps its headers, as httpx keeps ``Authorization`` there: they were already sent in the clear.
    """
    here = _origin(request.url)
    first = request.extensions.setdefault(_FIRST_ORIGIN, here)
    if here == first:
        return
    if first[1] == here[1] and (first[0], first[2], here[0], here[2]) == ("http", 80, "https", 443):
        return
    for name in {name for name in request.headers if _SECRET_HEADER.search(name)}:
        del request.headers[name]


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            event_hooks={"request": [drop_secrets_on_redirect]},
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class FetchError(Exception):
    """A fetch failed in a way worth surfacing to the operator (status code, auth, parse)."""

    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status


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
        raise FetchError(f"{_redact(url)}: response was not JSON") from exc


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
            detail = scrub_secrets(f"{type(exc).__name__}: {exc}", headers, params)
            raise FetchError(f"{method} {_redact(url)}: {detail}") from exc
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            await asyncio.sleep(2.0 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            # Scrub before cutting: a key the remote echoes across the cut would no longer match in full.
            body = scrub_secrets(resp.text.replace("\n", " "), headers, params)[:300]
            raise FetchError(f"{method} {_redact(url)}: HTTP {resp.status_code} {body}", status=resp.status_code)
        return resp
    raise FetchError(f"{method} {_redact(url)}: {scrub_secrets(str(last_exc), headers, params)}")


def scrub_secrets(text: str, *sources: dict[str, Any] | None) -> str:
    """Blank out header and query values (keys, tokens) wherever they appear in ``text``.

    httpx quotes a header value it refuses in full, e.g. ``Illegal header value b'Bearer sk-…\\n'``
    for a key pasted with a trailing newline, and some APIs echo the key in their error body.
    """
    for source in sources:
        for value in (source or {}).values():
            for part in str(value).split():
                if len(part) >= 8:
                    text = text.replace(part, "…")
    return text


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
