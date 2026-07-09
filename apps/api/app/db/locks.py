"""Per-entity run locks (FIX E-3).

Guards the read-modify-write window of the AI ``run-ai`` / ``generate`` routes
against concurrent double-click / two-tab invocations. Without this, two
overlapping ``run-ai`` calls interleave their row updates and return wrong
diffs, and two overlapping risk ``generate`` calls mint two registers with the
same version and corrupt the supersession chain.

Two layers are taken together, and BOTH survive FIX E-1's mid-request
``db.rollback()`` (the rollback that returns the pooled connection to the pool
before the slow provider call):

1. An in-process, non-reentrant mutex keyed by the entity id. This is the layer
   the test suite exercises (SQLite has no server-side advisory locks) and it
   protects a single worker process. It lives entirely in Python memory, so a
   DB rollback cannot touch it.

2. On PostgreSQL, a *session-scoped* advisory lock taken on a DEDICATED
   connection (never the request session's connection) so the guard holds
   across worker processes / hosts for true cross-process safety. We hold that
   private connection open for the whole request and explicitly unlock + close
   it in a ``finally`` block.

Why not ``pg_advisory_xact_lock`` (transaction-scoped), which the remediation
document suggested? FIX E-1 calls ``db.rollback()`` before the provider call to
release the pooled connection, and a TRANSACTION-scoped advisory lock is
released by that very rollback -- it would not cover the read-modify-write
window at all. A row lock (``SELECT ... FOR UPDATE``) is transaction scoped too,
so it has the identical problem. A session-scoped lock taken on the *request*
connection is also unsafe: after ``db.rollback()`` that connection is returned
to the pool, and a later unrelated request could check it out and inherit (or
fail to release) the lock. Taking the lock on a private, request-lifetime
connection is the only variant that both survives the rollback and never leaks
onto a pooled connection.

The lock connection comes from a SEPARATE ``NullPool`` engine, not from
``db.get_bind().connect()``. That distinction is the whole point of E-1: the
request engine's pool is 5 + 10 overflow and serves every other endpoint. If the
lock borrowed from it, each in-flight AI run would hold a pooled connection for
the entire provider call -- reinstating exactly the starvation E-1 removed, and
doing so invisibly, because the test suite runs on SQLite and never takes this
branch. A NullPool engine opens a private connection per lock and closes it on
release, so the request pool stays free for unrelated traffic.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


class RunInProgressError(Exception):
    """A concurrent run already holds the lock for this entity.

    Routes translate this into a typed HTTP 409 ("a run is already in
    progress"), so the loser of a double-click never corrupts the winner's
    read-modify-write.
    """


# In-process guard: a set of (namespace, id) currently running, protected by a
# module-level lock. Non-reentrant by design -- a second entrant for the same
# key fails immediately rather than blocking.
_guard = threading.Lock()
_active: set[tuple[str, str]] = set()


def _pg_key(namespace: str, ident: uuid.UUID) -> int:
    """Fold (namespace, uuid) into a signed 64-bit int for pg advisory locks."""
    digest = hashlib.blake2b(f"{namespace}:{ident}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


# One NullPool engine per database URL, created on first use. NullPool means the
# lock never borrows from -- or blocks on -- the request engine's pool.
_lock_engines: dict[str, Engine] = {}
_lock_engines_guard = threading.Lock()


def lock_engine(bind: Engine) -> Engine:
    """A dedicated, pool-less engine for advisory-lock connections."""
    url = str(bind.url)
    with _lock_engines_guard:
        engine = _lock_engines.get(url)
        if engine is None:
            engine = create_engine(bind.url, poolclass=NullPool)
            _lock_engines[url] = engine
        return engine


@contextmanager
def run_lock(db: Session, namespace: str, ident: uuid.UUID) -> Iterator[None]:
    """Hold a per-entity run lock for the duration of the ``with`` block.

    Raises :class:`RunInProgressError` immediately (non-blocking) if another
    run already holds the lock for ``(namespace, ident)``.
    """
    key = (namespace, str(ident))
    # Layer 1: in-process, non-blocking try-acquire. Raise BEFORE the try block
    # so the finally cleanup never discards another caller's active entry.
    with _guard:
        if key in _active:
            raise RunInProgressError("a run is already in progress")
        _active.add(key)

    raw = None
    pg_locked = False
    pg_key: int | None = None
    try:
        # Layer 2: PostgreSQL cross-process advisory lock on a dedicated
        # connection. Skipped (no-op) on SQLite and any non-Postgres dialect --
        # the test suite runs on SQLite and relies on layer 1 alone.
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            pg_key = _pg_key(namespace, ident)
            # NOT bind.connect(): that would check a connection out of the
            # request pool and hold it across the provider call (see module
            # docstring). lock_engine() is NullPool, so this is private.
            raw = lock_engine(bind).connect()
            pg_locked = bool(
                raw.exec_driver_sql("SELECT pg_try_advisory_lock(%s)", (pg_key,)).scalar()
            )
            if not pg_locked:
                raise RunInProgressError("a run is already in progress")
        yield
    finally:
        if raw is not None:
            if pg_locked:
                # Best-effort unlock; closing the connection frees it regardless.
                with contextlib.suppress(Exception):
                    raw.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (pg_key,))
            raw.close()
        with _guard:
            _active.discard(key)
