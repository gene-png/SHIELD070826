"""csf_score Run-AI: dimension suggestions, validation, lock-skip (Work Order D4)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.csf.catalog import SUBCATEGORIES
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-csfai.db'}"
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
    from app.routes.csf import _llm_dep

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_llm_dep] = lambda: LLMClient(provider)
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, provider


def _bootstrap(c: TestClient) -> tuple[dict, str]:
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})
    return h, svc_id


@pytest.mark.unit
def test_csf_run_ai_applies_dimensions_and_clamps(app_client) -> None:
    c, provider = app_client
    h, svc_id = _bootstrap(c)
    code = SUBCATEGORIES[0].code
    provider.register_static(
        "csf_score",
        LLMResponse(
            '{"scores": [{"tier": "high", "subcategory_code": "' + code + '",'
            ' "governance": 2, "policy": 1, "implementation": 2, "monitoring": 1,'
            ' "improvement": 5, "what_we_found": "Mature IAM."}]}'  # improvement=5 invalid
        ),
    )
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(x for x in body["rows"] if x["subcategory_code"] == code and x["tier"] == "high")
    assert row["governance"] == 2
    assert row["policy"] == 1
    assert row["implementation"] == 2
    assert row["improvement"] == 0  # out-of-range 5 ignored, stays default 0
    assert row["what_we_found"] == "Mature IAM."
    fields = {ch["field"] for ch in body["changed"] if ch["subcategory_code"] == code}
    assert {"governance", "policy", "implementation", "monitoring", "what_we_found"} <= fields


@pytest.mark.unit
def test_csf_run_ai_skips_locked(app_client) -> None:
    c, provider = app_client
    h, svc_id = _bootstrap(c)
    code = SUBCATEGORIES[0].code
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    sid = next(x["id"] for x in rows if x["subcategory_code"] == code)
    c.patch(f"/csf/dimension-scores/{sid}", headers=h, json={"locked": True})
    provider.register_static(
        "csf_score",
        LLMResponse(
            '{"scores": [{"tier": "high", "subcategory_code": "' + code + '", "governance": 2}]}'
        ),
    )
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    row = next(x for x in r.json()["rows"] if x["subcategory_code"] == code and x["tier"] == "high")
    assert row["governance"] == 0
    assert all(ch["subcategory_code"] != code for ch in r.json()["changed"])


@pytest.mark.unit
def test_csf_run_ai_requires_seeded_profile(app_client) -> None:
    c, provider = app_client
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    provider.register_static("csf_score", LLMResponse('{"scores": []}'))
    # No profile seeded -> 409.
    assert c.post(f"/csf/services/{svc_id}/run-ai", headers=h).status_code == 409
