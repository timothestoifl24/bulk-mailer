"""Database engine and session handling.

Works on SQLite (zero-setup default) and PostgreSQL (multi-instance
deployments). The only dialect-specific code lives here; everything above uses
plain SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

IS_SQLITE = settings.database_url.startswith("sqlite")

_engine_kwargs: dict[str, object] = {"pool_pre_ping": True, "future": True}
if IS_SQLITE:
    # The sending worker runs in its own thread and shares this engine.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # A web process plus the sender thread; keep the pool small and recycle
    # connections so a proxy or server-side idle timeout cannot hand us a
    # dead socket mid-campaign.
    _engine_kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)

engine = create_engine(settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover
    """WAL + a busy timeout so the worker thread and the web app can coexist.

    Also enables foreign keys, which SQLite leaves off by default - the
    ON DELETE rules on campaign_recipients and list_members depend on them.
    PostgreSQL needs none of this.
    """
    if not IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for use outside of request handling (worker, CLI)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
