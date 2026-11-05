"""The process entry point: what it hands to uvicorn, and what uvicorn's access log may record."""

from __future__ import annotations

import logging
import sys

import pytest

from vibonomics import __main__ as entry


def _run(monkeypatch) -> dict:
    seen: dict = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["vibonomics"])
    entry.main()
    return seen


def test_forwarded_headers_are_trusted_from_every_address_by_default(monkeypatch):
    # The deployed container sits behind a proxy on another address, so the default must not change.
    monkeypatch.delenv("VIBONOMICS_FORWARDED_ALLOW_IPS", raising=False)
    seen = _run(monkeypatch)
    assert seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == "*"


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_setting_keeps_the_default(monkeypatch, value):
    # docker-compose passes an unset variable through as an empty string.
    monkeypatch.setenv("VIBONOMICS_FORWARDED_ALLOW_IPS", value)
    assert _run(monkeypatch)["forwarded_allow_ips"] == "*"


def test_the_operator_can_narrow_who_may_set_forwarded_headers(monkeypatch):
    monkeypatch.setenv("VIBONOMICS_FORWARDED_ALLOW_IPS", " 127.0.0.1, 192.168.10.2 ")
    assert _run(monkeypatch)["forwarded_allow_ips"] == "127.0.0.1, 192.168.10.2"


def _access_record(path: str) -> logging.LogRecord:
    # The shape uvicorn gives its access log: client, method, full path with the query string, HTTP version, status.
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.5:5123", "POST", path, "1.1", 202),
        None,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/subscription/ingest/claude?token=sk-test-abc123",
        "/api/subscription/ingest/claude?x=1&token=sk-test-abc123&y=2",
        "/vibonomics/api/subscription/ingest/claude?TOKEN=sk-test-abc123",
    ],
)
def test_the_access_log_does_not_record_a_token_in_the_query_string(path):
    record = _access_record(path)
    assert entry.RedactIngestToken().filter(record) is True
    line = record.getMessage()
    assert "sk-test-abc123" not in line
    assert "/api/subscription/ingest/claude?" in line
    assert line.startswith('10.0.0.5:5123 - "POST ')
    assert line.endswith('HTTP/1.1" 202')


def test_other_query_strings_are_logged_as_they_are():
    record = _access_record("/api/models?provider=openrouter&limit=5")
    entry.RedactIngestToken().filter(record)
    assert "/api/models?provider=openrouter&limit=5" in record.getMessage()


def test_main_puts_the_redaction_on_uvicorns_access_log_once(monkeypatch):
    logger = logging.getLogger("uvicorn.access")
    before = list(logger.filters)
    try:
        _run(monkeypatch)
        _run(monkeypatch)
        installed = [f for f in logger.filters if isinstance(f, entry.RedactIngestToken)]
        assert len(installed) == 1
    finally:
        logger.filters[:] = before
