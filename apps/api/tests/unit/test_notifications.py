"""Notification + intake-fan-out tests."""

from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.notification import Notification
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, sessionmaker]]:
    db_path = tmp_path / "shield-notif.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    test_engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    # Work Order B1: seed a "(pending intake)" client + approved domain so the
    # second (client-role) registrant auto-joins it.
    from app.models.client import Client as _Client
    from app.models.client_domain import ClientDomain as _ClientDomain

    _seed = TestSession()
    _tenant = _Client(legal_name="(pending intake)")
    _seed.add(_tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=_tenant.id, domain="example.com"))
    _seed.commit()
    _seed.close()

    with TestClient(app) as c:
        yield c, TestSession


def _register(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple!",
            "display_name": email.split("@")[0],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.unit
def test_intake_submit_writes_admin_notification(app_client) -> None:
    client_app, TestSession = app_client
    admin = register_admin(client_app, "admin@example.com")
    assert admin["user"]["role"] == "admin"
    poc = _register(client_app, "poc@example.com")

    r = client_app.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {poc['tokens']['access_token']}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions", "industry": "Defense"},
            "service_requests": [
                {"service_type": "nist_csf", "csf_target_tier": 3, "csf_profile": "MOD"},
                {"service_type": "consultation"},
            ],
        },
    )
    assert r.status_code == 200, r.text

    with TestSession() as db:
        rows = (
            db.execute(
                select(Notification).where(Notification.user_id == _uuid.UUID(admin["user"]["id"]))
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        n = rows[0]
        assert n.event_type == "intake.submitted"
        assert n.title == "New intake submitted"
        assert "Atlas Defense Solutions" in (n.body or "")
        assert "consultation" in (n.body or "") and "nist_csf" in (n.body or "")
        assert n.link == "/admin/queue"
        assert n.read_at is None


@pytest.mark.unit
def test_intake_submit_does_not_notify_client_role(app_client) -> None:
    """Only admins get the new-lead bell, not the submitter themselves."""
    client_app, TestSession = app_client
    register_admin(client_app, "admin@example.com")
    poc = _register(client_app, "poc@example.com")
    r = client_app.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {poc['tokens']['access_token']}"},
        json={
            "client": {"legal_name": "Atlas Defense"},
            "service_requests": [{"service_type": "consultation"}],
        },
    )
    assert r.status_code == 200
    with TestSession() as db:
        rows = (
            db.execute(
                select(Notification).where(Notification.user_id == _uuid.UUID(poc["user"]["id"]))
            )
            .scalars()
            .all()
        )
        assert rows == []


@pytest.mark.unit
def test_list_notifications_for_current_user(app_client) -> None:
    client_app, _ = app_client
    admin = register_admin(client_app, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    poc = _register(client_app, "poc@example.com")

    client_app.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {poc['tokens']['access_token']}"},
        json={
            "client": {"legal_name": "X"},
            "service_requests": [{"service_type": "consultation"}],
        },
    )

    r = client_app.get("/notifications", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200
    body = r.json()
    assert body["unread_count"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["event_type"] == "intake.submitted"
    assert body["items"][0]["link"] == "/admin/queue"


@pytest.mark.unit
def test_mark_read_clears_unread_count(app_client) -> None:
    client_app, _ = app_client
    admin = register_admin(client_app, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    poc = _register(client_app, "poc@example.com")

    client_app.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {poc['tokens']['access_token']}"},
        json={
            "client": {"legal_name": "X"},
            "service_requests": [{"service_type": "consultation"}],
        },
    )

    list_r = client_app.get("/notifications", headers={"Authorization": f"Bearer {bearer}"})
    n_id = list_r.json()["items"][0]["id"]

    r = client_app.post(
        f"/notifications/{n_id}/read", headers={"Authorization": f"Bearer {bearer}"}
    )
    assert r.status_code == 200
    assert r.json()["read_at"] is not None

    list_r = client_app.get("/notifications", headers={"Authorization": f"Bearer {bearer}"})
    assert list_r.json()["unread_count"] == 0


@pytest.mark.unit
def test_mark_other_users_notification_returns_404(app_client) -> None:
    client_app, _ = app_client
    admin = register_admin(client_app, "admin@example.com")
    poc = _register(client_app, "poc@example.com")
    client_app.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {poc['tokens']['access_token']}"},
        json={
            "client": {"legal_name": "X"},
            "service_requests": [{"service_type": "consultation"}],
        },
    )
    list_r = client_app.get(
        "/notifications", headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"}
    )
    n_id = list_r.json()["items"][0]["id"]
    # The POC user attempts to mark the admin's notification read.
    r = client_app.post(
        f"/notifications/{n_id}/read",
        headers={"Authorization": f"Bearer {poc['tokens']['access_token']}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_notification_routes_require_authentication(app_client) -> None:
    client_app, _ = app_client
    assert client_app.get("/notifications").status_code == 401
    assert client_app.post(f"/notifications/{_uuid.uuid4()}/read").status_code == 401
    assert client_app.post("/notifications/read-all").status_code == 401
