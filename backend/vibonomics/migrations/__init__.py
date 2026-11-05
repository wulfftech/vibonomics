"""Alembic migrations, run programmatically at startup (no alembic.ini needed).

Create a new revision after changing ``orm.py``::

    python -m vibonomics.migrations revision "describe the change"
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine

_HERE = Path(__file__).resolve().parent


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_HERE))
    return cfg


@contextmanager
def _foreign_keys_off(engine: Engine) -> Iterator[Connection]:
    """A connection with SQLite's foreign keys off, for a migration to run on.

    SQLite changes a column by building a new table, copying the rows, dropping the old table and renaming the new
    one. With foreign keys on, the drop deletes the rows of every table that cascades from the old one, so a rebuild
    of ``provider_configs`` would empty ``models``. SQLite ignores the pragma once a transaction is open, so it is set
    here, before the migration's transaction begins, and put back afterwards for the pool's next user.
    """
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar() != 0:
            raise RuntimeError("SQLite foreign keys could not be turned off, so the migration was not run")
        connection.commit()
        try:
            yield connection
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _check_foreign_keys(connection: Connection) -> None:
    """Refuse a migration that left a row pointing at nothing; raising rolls it back."""
    broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if broken:
        shown = ", ".join(f"{table} row {rowid} -> {parent}" for table, rowid, parent, _fk in broken[:10])
        raise RuntimeError(f"The migration left {len(broken)} row(s) with a broken foreign key ({shown}); rolled back")


def _upgrade(connection: Connection, cfg: Config) -> None:
    before = _revision(connection)
    command.upgrade(cfg, "head")
    # With enforcement off, only this check would notice. It runs when a migration was applied, so a database that
    # already held a stray row is not refused at every start.
    if _revision(connection) != before:
        _check_foreign_keys(connection)


def upgrade_to_head(engine: Engine) -> None:
    cfg = _alembic_config()
    with _foreign_keys_off(engine) as connection, connection.begin():
        cfg.attributes["connection"] = connection
        _upgrade(connection, cfg)


def _main(argv: list[str]) -> None:
    from ..db import get_engine

    if len(argv) >= 2 and argv[0] == "revision":
        cfg = _alembic_config()
        with _foreign_keys_off(get_engine()) as connection, connection.begin():
            cfg.attributes["connection"] = connection
            _upgrade(connection, cfg)
            command.revision(cfg, message=argv[1], autogenerate=True)
    else:
        print('usage: python -m vibonomics.migrations revision "message"')


if __name__ == "__main__":
    _main(sys.argv[1:])
