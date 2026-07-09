"""End-to-end /intake route tests against an ephemeral SQLite + FastAPI TestClient."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit_entry import AuditEntry
from app.models.client import Client
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, sessionmaker]]:
    db_path = tmp_path / "shield-intake-rt.db"
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

    with TestClient(app) as c:
        yield c, TestSession


def _register_and_bearer(client: TestClient) -> str:
    # Seed a platform admin (self-registration only makes client users now).
    # Under Work Order B1 a client can only self-register against a pre-approved
    # org domain, so the admin first creates the org + approves "example.com",
    # then the client-role user registers and auto-joins it.
    admin = register_admin(client, "admin@example.com")
    admin_bearer = admin["tokens"]["access_token"]
    created = client.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"legal_name": "(pending intake)"},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    dom = client.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"domain": "example.com"},
    )
    assert dom.status_code == 201, dom.text
    r = client.post(
        "/auth/register",
        json={
            "email": "poc@example.com",
            "password": "correct horse battery staple!",
            "display_name": "POC",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["tokens"]["access_token"]


@pytest.mark.unit
def test_get_intake_creates_singleton_client(app_client) -> None:
    client, _ = app_client
    bearer = _register_and_bearer(client)
    r = client.get("/intake", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client"]["legal_name"] == "(pending intake)"
    assert body["service_requests"] == []
    assert body["intake_completed_at"] is None


@pytest.mark.unit
def test_patch_intake_updates_client_and_profile(app_client) -> None:
    client, _ = app_client
    bearer = _register_and_bearer(client)
    r = client.patch(
        "/intake",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {
                "legal_name": "Atlas Defense Solutions",
                "industry": "Defense",
                "size_band": "501-1000",
            },
            "title": "CISO",
            "phone": "+1-555-0123",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client"]["legal_name"] == "Atlas Defense Solutions"
    assert body["client"]["industry"] == "Defense"
    assert body["client"]["size_band"] == "501-1000"


@pytest.mark.unit
def test_submit_intake_writes_service_requests_and_audit(app_client) -> None:
    client, TestSession = app_client
    bearer = _register_and_bearer(client)

    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {
                "legal_name": "Atlas Defense Solutions",
                "industry": "Defense",
                "address_line1": "123 Pentagon Way",
                "city": "Arlington",
                "state": "VA",
                "country": "US",
            },
            "service_requests": [
                {
                    "service_type": "nist_csf",
                    "notes": "Annual assessment refresh.",
                    "csf_target_tier": 3,
                    "csf_profile": "MOD",
                },
                {"service_type": "zero_trust_cisa", "zt_target_stage": 3},
            ],
            "title": "CISO",
            "phone": "+1-555-0123",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intake_completed_at"] is not None
    assert len(body["service_requests"]) == 2
    types = sorted(req["service_type"] for req in body["service_requests"])
    assert types == ["nist_csf", "zero_trust_cisa"]

    # Audit row written.
    with TestSession() as db:
        rows = (
            db.execute(select(AuditEntry).where(AuditEntry.action == "client.intake_submitted"))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].details["services"] == ["nist_csf", "zero_trust_cisa"]

        c_row = db.execute(select(Client)).scalar_one()
        assert c_row.intake_completed_at is not None
        assert c_row.legal_name == "Atlas Defense Solutions"


@pytest.mark.unit
def test_submit_rejects_empty_service_requests(app_client) -> None:
    client, _ = app_client
    bearer = _register_and_bearer(client)
    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [],
        },
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_submit_rejects_pending_placeholder_legal_name(app_client) -> None:
    client, _ = app_client
    bearer = _register_and_bearer(client)
    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {"legal_name": "(pending intake)"},
            "service_requests": [{"service_type": "consultation"}],
        },
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_submit_dedupes_duplicate_service_requests(app_client) -> None:
    client, _ = app_client
    bearer = _register_and_bearer(client)
    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [
                {"service_type": "nist_csf", "csf_target_tier": 3, "csf_profile": "MOD"},
                {"service_type": "nist_csf", "csf_target_tier": 3, "csf_profile": "MOD"},
                {"service_type": "consultation"},
            ],
        },
    )
    assert r.status_code == 200
    assert len(r.json()["service_requests"]) == 2


@pytest.mark.unit
def test_submit_requires_csf_and_zt_targets(app_client) -> None:
    client, _ = app_client
    bearer = _register_and_bearer(client)

    # NIST CSF without a target tier + profile is rejected.
    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [{"service_type": "nist_csf"}],
        },
    )
    assert r.status_code == 422

    # Zero Trust without a target stage is rejected.
    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [{"service_type": "zero_trust_dod"}],
        },
    )
    assert r.status_code == 422

    # With targets supplied, the same services are accepted and persisted.
    r = client.post(
        "/intake/submit",
        headers={"Authorization": f"Bearer {bearer}"},
        json={
            "client": {"legal_name": "Atlas Defense Solutions"},
            "service_requests": [
                {"service_type": "nist_csf", "csf_target_tier": 4, "csf_profile": "HIGH"},
                {"service_type": "zero_trust_dod", "zt_target_stage": 2},
            ],
        },
    )
    assert r.status_code == 200, r.text
    by_type = {s["service_type"]: s for s in r.json()["service_requests"]}
    assert by_type["nist_csf"]["csf_target_tier"] == 4
    assert by_type["nist_csf"]["csf_profile"] == "HIGH"
    assert by_type["zero_trust_dod"]["zt_target_stage"] == 2


@pytest.mark.unit
def test_intake_requires_authentication(app_client) -> None:
    client, _ = app_client
    r = client.get("/intake")
    assert r.status_code == 401
    r = client.patch("/intake", json={})
    assert r.status_code == 401
    r = client.post(
        "/intake/submit",
        json={
            "client": {"legal_name": "X"},
            "service_requests": [{"service_type": "consultation"}],
        },
    )
    assert r.status_code == 401
