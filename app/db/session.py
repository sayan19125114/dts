"""SQLAlchemy engine/session with SQLite hardening: FK, WAL, busy timeout."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


def _apply_sqlite_pragmas(dbapi_conn, conn_record):
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        connect_args = {}
        if s.database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 10}
        _engine = create_engine(s.database_url, connect_args=connect_args, future=True)
        if s.database_url.startswith("sqlite"):
            event.listen(_engine, "connect", _apply_sqlite_pragmas)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)
    return _SessionLocal


def reset_engine():
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def get_db():
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
