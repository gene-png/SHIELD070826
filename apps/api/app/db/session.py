"""SQLAlchemy engine + session factory.

Sync engine for v1 (FastAPI runs handlers in a worker thread when they're
defined `def`). Switching to async is a v1.x candidate but not load-bearing
for v1.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _build_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def open_autonomous_session(bind: object | None = None) -> Session:
    """A short-lived session for an AUTONOMOUS transaction, independent of any
    request-scoped session (FIX E-2: the llm_calls audit trail must survive a
    request rollback).

    Reuses the module `SessionLocal`/engine — it does NOT build a second engine.
    `bind` pins it to a specific engine so the audit row lands in the same
    database the caller is using: production callers share the module engine,
    but the test suite overrides `get_db` with per-test engines, so we bind to
    the caller's engine via `Session.get_bind()`.

    `expire_on_commit=False` so the committed row's column values stay readable
    after this session closes — `invoke()` returns the row detached and callers
    (and JobResult.llm_call) read its fields without a live session.
    """
    if bind is not None:
        return SessionLocal(bind=bind, expire_on_commit=False)
    return SessionLocal(expire_on_commit=False)
