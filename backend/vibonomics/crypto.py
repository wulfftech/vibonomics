"""Encryption at rest for stored API keys.

Keys are encrypted with Fernet. The encryption key comes from ``VIBONOMICS_SECRET_KEY`` when set
(any string; it is stretched with SHA-256), otherwise from ``<config>/secret.key``, which is
generated on first run with owner-only permissions. Keeping the key in the same volume as the
database only protects against casual disclosure (backups, screenshots of the DB); set
``VIBONOMICS_SECRET_KEY`` from a secret store if you need more.
"""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import get_config


def write_owner_only(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


@lru_cache(maxsize=1)
def _key_bytes() -> bytes:
    cfg = get_config()
    if cfg.secret_key:
        return base64.urlsafe_b64encode(hashlib.sha256(cfg.secret_key.encode()).digest())
    path = cfg.secret_key_file
    if not path.exists():
        write_owner_only(path, Fernet.generate_key())
    return path.read_bytes().strip()


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_key_bytes())


def derive_secret(purpose: str) -> str:
    """A stable secret for other uses (e.g. cookie signing), derived from the encryption key."""
    return hashlib.sha256(_key_bytes() + b"|" + purpose.encode()).hexdigest()


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # The secret key changed; the stored value is unrecoverable and must be re-entered.
        return None


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"…{value[-4:]}" if len(value) > 8 else "…"
