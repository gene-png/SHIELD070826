"""Admin audit log viewer: GET /admin/audit (FIX H-7).

Covers the role gate (client-role user is rejected), the filters, pagination,
CSV export, and - critically - that the read-only endpoint performs no writes
against the append-only audit_entries table.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from app.audit import audit
from app.models.audit_entry import AuditEntry
from app.models.client import Client
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def admin_app(tmp_path) -> Iterator[SimpleNamespace]:
    url = f"sqlite:///{tmp_path / 'shield-audit.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app
    from app.models.client_domain import ClientDomain

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    seed = TestSession()
    tenant = Client(legal_name="(pending intake)")
    seed.add(tenant)
    seed.flush()
    seed.add(ClientDomain(client_id=tenant.id, domain="example.com"))
    seed.commit()
    seed.close()

    with TestClient(app) as c:
        yield SimpleNamespace(client=c, Session=TestSession)


def _admin_headers(c: TestClient) -> dict:
    tok = register_admin(c, "admin@example.com")["tokens"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _seed_entries(app: SimpleNamespace) -> dict:
    """Write a spread of audit rows directly via the blessed audit() helper.

    Returns handles the tests assert against.
    """
    actor = uuid.uuid4()
    other_actor = uuid.uuid4()
    client_a = uuid.uuid4()
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    with app.Session() as db:
        # A client-targeted row (used by the client_id filter + known action).
        audit(
            db,
            action="client.created",
            target_type="client",
            target_id=client_a,
            actor_user_id=actor,
            details={"legal_name": "Acme"},
        )
        # A user event by the same actor.
        audit(
            db,
            action="user.created",
            target_type="user",
            target_id=uuid.uuid4(),
            actor_user_id=actor,
            details={"role": "client"},
        )
        # A login event by a different actor.
        audit(
            db,
            action="user.login",
            target_type="user",
            target_id=uuid.uuid4(),
            actor_user_id=other_actor,
        )
        db.commit()
        # Stamp deterministic timestamps so the date-range filter is testable.
        # The ORM before_flush guard blocks AuditEntry updates, so bypass the
        # unit-of-work with a Core UPDATE (test-harness seeding only, never the
        # route). SQLite has no append-only trigger, so this succeeds.
        rows = db.execute(select(AuditEntry).order_by(AuditEntry.action)).scalars().all()
        for i, r in enumerate(rows):
            db.execute(
                AuditEntry.__table__.update()
                .where(AuditEntry.__table__.c.id == r.id)
                .values(at=base + timedelta(days=i))
            )
        db.commit()
    return {
        "actor": actor,
        "other_actor": other_actor,
        "client_a": client_a,
        "base": base,
    }


@pytest.mark.unit
def test_audit_requires_admin_role(admin_app) -> None:
    """A client-role token is rejected with 403 (the role gate)."""
    reg = admin_app.client.post(
        "/auth/register",
        json={
            "email": "client@example.com",
            "password": "correct horse battery staple!",
            "display_name": "client",
        },
    )
    assert reg.status_code == 201, reg.text
    client_tok = reg.json()["tokens"]["access_token"]
    resp = admin_app.client.get("/admin/audit", headers={"Authorization": f"Bearer {client_tok}"})
    assert resp.status_code == 403


@pytest.mark.unit
def test_audit_lists_known_action(admin_app) -> None:
    _seed_entries(admin_app)
    h = _admin_headers(admin_app.client)
    body = admin_app.client.get("/admin/audit", headers=h).json()
    actions = {r["action"] for r in body["rows"]}
    # A known state-changing action appears in the trail.
    assert "client.created" in actions
    assert body["total"] >= 3
    # Newest first: timestamps are non-increasing down the page.
    ats = [r["at"] for r in body["rows"]]
    assert ats == sorted(ats, reverse=True)


@pytest.mark.unit
def test_audit_filters_by_action_and_actor(admin_app) -> None:
    handles = _seed_entries(admin_app)
    h = _admin_headers(admin_app.client)

    by_action = admin_app.client.get(
        "/admin/audit", params={"action": "user.login"}, headers=h
    ).json()
    assert by_action["total"] == 1
    assert by_action["rows"][0]["action"] == "user.login"

    by_actor = admin_app.client.get(
        "/admin/audit", params={"actor_id": str(handles["actor"])}, headers=h
    ).json()
    assert by_actor["total"] == 2
    assert {r["action"] for r in by_actor["rows"]} == {
        "client.created",
        "user.created",
    }


@pytest.mark.unit
def test_audit_filters_by_client_and_date_range(admin_app) -> None:
    handles = _seed_entries(admin_app)
    h = _admin_headers(admin_app.client)

    by_client = admin_app.client.get(
        "/admin/audit", params={"client_id": str(handles["client_a"])}, headers=h
    ).json()
    assert by_client["total"] == 1
    assert by_client["rows"][0]["target_type"] == "client"
    assert by_client["rows"][0]["target_id"] == str(handles["client_a"])

    # Only the first-day row falls at/under this end bound.
    end = (handles["base"]).isoformat()
    windowed = admin_app.client.get("/admin/audit", params={"end": end}, headers=h).json()
    assert windowed["total"] == 1


@pytest.mark.unit
def test_audit_pagination(admin_app) -> None:
    handles = _seed_entries(admin_app)
    h = _admin_headers(admin_app.client)
    # Scope to one actor (2 seeded rows) so registering the admin - which itself
    # writes a user.created audit row - doesn't shift the paging math.
    actor = str(handles["actor"])
    first = admin_app.client.get(
        "/admin/audit", params={"actor_id": actor, "limit": 1, "offset": 0}, headers=h
    ).json()
    assert first["limit"] == 1
    assert first["offset"] == 0
    assert first["total"] == 2
    assert len(first["rows"]) == 1

    second = admin_app.client.get(
        "/admin/audit", params={"actor_id": actor, "limit": 1, "offset": 1}, headers=h
    ).json()
    assert len(second["rows"]) == 1
    # Pages don't overlap.
    assert second["rows"][0]["id"] != first["rows"][0]["id"]


@pytest.mark.unit
def test_audit_csv_export(admin_app) -> None:
    _seed_entries(admin_app)
    h = _admin_headers(admin_app.client)
    resp = admin_app.client.get("/admin/audit", params={"format": "csv"}, headers=h)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    text = resp.text
    assert text.splitlines()[0].startswith("at,action,actor_user_id")
    assert "client.created" in text


@pytest.mark.unit
def test_audit_endpoint_performs_no_writes(admin_app) -> None:
    """The viewer must never write: row count is unchanged across every call."""
    _seed_entries(admin_app)
    h = _admin_headers(admin_app.client)

    def count() -> int:
        with admin_app.Session() as db:
            return db.execute(select(func.count()).select_from(AuditEntry)).scalar_one()

    # register_admin logs a user.created/login; capture the count AFTER auth.
    before = count()
    admin_app.client.get("/admin/audit", headers=h)
    admin_app.client.get("/admin/audit", params={"action": "client.created"}, headers=h)
    admin_app.client.get("/admin/audit", params={"format": "csv"}, headers=h)
    admin_app.client.get("/admin/audit", params={"limit": 1, "offset": 1}, headers=h)
    after = count()
    assert after == before
