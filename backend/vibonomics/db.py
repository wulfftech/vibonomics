"""SQLite engine and session handling."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_config


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 60},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=60000")
        cur.close()

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    cfg = get_config()
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    return make_engine(cfg.db_path)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _request_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def get_session(session: Session = Depends(_request_session, scope="function")) -> Session:
    """FastAPI dependency.

    A dependency that yields is closed after the response has been sent unless it has function scope. Closing is
    where this one commits, so with the default scope a failed commit still reached the client as a 2xx and the
    rows were silently not stored.
    """
    return session


def init_db() -> None:
    from .migrations import upgrade_to_head

    upgrade_to_head(get_engine())
    db_path = get_config().db_path
    for suffix in ("", "-wal", "-shm"):
        path = db_path.with_name(db_path.name + suffix)
        if path.exists():
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
