"""The E-3 run lock must never borrow from the request connection pool.

Why this file exists: E-1 removed the pooled connection that the synchronous AI
call used to hold, so one slow provider call can no longer starve the pool for
every other user. E-3 then added a per-entity advisory lock held for the whole
request -- and the obvious implementation, ``db.get_bind().connect()``, would
check a connection out of that very pool and hold it across the provider call,
silently reinstating the starvation E-1 removed.

The test suite runs on SQLite, which takes the in-process-mutex branch and never
opens a lock connection at all. So the regression would be invisible: a green
``test_pooled_connection_released_across_provider_call`` while production held a
connection per in-flight run. These tests pin the invariant directly instead.
"""

from __future__ import annotations

import types
import uuid

import pytest
from app.db import locks
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


@pytest.mark.unit
def test_lock_engine_is_pool_less_and_cached_per_url() -> None:
    request_engine = create_engine("sqlite://")
    lock_a = locks.lock_engine(request_engine)

    assert lock_a is not request_engine, "lock must not reuse the request engine"
    assert isinstance(lock_a.pool, NullPool), (
        "lock engine must be NullPool: a pooled lock connection held across the "
        "provider call would reinstate the E-1 starvation"
    )
    # Cached, so we don't leak an engine per request.
    assert locks.lock_engine(request_engine) is lock_a


@pytest.mark.unit
def test_run_lock_never_calls_connect_on_the_request_bind(monkeypatch) -> None:
    """On PostgreSQL the lock connection must come from lock_engine(), not the bind."""
    calls: dict[str, int] = {"bind_connect": 0, "lock_engine": 0}

    class _Conn:
        def exec_driver_sql(self, *_args, **_kwargs):
            calls["lock_engine"] += 1
            return types.SimpleNamespace(scalar=lambda: True)

        def close(self) -> None:
            pass

    def _bind_connect():
        calls["bind_connect"] += 1
        raise AssertionError("run_lock called bind.connect() -- that borrows from the request pool")

    fake_bind = types.SimpleNamespace(
        dialect=types.SimpleNamespace(name="postgresql"),
        url="postgresql://stub/stub",
        connect=_bind_connect,
    )
    fake_db = types.SimpleNamespace(get_bind=lambda: fake_bind)

    monkeypatch.setattr(locks, "lock_engine", lambda _bind: types.SimpleNamespace(connect=_Conn))

    with locks.run_lock(fake_db, "csf", uuid.uuid4()):
        pass

    assert calls["bind_connect"] == 0
    assert calls["lock_engine"] >= 1, "the postgres branch never ran; test is vacuous"


@pytest.mark.unit
def test_run_lock_second_entrant_is_rejected_not_blocked() -> None:
    """A double-click must fail fast with RunInProgressError, never deadlock."""
    engine = create_engine("sqlite://")
    db = types.SimpleNamespace(get_bind=lambda: engine)
    ident = uuid.uuid4()

    # The second entrant for the same key must fail fast rather than block.
    with (
        locks.run_lock(db, "csf", ident),
        pytest.raises(locks.RunInProgressError),
        locks.run_lock(db, "csf", ident),
    ):
        pass

    # Released on exit: the same key may be acquired again.
    with locks.run_lock(db, "csf", ident):
        pass
