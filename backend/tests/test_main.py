"""The process entry point: what it hands to uvicorn, and what uvicorn's access log may record."""

from __future__ import annotations

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
