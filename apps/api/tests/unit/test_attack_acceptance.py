"""Phase 5 acceptance gate: ATT&CK Coverage end-to-end.

Walks intake -> coverage edits -> approve -> finalize -> release ->
client downloads PDF + XLSX. Plus the same supersession + unreleased-
invisibility checks the other services have.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.attack.catalog import TECHNIQUES
from app.storage.local import LocalFilesystemStorage
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-attack-acc.db"
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


def _seed_and_finalize(c: TestClient, bearer: str) -> tuple[str, str]:
    """Open service, create assessment, mark 5 techniques covered,
    approve, finalize. Deliverables are admin-only (Work Order A1)."""
    sr = c.post(
        "/attack/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "attack_coverage", "title": "Atlas - ATT&CK Coverage"},
    )
    svc_id = sr.json()["id"]
    a = c.post(
        f"/attack/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assessment = a.json()
    # Pick 5 rows and set them covered. Cheap proxy for "real assessor work".
    for cov in assessment["coverage"][:5]:
        c.patch(
            f"/attack/coverage/{cov['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"status": "covered"},
        )
    c.post(
        f"/attack/assessments/{assessment['id']}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    fin = c.post(
        f"/attack/services/{svc_id}/deliverables/finalize",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert fin.status_code == 201, fin.text
    deliv_id = fin.json()["id"]
    return svc_id, deliv_id


@pytest.mark.unit
def test_phase5_attack_admin_only_deliverable(app_client) -> None:
    """Work Order A1: deliverables + heatmap are admin-only."""
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]

    svc_id, deliv_id = _seed_and_finalize(c, bearer_admin)

    # Admin reads the latest deliverable.
    latest = c.get(
        f"/attack/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert latest.status_code == 200
    target = latest.json()
    assert target["id"] == deliv_id
    assert "MITRE_ATTACK_Coverage" in target["pdf_filename"]

    # Admin downloads work.
    pdf = c.get(
        f"/artifacts/{target['pdf_artifact_id']}/download",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")

    # Client is blocked from the deliverable endpoint and artifact download.
    blocked = c.get(
        f"/attack/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert blocked.status_code == 403
    pdf_client = c.get(
        f"/artifacts/{target['pdf_artifact_id']}/download",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert pdf_client.status_code == 404

    # Heatmap stays admin-only.
    h = c.get(
        f"/attack/services/{svc_id}/heatmap",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert h.status_code == 403


@pytest.mark.unit
def test_finalize_requires_approved_assessment(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    sr = c.post(
        "/attack/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "attack_coverage", "title": "x"},
    )
    svc_id = sr.json()["id"]
    c.post(
        f"/attack/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    r = c.post(
        f"/attack/services/{svc_id}/deliverables/finalize",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 409


@pytest.mark.unit
def test_attack_latest_returns_newest_version(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer_admin = admin["tokens"]["access_token"]

    svc_id, v1_id = _seed_and_finalize(c, bearer_admin)
    fin2 = c.post(
        f"/attack/services/{svc_id}/deliverables/finalize",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    v2_id = fin2.json()["id"]
    assert v1_id != v2_id

    latest = c.get(
        f"/attack/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert latest.json()["id"] == v2_id
    assert latest.json()["version"] == 2


@pytest.mark.unit
def test_attack_deliverable_invisible_to_client(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]

    svc_id, _ = _seed_and_finalize(c, bearer_admin)
    latest = c.get(
        f"/attack/services/{svc_id}/deliverables/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    pdf = c.get(
        f"/artifacts/{latest.json()['pdf_artifact_id']}/download",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert pdf.status_code == 404


@pytest.mark.unit
def test_catalog_count_matches_constant() -> None:
    # Smoke - lock against accidental catalog regression.
    assert len(TECHNIQUES) >= 600
