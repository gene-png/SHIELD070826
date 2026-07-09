"""End-to-end smoke for migrations + models + audit immutability against SQLite.

The real prod target is Postgres - the trigger that enforces append-only
audit_entries is pg-only. These tests verify:
  - The Alembic migration applies cleanly on a brand-new database.
  - The ORM model graph round-trips a basic insert.
  - The Python-side audit immutability guard fires on UPDATE and DELETE
    attempts (SQLAlchemy event listener; redundant with the pg trigger,
    but catches the case where a logic bug bypasses the trigger).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models import AuditEntry, Client, User, UserRole
from app.models.audit_entry import AuditEntryImmutableError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture()
def migrated_sqlite(tmp_path) -> str:
    """Apply migrations to a fresh SQLite file; yield its URL."""
    db_path = tmp_path / "shield-test.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url

    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


@pytest.mark.unit
def test_migration_creates_all_three_tables(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with engine.connect() as conn:
        names = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'alembic%'"
            )
        }
    assert {"client", "users", "audit_entries"} <= names


@pytest.mark.unit
def test_orm_roundtrip_user_and_audit(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with Session(engine) as db:
        u = User(
            email="admin@example.com",
            password_hash="x" * 64,
            role=UserRole.ADMIN,
            display_name="Admin Zero",
        )
        db.add(u)
        db.flush()
        e = AuditEntry(action="user.created", target_type="user", target_id=u.id)
        db.add(e)
        db.commit()

        loaded = db.get(User, u.id)
        assert loaded is not None
        assert loaded.role == UserRole.ADMIN
        assert loaded.is_active is True
        assert loaded.mfa_enrolled is False


@pytest.mark.unit
def test_audit_entries_reject_update_at_app_layer(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with Session(engine) as db:
        e = AuditEntry(action="x.created", target_type="x", target_id=uuid.uuid4())
        db.add(e)
        db.commit()
        e.action = "x.tampered"
        with pytest.raises(AuditEntryImmutableError):
            db.commit()


@pytest.mark.unit
def test_audit_entries_reject_delete_at_app_layer(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with Session(engine) as db:
        e = AuditEntry(action="x.created", target_type="x", target_id=uuid.uuid4())
        db.add(e)
        db.commit()
        db.delete(e)
        with pytest.raises(AuditEntryImmutableError):
            db.commit()


@pytest.mark.unit
def test_client_singleton_can_be_created(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with Session(engine) as db:
        c = Client(legal_name="Atlas Defense Solutions")
        db.add(c)
        db.commit()
        assert c.id is not None
