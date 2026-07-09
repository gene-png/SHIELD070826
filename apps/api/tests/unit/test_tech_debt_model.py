"""Smoke for Phase 3 stage 1 schema."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models import (
    CapabilityItem,
    CapabilityList,
    Client,
    Deliverable,
    Service,
    ServiceKind,
    ServiceStatus,
    User,
    UserRole,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture()
def migrated_sqlite(tmp_path) -> str:
    db_path = tmp_path / "shield-tech-debt.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


@pytest.mark.unit
def test_migration_creates_phase3_tables(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with engine.connect() as conn:
        names = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('services','capability_lists','capability_items','deliverables')"
            )
        }
    assert names == {"services", "capability_lists", "capability_items", "deliverables"}


@pytest.mark.unit
def test_service_capability_deliverable_roundtrip(migrated_sqlite: str) -> None:
    engine = create_engine(migrated_sqlite)
    with Session(engine) as db:
        admin = User(
            email="admin@example.com",
            password_hash="x" * 64,
            role=UserRole.ADMIN,
            display_name="Admin",
        )
        db.add(admin)
        db.flush()

        client = Client(legal_name="Atlas Defense")
        db.add(client)
        db.flush()

        svc = Service(
            kind=ServiceKind.TECH_DEBT,
            status=ServiceStatus.IN_PROGRESS,
            title="Atlas Defense — Tech Debt Review",
            client_id=client.id,
            opened_by=admin.id,
        )
        db.add(svc)
        db.flush()

        cap_list = CapabilityList(service_id=svc.id, version=1)
        db.add(cap_list)
        db.flush()

        item = CapabilityItem(
            capability_list_id=cap_list.id,
            name="Wiz",
            vendor="Wiz, Inc.",
            category="CNAPP",
            function="Cloud security posture management",
            annual_cost_usd=350000,
            license_count=200,
            confidence_pct=92,
        )
        db.add(item)
        db.commit()

        loaded_list = db.execute(select(CapabilityList)).scalar_one()
        loaded_item = db.execute(select(CapabilityItem)).scalar_one()
        assert loaded_list.service_id == svc.id
        assert loaded_list.version == 1
        assert loaded_item.name == "Wiz"
        assert float(loaded_item.annual_cost_usd) == 350000.0
        assert loaded_item.confidence_pct == 92

        deliv = Deliverable(service_id=svc.id, title="Tech Debt Review v1", version=1)
        db.add(deliv)
        db.commit()
        assert db.execute(select(Deliverable)).scalar_one().service_id == svc.id


@pytest.mark.unit
def test_capability_list_service_version_is_unique(migrated_sqlite: str) -> None:
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(migrated_sqlite)
    with Session(engine) as db:
        admin = User(
            email="a@example.com",
            password_hash="x" * 64,
            role=UserRole.ADMIN,
            display_name="A",
        )
        db.add(admin)
        db.flush()
        client = Client(legal_name="x co")
        db.add(client)
        db.flush()
        svc = Service(
            kind=ServiceKind.TECH_DEBT,
            title="x",
            client_id=client.id,
            opened_by=admin.id,
        )
        db.add(svc)
        db.flush()
        db.add(CapabilityList(service_id=svc.id, version=1))
        db.commit()
        db.add(CapabilityList(service_id=svc.id, version=1))
        with pytest.raises(IntegrityError):
            db.commit()
