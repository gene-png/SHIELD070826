"""CSF full-Playbook tiered Working Profile + Enterprise roll-up (Work Order D4)."""

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
def app_client(tmp_path) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'shield-csfprofile.db'}"
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
    from app.models.client import Client as _Client
    from app.models.client_domain import ClientDomain as _ClientDomain

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c


def _bootstrap(c: TestClient) -> tuple[dict, str]:
    r = register_admin_resp(c, "admin@example.com")
    bearer = r.json()["tokens"]["access_token"]
    h = {"Authorization": f"Bearer {bearer}"}
    svc = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"})
    svc_id = svc.json()["id"]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    return h, svc_id


def _row(c: TestClient, h: dict, svc_id: str, tier: str, code: str) -> dict:
    rows = c.get(f"/csf/services/{svc_id}/profile/{tier}", headers=h).json()["rows"]
    return next(r for r in rows if r["subcategory_code"] == code)


@pytest.mark.unit
def test_seed_and_evidence_cap(app_client) -> None:
    c = app_client
    h, svc_id = _bootstrap(c)
    seeded = c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})
    assert seeded.status_code == 200
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    assert len(rows) == len(SUBCATEGORIES)

    sid = rows[0]["id"]
    # Full marks, NO evidence -> Implementation capped to 1, level capped to 2.
    r = c.patch(
        f"/csf/dimension-scores/{sid}",
        headers=h,
        json={
            "governance": 2,
            "policy": 2,
            "implementation": 2,
            "monitoring": 2,
            "improvement": 2,
            "has_evidence": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 9  # 2+2+1+2+2
    assert body["level"] == 2
    assert body["evidence_capped"] is True

    # Same scores WITH evidence -> total 10, level 5.
    r2 = c.patch(f"/csf/dimension-scores/{sid}", headers=h, json={"has_evidence": True})
    assert r2.json()["total"] == 10 and r2.json()["level"] == 5


@pytest.mark.unit
def test_enterprise_rollup_high_lowest_rule3(app_client) -> None:
    c = app_client
    h, svc_id = _bootstrap(c)
    c.post(
        f"/csf/services/{svc_id}/profiles/seed",
        headers=h,
        json={"tiers": ["high", "moderate", "low"]},
    )
    code = SUBCATEGORIES[0].code

    # high -> level 1 (total 0); moderate -> level 3 (total 7); low -> level 4 (total 9).
    c.patch(
        f"/csf/dimension-scores/{_row(c, h, svc_id, 'high', code)['id']}",
        headers=h,
        json={"has_evidence": True, "target_level": 4},
    )
    c.patch(
        f"/csf/dimension-scores/{_row(c, h, svc_id, 'moderate', code)['id']}",
        headers=h,
        json={
            "governance": 2,
            "policy": 2,
            "implementation": 2,
            "monitoring": 1,
            "improvement": 0,
            "has_evidence": True,
        },
    )
    c.patch(
        f"/csf/dimension-scores/{_row(c, h, svc_id, 'low', code)['id']}",
        headers=h,
        json={
            "governance": 2,
            "policy": 2,
            "implementation": 2,
            "monitoring": 2,
            "improvement": 1,
            "has_evidence": True,
        },
    )

    ent = c.get(f"/csf/services/{svc_id}/enterprise-profile", headers=h)
    assert ent.status_code == 200, ent.text
    body = ent.json()
    assert set(body["tiers_in_use"]) == {"high", "moderate", "low"}
    sub = next(s for s in body["subcategories"] if s["subcategory_code"] == code)
    assert sub["tier_levels"] == {"high": 1, "moderate": 3, "low": 4}
    # HIGH is the lowest scorer -> Rule 3 -> enterprise level = HIGH's (1).
    assert sub["enterprise_level"] == 1
    assert sub["rollup_rule"] == 3
    # Target 4 with enterprise level 1 -> a gap; HIGH involved + multi-system -> P2.
    assert sub["gap"] is True
    assert sub["priority"] == "P2"


@pytest.mark.unit
def test_out_of_scope_rows_excluded_from_rollup(app_client) -> None:
    c = app_client
    h, svc_id = _bootstrap(c)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})
    code = SUBCATEGORIES[0].code
    c.patch(
        f"/csf/dimension-scores/{_row(c, h, svc_id, 'high', code)['id']}",
        headers=h,
        json={"in_scope": False},
    )
    body = c.get(f"/csf/services/{svc_id}/enterprise-profile", headers=h).json()
    assert all(s["subcategory_code"] != code for s in body["subcategories"])
