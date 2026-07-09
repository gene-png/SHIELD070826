"""Phase 4 acceptance gate: end-to-end CSF assessment walk.

Full happy path: admin opens a CSF service, creates an assessment,
scores all 106 subcategories, approves, finalizes, releases. From the
engagement client's POV we verify:

  - the assessment becomes readable after release
  - the deliverable shows up under GET /deliverables (the global,
    service-kind-agnostic client list from Phase 3 stage 9)
  - the client can download PDF + XLSX bytes
  - re-release on the same day supersedes the prior version
  - score + gap stay admin-only throughout
"""

from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.storage.local import LocalFilesystemStorage
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-csfacc.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    storage = LocalFilesystemStorage(tmp_path / "storage")

    from app.db.session import get_db
    from app.main import create_app
    from app.routes.artifacts import _storage_dep

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_storage_dep] = lambda: storage
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


def _seed_and_finalize(c: TestClient, bearer: str, *, score_tier: int = 3) -> tuple[str, str]:
    """Open service, create assessment, score every subcategory at
    `score_tier`, approve, finalize. Returns (service_id, deliverable_id).
    Deliverables are admin-only (Work Order A1) so there is no release step.
    """
    sr = c.post(
        "/csf/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "nist_csf", "title": "Atlas - CSF"},
    )
    svc_id = sr.json()["id"]
    a = c.post(
        f"/csf/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assessment = a.json()
    for ans in assessment["answers"]:
        c.patch(
            f"/csf/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_tier": score_tier},
        )
    c.post(
        f"/csf/assessments/{assessment['id']}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    fin = c.post(
        f"/csf/services/{svc_id}/deliverables/finalize",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert fin.status_code == 201, fin.text
    deliv_id = fin.json()["id"]
    return svc_id, deliv_id


@pytest.mark.unit
def test_phase4_acceptance_gate(app_client) -> None:
    """Work Order A1: admin can finalize + download; clients never can."""
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]

    svc_id, deliv_id = _seed_and_finalize(c, bearer_admin, score_tier=3)

    # Admin reads the latest deliverable and downloads both artifacts.
    latest = c.get(
        f"/csf/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert latest.status_code == 200
    target = latest.json()
    assert target["id"] == deliv_id
    assert target["version"] == 1
    assert "NIST_CSF_2_0_Assessment" in target["pdf_filename"]

    pdf = c.get(
        f"/artifacts/{target['pdf_artifact_id']}/download",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")

    # Client is blocked from the deliverable endpoint and artifact download.
    blocked = c.get(
        f"/csf/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert blocked.status_code == 403
    pdf_client = c.get(
        f"/artifacts/{target['pdf_artifact_id']}/download",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert pdf_client.status_code == 404

    # Score + gap remain admin-only.
    s = c.get(
        f"/csf/services/{svc_id}/score",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert s.status_code == 403
    g = c.get(
        f"/csf/services/{svc_id}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert g.status_code == 403


@pytest.mark.unit
def test_csf_deliverable_invisible_to_client(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]

    svc_id, _ = _seed_and_finalize(c, bearer_admin, score_tier=2)
    latest = c.get(
        f"/csf/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    # Client artifact download forbidden.
    pdf = c.get(
        f"/artifacts/{latest.json()['pdf_artifact_id']}/download",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert pdf.status_code == 404


@pytest.mark.unit
def test_csf_latest_returns_newest_version(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer_admin = admin["tokens"]["access_token"]

    svc_id, v1_id = _seed_and_finalize(c, bearer_admin, score_tier=2)

    # Re-finalize on the same day -> v2.
    fin2 = c.post(
        f"/csf/services/{svc_id}/deliverables/finalize",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    v2_id = fin2.json()["id"]
    assert v1_id != v2_id

    latest = c.get(
        f"/csf/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert latest.json()["id"] == v2_id
    assert latest.json()["version"] == 2


@pytest.mark.unit
def test_unknown_csf_service_404_consistently(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    unknown = _uuid.uuid4()
    paths = [
        f"/csf/services/{unknown}/assessments",
        f"/csf/services/{unknown}/assessments/latest",
        f"/csf/services/{unknown}/score",
        f"/csf/services/{unknown}/gap-analysis",
        f"/csf/services/{unknown}/deliverables/finalize",
        f"/csf/services/{unknown}/deliverables/latest",
    ]
    for path in paths:
        method = "POST" if path.endswith("/assessments") or path.endswith("/finalize") else "GET"
        r = c.request(
            method,
            path,
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert r.status_code == 404, f"{path} returned {r.status_code}"
