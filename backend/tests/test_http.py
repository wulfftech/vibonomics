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


@pytest.mark.parametrize(
    ("url", "shown", "public"),
    [
        ("https://gw.example/v1/models", "https://gw.example/v1/models", "https://gw.example/v1/models"),
        ("https://admin:pass@gw.example/v1", "https://gw.example/v1", "https://gw.example/v1"),
        ("https://admin@gw.example:8443/v1", "https://gw.example:8443/v1", "https://gw.example:8443/v1"),
        ("https://gw.example/v1?token=abc123", "https://gw.example/v1?…", "https://gw.example/v1"),
        (
            "https://gw.example/v1/models?key=AIzaSyFAKE",
            "https://gw.example/v1/models?…",
            "https://gw.example/v1/models",
        ),
        ("https://u:p@gw.example/v1?a=1&b=2#frag", "https://gw.example/v1?…", "https://gw.example/v1"),
        ("http://[::1]:4000/v1?x=1", "http://[::1]:4000/v1?…", "http://[::1]:4000/v1"),
        ("gw.example/v1", "gw.example/v1", "gw.example/v1"),
    ],
)
def test_a_url_shown_to_a_person_carries_no_credentials(url, shown, public):
    assert http._redact(url) == shown
    assert http.public_url(url) == public


@respx.mock
async def test_an_error_names_the_url_without_its_credentials():
    respx.get("https://gw.example/v1/models").mock(return_value=httpx.Response(404, text="not here"))

    with pytest.raises(http.FetchError) as caught:
        await http.get_json("https://admin:s3cret@gw.example/v1/models?token=abc123", retries=0)

    assert "s3cret" not in str(caught.value)
    assert "abc123" not in str(caught.value)
    assert "gw.example/v1/models" in str(caught.value)


def test_an_error_built_from_a_base_url_is_redacted_where_it_is_made():
    # The adapters word these themselves, e.g. f"{self.base_url}/models: unexpected response shape".
    error = http.FetchError(
        "https://admin:s3cret@gw.example/v1/models: unexpected shape (see https://x.example/a?token=abc123 too)"
    )

    assert "s3cret" not in str(error)
    assert "abc123" not in str(error)
    assert str(error) == "https://gw.example/v1/models: unexpected shape (see https://x.example/a?… too)"


SECRET_HEADERS = {
    "x-api-key": KEY,
    "x-goog-api-key": KEY,
    "Authorization": f"Bearer {KEY}",
    "api-key": KEY,
    "X-Service-Token": KEY,
}


def _redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"Location": location})


@respx.mock
@pytest.mark.parametrize(
    "location",
    [
        "https://elsewhere.example/steal",
        "https://api.example:8443/landing",
        "http://api.example/landing",
    ],
    ids=["other-host", "other-port", "downgrade-to-http"],
)
async def test_a_redirect_that_leaves_the_origin_carries_no_secret_header(location):
    respx.get("https://api.example/v1/models").mock(return_value=_redirect(location))
    landing = respx.get(location).mock(return_value=httpx.Response(200, json={"data": []}))

    await http.get_json("https://api.example/v1/models", headers={**SECRET_HEADERS, "Accept": "application/json"})

    assert landing.call_count == 1
    arrived = landing.calls[0].request.headers
    for name in SECRET_HEADERS:
        assert name not in arrived, name
    assert arrived["accept"] == "application/json"


@respx.mock
async def test_a_redirect_that_leaves_the_origin_carries_no_secret_header_on_a_post_either():
    respx.post("https://api.example/v1/chat").mock(return_value=_redirect("https://elsewhere.example/steal", 307))
    landing = respx.post("https://elsewhere.example/steal").mock(return_value=httpx.Response(200, json={}))

    await http.request("POST", "https://api.example/v1/chat", headers=SECRET_HEADERS, json_body={"a": 1})

    assert landing.call_count == 1
    assert not set(landing.calls[0].request.headers) & {name.lower() for name in SECRET_HEADERS}


@respx.mock
@pytest.mark.parametrize(
    "location",
    ["https://api.example/v2/models", "https://api.example:443/landing"],
    ids=["other-path", "explicit-default-port"],
)
async def test_a_redirect_within_the_origin_keeps_the_headers(location):
    respx.get("https://api.example/v1/models").mock(return_value=_redirect(location))
    landing = respx.get(location).mock(return_value=httpx.Response(200, json={"data": []}))

    await http.get_json("https://api.example/v1/models", headers=SECRET_HEADERS)

    arrived = landing.calls[0].request.headers
    assert arrived["x-api-key"] == KEY
    assert arrived["x-goog-api-key"] == KEY


@respx.mock
async def test_an_http_to_https_upgrade_on_the_same_host_keeps_the_headers():
    # Authorization survives this in httpx itself; the key headers follow the same rule, or a plain-http endpoint
    # that upgrades would start answering 401.
    respx.get("http://llm.internal/v1/models").mock(return_value=_redirect("https://llm.internal/v1/models", 301))
    landing = respx.get("https://llm.internal/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

    await http.get_json("http://llm.internal/v1/models", headers=SECRET_HEADERS)

    assert landing.calls[0].request.headers["x-api-key"] == KEY


@respx.mock
async def test_a_chain_that_leaves_the_origin_and_comes_back_does_not_get_the_headers_back():
    respx.get("https://api.example/start").mock(return_value=_redirect("https://elsewhere.example/hop"))
    respx.get("https://elsewhere.example/hop").mock(return_value=_redirect("https://api.example/back"))
    landing = respx.get("https://api.example/back").mock(return_value=httpx.Response(200, json={}))

    await http.get_json("https://api.example/start", headers=SECRET_HEADERS)

    assert "x-api-key" not in landing.calls[0].request.headers
