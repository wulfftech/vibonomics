"""Process-level configuration read from environment variables.

Everything an operator can change at runtime lives in the settings store (``settings_store.py``).
This module only covers what must be known before the database opens: paths, port, secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _find_static_dir() -> Path | None:
    explicit = _env("VIBONOMICS_STATIC_DIR")
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve().parent
    for candidate in (here / "static", here.parent.parent / "frontend" / "dist"):
        if (candidate / "index.html").exists():
            return candidate
    return None


@dataclass(frozen=True)
class AppConfig:
    config_dir: Path
    host: str
    port: int
    url_base: str
    sandbox_url: str | None
    sandbox_guard: bool
    secret_key: str | None
    password: str | None
    log_level: str
    static_dir: Path | None
    # Host names the app answers to; empty means any. See ``app.py`` for why this exists.
    allowed_hosts: tuple[str, ...] = ()
    # With a password set, let visitors read without logging in; changing anything still needs the password.
    open_read: bool = False

    @property
    def db_path(self) -> Path:
        return self.config_dir / "vibonomics.db"

    @property
    def cache_dir(self) -> Path:
        return self.config_dir / "cache"

    @property
    def secret_key_file(self) -> Path:
        return self.config_dir / "secret.key"


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    url_base = (_env("VIBONOMICS_URL_BASE", "") or "").strip("/")
    return AppConfig(
        config_dir=Path(_env("VIBONOMICS_CONFIG_DIR", "./config") or "./config").resolve(),
        host=_env("VIBONOMICS_HOST", "0.0.0.0") or "0.0.0.0",
        port=int(_env("VIBONOMICS_PORT", "2289") or "2289"),
        url_base=f"/{url_base}" if url_base else "",
        sandbox_url=_env("VIBONOMICS_SANDBOX_URL"),
        sandbox_guard=(_env("VIBONOMICS_SANDBOX_GUARD", "on") or "on").lower() not in {"off", "false", "0", "no"},
        secret_key=_env("VIBONOMICS_SECRET_KEY"),
        password=_env("VIBONOMICS_PASSWORD"),
        log_level=(_env("VIBONOMICS_LOG_LEVEL", "info") or "info").lower(),
        static_dir=_find_static_dir(),
        allowed_hosts=tuple(h.strip() for h in (_env("VIBONOMICS_ALLOWED_HOSTS", "") or "").split(",") if h.strip()),
        open_read=(_env("VIBONOMICS_OPEN_READ", "") or "").lower() in {"1", "true", "yes", "on"},
    )


def env_value(name: str) -> str | None:
    """Read an arbitrary environment variable (used for per-provider key overrides)."""
    return _env(name)
