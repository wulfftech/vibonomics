"""Alembic migrations, run programmatically at startup (no alembic.ini needed).

Create a new revision after changing ``orm.py``::

    python -m vibonomics.migrations revision "describe the change"
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

_HERE = Path(__file__).resolve().parent


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_HERE))
    return cfg


def upgrade_to_head(engine: Engine) -> None:
    cfg = _alembic_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def _main(argv: list[str]) -> None:
    from ..db import get_engine

    if len(argv) >= 2 and argv[0] == "revision":
        cfg = _alembic_config()
        with get_engine().begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
            command.revision(cfg, message=argv[1], autogenerate=True)
    else:
        print('usage: python -m vibonomics.migrations revision "message"')


if __name__ == "__main__":
    _main(sys.argv[1:])
