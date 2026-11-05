"""Entry point: ``python -m vibonomics`` (web app) or ``python -m vibonomics sandbox`` (code sandbox)."""

from __future__ import annotations

import logging
import os
import re
import sys

import uvicorn

from .config import get_config

_TOKEN_QUERY = re.compile(r"([?&]token=)[^&\s\"]*", re.IGNORECASE)


class RedactIngestToken(logging.Filter):
    """Blank the value of a ``token`` query parameter in a log record.

    The ingest endpoint still accepts its token as ``?token=`` for scripts written before the header, and uvicorn's
    access log records the whole query string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_TOKEN_QUERY.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        return True


def _forwarded_allow_ips() -> str:
    """Which peers may set X-Forwarded-For and X-Forwarded-Proto: anyone (``*``) unless the operator narrows it."""
    return (os.environ.get("VIBONOMICS_FORWARDED_ALLOW_IPS") or "").strip() or "*"


def main() -> None:
    cfg = get_config()
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if len(sys.argv) > 1 and sys.argv[1] == "sandbox":
        from .sandbox.server import main as sandbox_main

        sandbox_main()
        return
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactIngestToken) for f in access_log.filters):
        access_log.addFilter(RedactIngestToken())
    uvicorn.run(
        "vibonomics.app:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        proxy_headers=True,
        forwarded_allow_ips=_forwarded_allow_ips(),
    )


if __name__ == "__main__":
    main()
