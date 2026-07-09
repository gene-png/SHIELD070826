"""HTTP-level tests for the MITRE ATT&CK Coverage routes."""

from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.attack.catalog import TACTICS, TECHNIQUES
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-attack.db"
    url = f"sqlite:///{db_path}"
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

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    # Multi-tenant (post-0013): admin/reviewer callers must name an active
    # tenant via X-Client-Id. Seed one tenant and bake the header into the
    # test client so single-tenant-style tests resolve to it; client-role
    # callers are pinned to their own client and ignore this header.
    from app.models.client import Client as _Client

    _seed = TestSession()
    _tenant = _Client(legal_name="Test Tenant")
    _seed.add(_tenant)
    _seed.flush()
    from app.models.client_domain import ClientDomain as _ClientDomain

    _seed.add(_ClientDomain(client_id=_tenant.id, domain="example.com"))
    _seed.commit()
    _cid = str(_tenant.id)
    _seed.close()

    with TestClient(app, headers={"X-Client-Id": _cid}) as c:
        yield c


def _register(c: TestClient, email: str) -> dict:
    r = c.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple!",
            "display_name": email.split("@")[0],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _open_service(c: TestClient, bearer: str) -> str:
    r = c.post(
        "/attack/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "attack_coverage", "title": "Atlas - ATT&CK Coverage"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _new_assessment(c: TestClient, bearer: str, svc_id: str) -> dict:
    r = c.post(
        f"/attack/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_catalog_returns_full_matrix(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    r = c.get(
        "/attack/catalog",
        headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["tactics"]) == 14
    assert len(body["techniques"]) >= 600
    assert body["total_techniques"] >= 150
    assert body["total_sub_techniques"] >= 350
    assert len(body["coverage_definitions"]) == 4


# ---------------------------------------------------------------------------
# Services + assessments
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_admin_can_open_attack_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    assert svc_id


@pytest.mark.unit
def test_client_cannot_open_attack_service(app_client) -> None:
    c = app_client
    register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    r = c.post(
        "/attack/services",
        headers={"Authorization": f"Bearer {client['tokens']['access_token']}"},
        json={"kind": "attack_coverage", "title": "x"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_open_service_rejects_non_attack_kind(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    r = c.post(
        "/attack/services",
        headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"},
        json={"kind": "tech_debt", "title": "x"},
    )
    assert r.status_code == 400


@pytest.mark.unit
def test_create_assessment_seeds_full_catalog_unscored(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    body = _new_assessment(c, bearer, svc_id)
    assert body["status"] == "draft"
    assert len(body["coverage"]) == len(TECHNIQUES)
    assert all(r["status"] is None for r in body["coverage"])


@pytest.mark.unit
def test_create_assessment_increments_version(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    v1 = _new_assessment(c, bearer, svc_id)
    v2 = _new_assessment(c, bearer, svc_id)
    assert v1["version"] == 1
    assert v2["version"] == 2


# ---------------------------------------------------------------------------
# Coverage editing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_patch_coverage_records_status(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    cov = a["coverage"][0]
    r = c.patch(
        f"/attack/coverage/{cov['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"status": "covered", "notes": "EDR + SIEM detection deployed."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "covered"
    assert body["notes"] == "EDR + SIEM detection deployed."
    assert body["answered_by"] is not None


@pytest.mark.unit
def test_patch_coverage_rejects_bad_status(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    r = c.patch(
        f"/attack/coverage/{a['coverage'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"status": "nope"},
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_patch_coverage_rejects_empty(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    r = c.patch(
        f"/attack/coverage/{a['coverage'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={},
    )
    assert r.status_code == 400


@pytest.mark.unit
def test_patch_coverage_rejects_client_role(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin)
    a = _new_assessment(c, bearer_admin, svc_id)
    r = c.patch(
        f"/attack/coverage/{a['coverage'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer_client}"},
        json={"status": "covered"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_approve_locks_edits(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    r = c.post(
        f"/attack/assessments/{a['id']}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    r2 = c.patch(
        f"/attack/coverage/{a['coverage'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"status": "covered"},
    )
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_heatmap_includes_every_tactic(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    _new_assessment(c, bearer, svc_id)
    r = c.get(
        f"/attack/services/{svc_id}/heatmap",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["by_tactic"]) == 14
    # All-unscored heatmap.
    assert body["scored_count"] == 0
    assert body["unscored_count"] == len(TECHNIQUES)
    assert body["coverage_pct"] == 0.0


@pytest.mark.unit
def test_heatmap_reflects_coverage_after_patches(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    # Cover the first 10 techniques.
    for cov in a["coverage"][:10]:
        c.patch(
            f"/attack/coverage/{cov['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"status": "covered"},
        )
    r = c.get(
        f"/attack/services/{svc_id}/heatmap",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    body = r.json()
    assert body["covered"] == 10
    assert body["scored_count"] == 10
    # All 10 covered -> coverage_pct = 100 on addressable (no gaps/partial).
    assert body["coverage_pct"] == 100.0


@pytest.mark.unit
def test_heatmap_admin_only(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin)
    r = c.get(
        f"/attack/services/{svc_id}/heatmap",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_heatmap_404_for_non_attack_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    td = c.post(
        "/tech-debt/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "tech_debt", "title": "x"},
    )
    r = c.get(
        f"/attack/services/{td.json()['id']}/heatmap",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_heatmap_404_when_no_assessment(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    r = c.get(
        f"/attack/services/{svc_id}/heatmap",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_latest_assessment_admin_only_until_released(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin)
    _new_assessment(c, bearer_admin, svc_id)
    r = c.get(
        f"/attack/services/{svc_id}/assessments/latest",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_unknown_assessment_404(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    r = c.post(
        f"/attack/assessments/{_uuid.uuid4()}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_total_tactics_equals_catalog() -> None:
    """Lock the route output against catalog drift."""
    assert len(TACTICS) == 14
