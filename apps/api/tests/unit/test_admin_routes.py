"""Admin queue route + role-guard tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-admin.db"
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

    # Work Order B1: a client user can only self-register against a pre-approved
    # org domain. Seed a "(pending intake)" client + the example.com domain so
    # the second registrant in these tests auto-joins it.
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
        yield c


def _register(
    client: TestClient, email: str, password: str = "correct horse battery staple!"
) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.unit
def test_admin_queue_empty_on_fresh_deployment(app_client: TestClient) -> None:
    body = register_admin(app_client, "admin@example.com")
    bearer = body["tokens"]["access_token"]
    # First registrant becomes admin per D-004.
    assert body["user"]["role"] == "admin"
    r = app_client.get("/admin/intake-queue", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["client"] is None or payload["client"]["legal_name"] == "(pending intake)"
    assert payload["service_requests"] == []
    assert payload["artifacts"] == []
    assert payload["total_users"] == 1


@pytest.mark.unit
def test_admin_queue_reflects_submitted_intake(app_client: TestClient) -> None:
    admin_body = register_admin(app_client, "admin@example.com")
    admin_bearer = admin_body["tokens"]["access_token"]

    # Second registrant is role=client.
    client_body = _register(app_client, "client@example.com")
    assert client_body["user"]["role"] == "client"
    client_bearer = client_body["tokens"]["access_token"]

    r = app_client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {client_bearer}"},
        json={
            "client": {
                "legal_name": "Atlas Defense Solutions",
                "industry": "Defense",
                "address_line1": "123 Pentagon Way",
            },
            "service_requests": [
                {
                    "service_type": "nist_csf",
                    "notes": "Annual refresh.",
                    "csf_target_tier": 3,
                    "csf_profile": "MOD",
                },
                {"service_type": "consultation"},
            ],
            "title": "CISO",
        },
    )
    assert r.status_code == 200, r.text

    r = app_client.get("/admin/intake-queue", headers={"Authorization": f"Bearer {admin_bearer}"})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["client"]["legal_name"] == "Atlas Defense Solutions"
    assert payload["client"]["industry"] == "Defense"
    assert payload["intake_completed_at"] is not None
    assert len(payload["service_requests"]) == 2
    types = sorted(req["service_type"] for req in payload["service_requests"])
    assert types == ["consultation", "nist_csf"]
    # Client-supplied CSF targets round-trip to the admin queue.
    csf_row = next(req for req in payload["service_requests"] if req["service_type"] == "nist_csf")
    assert csf_row["csf_target_tier"] == 3
    assert csf_row["csf_profile"] == "MOD"
    # Each row carries the requester summary so the admin can see who asked.
    assert all(
        req["requested_by"]["email"] == "client@example.com" for req in payload["service_requests"]
    )
    assert payload["total_users"] == 2


@pytest.mark.unit
def test_admin_can_publish_service_request(app_client: TestClient) -> None:
    admin_bearer = register_admin(app_client, "admin@example.com")["tokens"]["access_token"]
    client_bearer = _register(app_client, "client@example.com")["tokens"]["access_token"]

    # tech_debt is NOT auto-provisioned at intake (only CSF/ZT are), so it still
    # goes through the manual admin publish flow.
    app_client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {client_bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [
                {"service_type": "tech_debt"},
                {"service_type": "consultation"},
            ],
        },
    )

    queue = app_client.get(
        "/admin/intake-queue", headers={"Authorization": f"Bearer {admin_bearer}"}
    ).json()
    by_type = {s["service_type"]: s for s in queue["service_requests"]}
    td_id = by_type["tech_debt"]["id"]
    con_id = by_type["consultation"]["id"]

    # Publishing the tech-debt request opens a live workspace.
    r = app_client.post(
        f"/admin/service-requests/{td_id}/fulfill",
        headers={"Authorization": f"Bearer {admin_bearer}"},
    )
    assert r.status_code == 200, r.text
    pub = r.json()
    assert pub["already_fulfilled"] is False
    assert pub["service_type"] == "tech_debt"
    service_id = pub["service_id"]

    # Idempotent: re-publishing returns the same workspace.
    r2 = app_client.post(
        f"/admin/service-requests/{td_id}/fulfill",
        headers={"Authorization": f"Bearer {admin_bearer}"},
    )
    assert r2.status_code == 200
    assert r2.json()["already_fulfilled"] is True
    assert r2.json()["service_id"] == service_id

    # Consultation requests can't be published as a service.
    r3 = app_client.post(
        f"/admin/service-requests/{con_id}/fulfill",
        headers={"Authorization": f"Bearer {admin_bearer}"},
    )
    assert r3.status_code == 400

    # The queue now correlates the request to its live service.
    queue2 = app_client.get(
        "/admin/intake-queue", headers={"Authorization": f"Bearer {admin_bearer}"}
    ).json()
    td_row = next(s for s in queue2["service_requests"] if s["service_type"] == "tech_debt")
    assert td_row["fulfilled_service_id"] == service_id

    # Non-admins cannot publish.
    r4 = app_client.post(
        f"/admin/service-requests/{td_id}/fulfill",
        headers={"Authorization": f"Bearer {client_bearer}"},
    )
    assert r4.status_code == 403


@pytest.mark.unit
def test_get_service_resolves_owning_client(app_client: TestClient) -> None:
    admin_bearer = register_admin(app_client, "admin@example.com")["tokens"]["access_token"]
    client_body = _register(app_client, "client@example.com")
    client_bearer = client_body["tokens"]["access_token"]
    client_id = client_body["user"]["client_id"]

    app_client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {client_bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [
                {"service_type": "nist_csf", "csf_target_tier": 3, "csf_profile": "MOD"},
            ],
        },
    )
    queue = app_client.get(
        "/admin/intake-queue", headers={"Authorization": f"Bearer {admin_bearer}"}
    ).json()
    csf_id = next(s["id"] for s in queue["service_requests"] if s["service_type"] == "nist_csf")
    service_id = app_client.post(
        f"/admin/service-requests/{csf_id}/fulfill",
        headers={"Authorization": f"Bearer {admin_bearer}"},
    ).json()["service_id"]

    # The workspace shell uses this lookup to discover the owning tenant.
    r = app_client.get(
        f"/admin/services/{service_id}",
        headers={"Authorization": f"Bearer {admin_bearer}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == service_id
    assert body["client_id"] == client_id
    assert body["kind"] == "nist_csf"

    # Unknown service id -> 404.
    assert (
        app_client.get(
            "/admin/services/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {admin_bearer}"},
        ).status_code
        == 404
    )

    # Non-admins cannot look up services.
    assert (
        app_client.get(
            f"/admin/services/{service_id}",
            headers={"Authorization": f"Bearer {client_bearer}"},
        ).status_code
        == 403
    )


@pytest.mark.unit
def test_ai_status_reports_fixture_mode(app_client: TestClient) -> None:
    admin_bearer = register_admin(app_client, "admin@example.com")["tokens"]["access_token"]
    client_bearer = _register(app_client, "client@example.com")["tokens"]["access_token"]

    # Tests run in fixture mode -> AI is not live-ready, and no key leaks.
    r = app_client.get("/admin/ai-status", headers={"Authorization": f"Bearer {admin_bearer}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "fixture"
    assert body["ready"] is False
    assert "api_key" not in body and "anthropic_api_key" not in body

    # Admin-only.
    assert (
        app_client.get(
            "/admin/ai-status", headers={"Authorization": f"Bearer {client_bearer}"}
        ).status_code
        == 403
    )


@pytest.mark.unit
def test_admin_queue_rejects_client_role_with_403(app_client: TestClient) -> None:
    register_admin(app_client, "admin@example.com")
    client_body = _register(app_client, "client@example.com")
    client_bearer = client_body["tokens"]["access_token"]
    r = app_client.get("/admin/intake-queue", headers={"Authorization": f"Bearer {client_bearer}"})
    assert r.status_code == 403


@pytest.mark.unit
def test_admin_queue_rejects_unauthenticated_with_401(app_client: TestClient) -> None:
    r = app_client.get("/admin/intake-queue")
    assert r.status_code == 401
