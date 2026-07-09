"""Authorization + tenant isolation for the D4/E/C7 surfaces (Work Order F).

Locks in the security posture of the routes added this cycle:
  - Risk Register routes are admin-only (client-role users are forbidden).
  - A CSF dimension-score row can only be patched within its own tenant.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.csf.catalog import SUBCATEGORIES
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def c(tmp_path) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'shield-authz.db'}"
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
    with TestClient(app) as client:
        yield client


def _admin(c: TestClient) -> str:
    r = register_admin_resp(c, "admin@kentro.example")
    return r.json()["tokens"]["access_token"]


def _make_client(c: TestClient, admin: str, name: str, domain: str) -> str:
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {admin}"},
        json={"legal_name": name},
    ).json()["id"]
    c.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {admin}"},
        json={"domain": domain},
    )
    return cid


@pytest.mark.unit
def test_risk_routes_forbidden_for_client_role(c: TestClient) -> None:
    admin = _admin(c)
    cid = _make_client(c, admin, "Acme", "acme.example")
    # A client-role user registered under the approved domain.
    client_user = c.post(
        "/auth/register",
        json={
            "email": "user@acme.example",
            "password": "correct horse battery staple!",
            "display_name": "U",
        },
    )
    cbearer = client_user.json()["tokens"]["access_token"]
    ch = {"Authorization": f"Bearer {cbearer}"}

    # Risk routes require the admin role -> 403 for a client user.
    assert c.get(f"/risk/clients/{cid}/gate", headers=ch).status_code == 403
    assert c.post(f"/risk/clients/{cid}/register/generate", headers=ch).status_code == 403


@pytest.mark.unit
def test_dimension_score_patch_is_tenant_scoped(c: TestClient) -> None:
    admin = _admin(c)
    a_cid = _make_client(c, admin, "Acme", "acme.example")
    b_cid = _make_client(c, admin, "Beta", "beta.example")

    # Seed a CSF dimension score under client A.
    ah = {"Authorization": f"Bearer {admin}", "X-Client-Id": a_cid}
    svc_id = c.post("/csf/services", headers=ah, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=ah)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=ah, json={"tiers": ["high"]})
    code = SUBCATEGORIES[0].code
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=ah).json()["rows"]
    score_id = next(x["id"] for x in rows if x["subcategory_code"] == code)

    # Same admin, but now acting as client B -> the row belongs to A -> 404.
    bh = {"Authorization": f"Bearer {admin}", "X-Client-Id": b_cid}
    r = c.patch(f"/csf/dimension-scores/{score_id}", headers=bh, json={"governance": 2})
    assert r.status_code == 404

    # Acting as client A it works.
    ok = c.patch(f"/csf/dimension-scores/{score_id}", headers=ah, json={"governance": 2})
    assert ok.status_code == 200
    assert ok.json()["governance"] == 2


@pytest.mark.unit
def test_csf_tier_profile_endpoints_are_tenant_scoped(c: TestClient) -> None:
    admin = _admin(c)
    a_cid = _make_client(c, admin, "Acme", "acme.example")
    b_cid = _make_client(c, admin, "Beta", "beta.example")
    ah = {"Authorization": f"Bearer {admin}", "X-Client-Id": a_cid}
    svc_id = c.post("/csf/services", headers=ah, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=ah)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=ah, json={"tiers": ["high"]})

    # Acting as client B, client A's service + its tier-profile data is invisible.
    bh = {"Authorization": f"Bearer {admin}", "X-Client-Id": b_cid}
    assert c.get(f"/csf/services/{svc_id}/profile/high", headers=bh).status_code == 404
    assert c.get(f"/csf/services/{svc_id}/enterprise-profile", headers=bh).status_code == 404
    assert c.post(f"/csf/services/{svc_id}/playbook/export", headers=bh).status_code == 404
    assert c.post(f"/csf/services/{svc_id}/run-ai", headers=bh).status_code in (404, 409)
    # As client A it resolves.
    assert c.get(f"/csf/services/{svc_id}/profile/high", headers=ah).status_code == 200


@pytest.mark.unit
def test_client_domain_management_is_admin_only(c: TestClient) -> None:
    admin = _admin(c)
    cid = _make_client(c, admin, "Acme", "acme.example")
    user = c.post(
        "/auth/register",
        json={
            "email": "user@acme.example",
            "password": "correct horse battery staple!",
            "display_name": "U",
        },
    )
    ch = {"Authorization": f"Bearer {user.json()['tokens']['access_token']}"}
    assert c.get(f"/admin/clients/{cid}/domains", headers=ch).status_code == 403
    assert (
        c.post(
            f"/admin/clients/{cid}/domains", headers=ch, json={"domain": "x.example"}
        ).status_code
        == 403
    )


@pytest.mark.unit
def test_messages_inbox_does_not_leak_across_tenants(c: TestClient) -> None:
    admin = _admin(c)
    a_cid = _make_client(c, admin, "Acme", "acme.example")
    b_cid = _make_client(c, admin, "Beta", "beta.example")
    ah = {"Authorization": f"Bearer {admin}", "X-Client-Id": a_cid}
    svc = c.post(
        "/attack/services", headers=ah, json={"kind": "attack_coverage", "title": "A"}
    ).json()["id"]
    c.post(f"/services/{svc}/messages", headers=ah, json={"body": "hello A"})

    # The thread shows in A's inbox.
    a_inbox = c.get("/messages/inbox", headers=ah).json()
    assert any(t["service_id"] == svc for t in a_inbox["threads"])
    # ...and never in B's.
    bh = {"Authorization": f"Bearer {admin}", "X-Client-Id": b_cid}
    b_inbox = c.get("/messages/inbox", headers=bh).json()
    assert all(t["service_id"] != svc for t in b_inbox["threads"])
    # B cannot read A's thread directly either.
    assert c.get(f"/services/{svc}/messages", headers=bh).status_code == 404
