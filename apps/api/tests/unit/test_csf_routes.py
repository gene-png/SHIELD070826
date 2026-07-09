"""HTTP-level tests for the CSF 2.0 service routes."""

from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.csf.catalog import SUBCATEGORIES
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-csf.db"
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


def _open_service(c: TestClient, bearer: str, title: str = "NIST CSF") -> str:
    r = c.post(
        "/csf/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "nist_csf", "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _new_assessment(c: TestClient, bearer: str, svc_id: str) -> dict:
    r = c.post(
        f"/csf/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.unit
def test_catalog_endpoint_returns_106_subcategories(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    r = c.get("/csf/catalog", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200
    body = r.json()
    assert body["total_subcategories"] == 106
    assert len(body["functions"]) == 6
    assert len(body["tiers"]) == 4
    # Spot-check the structure of the first function.
    gv = body["functions"][0]
    assert gv["code"] == "GV"
    assert gv["name"] == "GOVERN"
    sub_count = sum(len(cat["subcategories"]) for cat in gv["categories"])
    assert sub_count == 31


@pytest.mark.unit
def test_admin_can_open_csf_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    assert svc_id


@pytest.mark.unit
def test_client_cannot_open_csf_service(app_client) -> None:
    c = app_client
    register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer = client["tokens"]["access_token"]
    r = c.post(
        "/csf/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "nist_csf", "title": "x"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_create_assessment_seeds_106_empty_answers(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    body = _new_assessment(c, bearer, svc_id)
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert len(body["answers"]) == 106
    # All start unscored.
    assert all(a["maturity_tier"] is None for a in body["answers"])


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
    assert v1["id"] != v2["id"]


@pytest.mark.unit
def test_patch_answer_records_score_and_actor(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    # Pick the first answer.
    answer = a["answers"][0]
    r = c.patch(
        f"/csf/answers/{answer['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_tier": 3, "notes": "Verified via SSP."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["maturity_tier"] == 3
    assert body["notes"] == "Verified via SSP."
    assert body["answered_by"] is not None
    assert body["answered_at"] is not None


@pytest.mark.unit
def test_patch_answer_rejects_bad_tier(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    r = c.patch(
        f"/csf/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_tier": 99},
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_patch_answer_rejects_empty_body(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    r = c.patch(
        f"/csf/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={},
    )
    assert r.status_code == 400


@pytest.mark.unit
def test_patch_answer_404_for_unknown(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    r = c.patch(
        f"/csf/answers/{_uuid.uuid4()}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_tier": 2},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_patch_answer_rejects_client_role(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer_admin = admin["tokens"]["access_token"]
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin)
    a = _new_assessment(c, bearer_admin, svc_id)
    r = c.patch(
        f"/csf/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer_client}"},
        json={"maturity_tier": 2},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_approve_assessment_locks_edits(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    r = c.post(
        f"/csf/assessments/{a['id']}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    # Second approve is idempotent.
    r2 = c.post(
        f"/csf/assessments/{a['id']}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r2.status_code == 200
    # And edits now fail with 409.
    r3 = c.patch(
        f"/csf/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_tier": 4},
    )
    assert r3.status_code == 409


@pytest.mark.unit
def test_score_endpoint_aggregates_answers(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    a = _new_assessment(c, bearer, svc_id)
    # Answer all 106 with tier 3.
    for ans in a["answers"]:
        c.patch(
            f"/csf/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_tier": 3},
        )
    r = c.get(
        f"/csf/services/{svc_id}/score",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_subcategories"] == 106
    assert body["answered_subcategories"] == 106
    assert body["coverage_pct"] == 100.0
    assert body["average_tier"] == 3.0
    assert body["overall_maturity_label"] == "Repeatable"
    assert len(body["by_function"]) == 6


@pytest.mark.unit
def test_score_endpoint_404_when_no_assessment(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer)
    r = c.get(
        f"/csf/services/{svc_id}/score",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_latest_assessment_admin_only_until_released(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer_admin = admin["tokens"]["access_token"]
    client = _register(c, "client@example.com")
    c.headers["X-Client-Id"] = client["user"]["client_id"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin)
    _new_assessment(c, bearer_admin, svc_id)
    # Admin can read.
    r = c.get(
        f"/csf/services/{svc_id}/assessments/latest",
        headers={"Authorization": f"Bearer {bearer_admin}"},
    )
    assert r.status_code == 200
    # Client cannot until release (Phase 4 stage 9-equivalent path).
    r2 = c.get(
        f"/csf/services/{svc_id}/assessments/latest",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert r2.status_code == 403


@pytest.mark.unit
def test_create_assessment_rejects_non_csf_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    # Open a tech-debt service instead.
    r = c.post(
        "/tech-debt/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "tech_debt", "title": "x"},
    )
    td_svc_id = r.json()["id"]
    r2 = c.post(
        f"/csf/services/{td_svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r2.status_code == 404


@pytest.mark.unit
def test_catalog_subcategory_count_matches_module() -> None:
    # Defense-in-depth: the route adapter shouldn't drop subcategories.
    # The route test (test_catalog_endpoint_returns_106_subcategories)
    # asserts on the HTTP shape; this one asserts the module's truth.
    assert len(SUBCATEGORIES) == 106
