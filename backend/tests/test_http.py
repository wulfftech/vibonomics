"""The shared outbound client: what its errors say, where its redirects go, and what it caches."""

from __future__ import annotations

import httpx
import pytest
import respx

from vibonomics import http

KEY = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghij"


async def _no_wait(_seconds: float) -> None:
    return None


@pytest.fixture(autouse=True)
async def fresh_client(monkeypatch):
    """Each test gets a client on its own event loop, and none of the retry sleeps."""
    monkeypatch.setattr(http, "_client", None)
    monkeypatch.setattr(http.asyncio, "sleep", _no_wait)
    yield
    await http.close_client()


@respx.mock
async def test_a_key_echoed_across_the_cut_in_an_error_body_is_still_scrubbed():
    # The body repeats the key starting at character 263, so a 300-character cut falls inside it.
    body = "x" * 263 + KEY + " is not a valid key"
    respx.get("https://api.example/v1/models").mock(return_value=httpx.Response(401, text=body))

    with pytest.raises(http.FetchError) as caught:
        await http.get_json("https://api.example/v1/models", headers={"x-api-key": KEY}, retries=0)

    message = str(caught.value)
    assert "sk-ant-api03" not in message
    assert KEY[10:30] not in message
    assert "HTTP 401" in message
    assert len(message) < 400
