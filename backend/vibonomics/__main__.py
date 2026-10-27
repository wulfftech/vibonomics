"""Entry point: ``python -m vibonomics`` (web app) or ``python -m vibonomics sandbox`` (code sandbox)."""

from __future__ import annotations

import logging
import sys

import uvicorn

from .config import get_config


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
    uvicorn.run(
        "vibonomics.app:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
