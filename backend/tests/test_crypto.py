"""Encryption at rest: a secret that cannot be read must say so, once, without saying what it was."""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from vibonomics import crypto


@pytest.fixture(autouse=True)
def not_yet_warned(monkeypatch):
    monkeypatch.setattr(crypto, "_warned_unreadable", False)


def test_a_stored_secret_round_trips_without_a_word_in_the_log(caplog):
    with caplog.at_level(logging.DEBUG, logger="vibonomics.crypto"):
        assert crypto.decrypt(crypto.encrypt("sk-test-roundtrip")) == "sk-test-roundtrip"
    assert caplog.records == []


def test_a_secret_encrypted_under_another_key_reads_as_not_set_and_the_log_says_why_once(caplog):
    token = Fernet(Fernet.generate_key()).encrypt(b"sk-test-old-key").decode()

    with caplog.at_level(logging.DEBUG, logger="vibonomics.crypto"):
        assert crypto.decrypt(token) is None
        assert crypto.decrypt(token) is None

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "secret key" in message
    assert "sk-test-old-key" not in message
    assert token not in message
