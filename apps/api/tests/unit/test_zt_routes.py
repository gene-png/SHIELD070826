"""HTTP-level tests for the Zero Trust routes.

Covers both frameworks (CISA + DoD) and the scoring + gap analytics.
"""

from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.client import Client
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-zt.db"
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
    # test client so these single-tenant-style tests resolve to it. Client-role
    # callers are pinned to their own client and ignore this header.
    seed = TestSession()
    from app.models.client_domain import ClientDomain as _ClientDomain

    tenant = Client(legal_name="Test Tenant")
    seed.add(tenant)
    seed.flush()
    seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    seed.commit()
    cid = str(tenant.id)
    seed.close()

    with TestClient(app, headers={"X-Client-Id": cid}) as c:
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


def _open_service(c: TestClient, bearer: str, kind: str) -> str:
    r = c.post(
        "/zt/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": kind, "title": f"Atlas - {kind}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _new_assessment(c: TestClient, bearer: str, svc_id: str) -> dict:
    r = c.post(
        f"/zt/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_catalog_returns_cisa_by_default(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    r = c.get(
        "/zt/catalog",
        headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["framework"] == "cisa_ztmm_2_0"
    assert body["total_capabilities"] == 37
    assert len(body["pillars"]) == 8


@pytest.mark.unit
def test_catalog_returns_dod_when_requested(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    r = c.get(
        "/zt/catalog?framework=dod_ztra",
        headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"},
    )
    body = r.json()
    assert body["framework"] == "dod_ztra"
    assert body["total_capabilities"] == 50
    assert len(body["pillars"]) == 7
    # DoD label vocabulary: 3 levels (Work Order A4).
    labels = {s["label"] for s in body["stages"]}
    assert labels == {"Not Started", "Target", "Advanced"}
    assert len(body["stages"]) == 3


@pytest.mark.unit
def test_catalog_returns_cisa_labels_for_cisa(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    r = c.get(
        "/zt/catalog?framework=cisa_ztmm_2_0",
        headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"},
    )
    labels = {s["label"] for s in r.json()["stages"]}
    assert {"Traditional", "Initial", "Advanced", "Optimal"} <= labels


# ---------------------------------------------------------------------------
# Services + assessments
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_admin_can_open_cisa_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    assert svc_id


@pytest.mark.unit
def test_admin_can_open_dod_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_dod")
    assert svc_id


@pytest.mark.unit
def test_open_service_rejects_non_zt_kind(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    r = c.post(
        "/zt/services",
        headers={"Authorization": f"Bearer {admin['tokens']['access_token']}"},
        json={"kind": "tech_debt", "title": "x"},
    )
    assert r.status_code == 400


@pytest.mark.unit
def test_client_cannot_open_zt_service(app_client) -> None:
    c = app_client
    register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    r = c.post(
        "/zt/services",
        headers={"Authorization": f"Bearer {client['tokens']['access_token']}"},
        json={"kind": "zero_trust_cisa", "title": "x"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_create_assessment_seeds_cisa_with_37_empty_answers(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    body = _new_assessment(c, bearer, svc_id)
    assert body["framework"] == "cisa_ztmm_2_0"
    assert body["status"] == "draft"
    assert len(body["answers"]) == 37
    assert all(a["maturity_stage"] is None for a in body["answers"])


@pytest.mark.unit
def test_create_assessment_seeds_dod_with_50_empty_answers(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_dod")
    body = _new_assessment(c, bearer, svc_id)
    assert body["framework"] == "dod_ztra"
    assert len(body["answers"]) == 50


@pytest.mark.unit
def test_create_assessment_idempotent_until_approved(app_client) -> None:
    # FIX E-3: the open-draft guard makes create idempotent — a second click
    # returns the SAME open draft. A new version is minted only after the current
    # draft is approved.
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    h = {"Authorization": f"Bearer {bearer}"}
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    v1 = _new_assessment(c, bearer, svc_id)
    v1_again = _new_assessment(c, bearer, svc_id)
    assert v1["version"] == 1
    assert v1_again["version"] == 1
    assert v1["id"] == v1_again["id"]
    assert c.post(f"/zt/assessments/{v1['id']}/approve", headers=h).status_code == 200
    v2 = _new_assessment(c, bearer, svc_id)
    assert v2["version"] == 2
    assert v2["id"] != v1["id"]


# ---------------------------------------------------------------------------
# Answer editing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_patch_answer_records_stage_and_actor(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    a = _new_assessment(c, bearer, svc_id)
    ans = a["answers"][0]
    r = c.patch(
        f"/zt/answers/{ans['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_stage": 3, "notes": "Validated by audit."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["maturity_stage"] == 3
    assert body["notes"] == "Validated by audit."
    assert body["answered_by"] is not None


@pytest.mark.unit
def test_patch_answer_rejects_bad_stage(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    a = _new_assessment(c, bearer, svc_id)
    r = c.patch(
        f"/zt/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_stage": 99},
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_patch_answer_rejects_empty(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    a = _new_assessment(c, bearer, svc_id)
    r = c.patch(
        f"/zt/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={},
    )
    assert r.status_code == 400


@pytest.mark.unit
def test_patch_answer_rejects_client_role(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin, "zero_trust_cisa")
    a = _new_assessment(c, bearer_admin, svc_id)
    r = c.patch(
        f"/zt/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer_client}"},
        json={"maturity_stage": 2},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_approve_locks_edits(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    a = _new_assessment(c, bearer, svc_id)
    r = c.post(
        f"/zt/assessments/{a['id']}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    # Edits 409 after approve.
    r2 = c.patch(
        f"/zt/answers/{a['answers'][0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_stage": 4},
    )
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Scoring + gap
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_score_endpoint_rolls_up_cisa(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    a = _new_assessment(c, bearer, svc_id)
    for ans in a["answers"]:
        c.patch(
            f"/zt/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_stage": 3},
        )
    r = c.get(
        f"/zt/services/{svc_id}/score",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    body = r.json()
    assert body["total_capabilities"] == 37
    assert body["answered_capabilities"] == 37
    assert body["average_stage"] == 3.0
    # CISA labels.
    assert body["overall_stage_label"] == "Advanced"
    assert len(body["by_pillar"]) == 8


@pytest.mark.unit
def test_score_endpoint_rolls_up_dod(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_dod")
    a = _new_assessment(c, bearer, svc_id)
    for ans in a["answers"]:
        c.patch(
            f"/zt/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_stage": 3},
        )
    r = c.get(
        f"/zt/services/{svc_id}/score",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    body = r.json()
    assert body["total_capabilities"] == 50
    assert body["average_stage"] == 3.0
    # DoD stage 3 ("Advanced") is the top of the 3-level scale -> 100%.
    assert body["overall_stage_label"] == "Advanced"
    assert body["maturity_pct"] == 100.0
    assert len(body["by_pillar"]) == 7


@pytest.mark.unit
def test_gap_endpoint_returns_prioritized_gaps(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    a = _new_assessment(c, bearer, svc_id)
    for ans in a["answers"][:5]:
        c.patch(
            f"/zt/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_stage": 1},
        )
    r = c.get(
        f"/zt/services/{svc_id}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    body = r.json()
    assert body["target_stage"] == 3
    assert body["total_gap_count"] == 5
    assert body["unscored_count"] == 32
    assert all(g["gap_size"] == 2 for g in body["gaps"])


@pytest.mark.unit
def test_gap_endpoint_admin_only(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]
    svc_id = _open_service(c, bearer_admin, "zero_trust_cisa")
    r = c.get(
        f"/zt/services/{svc_id}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_latest_assessment_admin_only_until_released(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    client = _register(c, "client@example.com")
    bearer_admin = admin["tokens"]["access_token"]
    bearer_client = client["tokens"]["access_token"]
    client_cid = client["user"]["client_id"]
    # Open the service inside the client's own tenant so it's the visibility
    # check (not tenant scoping) that gates the client out of the unreleased
    # assessment - otherwise they'd 404 on a service in a different tenant.
    admin_in_tenant = {
        "Authorization": f"Bearer {bearer_admin}",
        "X-Client-Id": client_cid,
    }
    r = c.post(
        "/zt/services",
        headers=admin_in_tenant,
        json={"kind": "zero_trust_cisa", "title": "Atlas - zero_trust_cisa"},
    )
    assert r.status_code == 201, r.text
    svc_id = r.json()["id"]
    r = c.post(f"/zt/services/{svc_id}/assessments", headers=admin_in_tenant)
    assert r.status_code == 201, r.text
    r = c.get(
        f"/zt/services/{svc_id}/assessments/latest",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_score_404_when_no_assessment(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id = _open_service(c, bearer, "zero_trust_cisa")
    r = c.get(
        f"/zt/services/{svc_id}/score",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_score_404_for_non_zt_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    td = c.post(
        "/tech-debt/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "tech_debt", "title": "x"},
    )
    r = c.get(
        f"/zt/services/{td.json()['id']}/score",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_unknown_assessment_404(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    r = c.post(
        f"/zt/assessments/{_uuid.uuid4()}/approve",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404
