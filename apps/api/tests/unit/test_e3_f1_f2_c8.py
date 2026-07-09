"""E-3 (concurrency/idempotency), F-1 (auto-seed), F-2 (auto-create), C-8 (evidence links).

All on SQLite: the Postgres advisory-lock layer is a documented no-op there, so
the concurrency test exercises the in-process mutex layer (app/db/locks.py),
which is what serializes a double-click within a worker either way.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def env(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider, sessionmaker]]:
    url = f"sqlite:///{tmp_path / 'shield-e3.db'}"
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
    from app.routes.artifacts import _storage_dep
    from app.routes.attack import _llm_dep as attack_llm
    from app.routes.csf import _llm_dep as csf_llm
    from app.routes.risk import _llm_dep as risk_llm
    from app.routes.zt import _llm_dep as zt_llm
    from app.storage.local import LocalFilesystemStorage

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    for dep in (csf_llm, zt_llm, attack_llm, risk_llm):
        app.dependency_overrides[dep] = lambda: LLMClient(provider)
    app.dependency_overrides[_storage_dep] = lambda: storage
    with TestClient(app) as c:
        yield c, provider, TestSession


def _admin(c: TestClient) -> tuple[str, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    return bearer, cid


# ---------------------------------------------------------------------------
# E-3: server-side concurrency guard on run-ai (one 200, one typed 409)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_concurrent_run_ai_yields_exactly_one_409(env) -> None:
    c, provider, _TS = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})

    entered = threading.Event()
    release = threading.Event()

    def blocking(_payload) -> LLMResponse:
        # Winner is now inside the provider call, still holding the run lock.
        entered.set()
        release.wait(timeout=10)
        return LLMResponse('{"scores": []}')

    provider.register("csf_score", blocking)

    results: dict[str, int] = {}

    def call(key: str) -> None:
        results[key] = c.post(f"/csf/services/{svc_id}/run-ai", headers=h).status_code

    winner = threading.Thread(target=call, args=("winner",))
    winner.start()
    assert entered.wait(timeout=10), "winner never reached the provider call"

    # Second concurrent click while the winner holds the lock -> typed 409.
    loser = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert loser.status_code == 409, loser.text
    assert "in progress" in loser.text.lower()

    release.set()
    winner.join(timeout=10)
    assert results["winner"] == 200
    assert sorted([results["winner"], loser.status_code]) == [200, 409]


# ---------------------------------------------------------------------------
# E-3: risk_registers unique (client_id, version) blocks a duplicate version
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_risk_register_unique_client_version(env) -> None:
    from app.models.client import Client as _Client
    from app.models.risk_register import RiskRegister

    _c, _provider, TestSession = env
    with TestSession() as db:
        cl = _Client(legal_name="Dup Co")
        db.add(cl)
        db.flush()
        db.add(RiskRegister(client_id=cl.id, version=1))
        db.commit()
        cid = cl.id

    with TestSession() as db:  # noqa: SIM117 - separate txn to isolate the violation
        db.add(RiskRegister(client_id=cid, version=1))  # same (client_id, version)
        with pytest.raises(IntegrityError):
            db.commit()


# ---------------------------------------------------------------------------
# F-1: run-ai auto-seeds CSF; auto-seed leaves scored_at NULL (B-3 gate holds)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_csf_run_ai_auto_seed_keeps_export_blocked(env) -> None:
    from app.models.csf_profile import CsfDimensionScore

    c, provider, TestSession = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)

    # Empty AI response: auto-seed happens but nothing gets scored.
    provider.register_static("csf_score", LLMResponse('{"scores": []}'))
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text  # F-1: no 409-on-unseeded
    assert r.json()["rows"], "auto-seed should have created rows"

    # Auto-seed must NOT stamp scored_at, or it would silently defeat B-3.
    with TestSession() as db:
        rows = db.query(CsfDimensionScore).all()
        assert rows
        assert all(row.scored_at is None for row in rows)

    # And B-3's export gate still blocks (in-scope rows unscored).
    exp = c.post(f"/csf/services/{svc_id}/playbook/export", headers=h)
    assert exp.status_code == 409, exp.text
    assert "unscored" in exp.text.lower()


# ---------------------------------------------------------------------------
# F-2: run-ai auto-creates the assessment for ATT&CK and ZT
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_attack_run_ai_auto_creates_assessment(env) -> None:
    c, provider, _TS = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    ).json()["id"]

    provider.register_static("mitre_map", LLMResponse('{"techniques": []}'))
    # No assessment created via "Start"; run-ai creates it.
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    latest = c.get(f"/attack/services/{svc_id}/assessments/latest", headers=h).json()
    assert latest["version"] == 1

    # A second run does not mint a second version.
    r2 = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r2.status_code == 200, r2.text
    assert c.get(f"/attack/services/{svc_id}/assessments/latest", headers=h).json()["version"] == 1


@pytest.mark.unit
def test_zt_run_ai_auto_creates_assessment(env) -> None:
    c, provider, _TS = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post(
        "/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"}
    ).json()["id"]

    provider.register_static("zt_score", LLMResponse('{"capabilities": []}'))
    r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    latest = c.get(f"/zt/services/{svc_id}/assessments/latest", headers=h).json()
    assert latest["version"] == 1

    r2 = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
    assert r2.status_code == 200, r2.text
    assert c.get(f"/zt/services/{svc_id}/assessments/latest", headers=h).json()["version"] == 1


# ---------------------------------------------------------------------------
# C-8: evidence-artifact links are validated (cross-tenant / nonexistent -> 404)
# ---------------------------------------------------------------------------


def _foreign_artifact_id(TestSession: sessionmaker, uploaded_by: uuid.UUID) -> uuid.UUID:
    """An artifact owned by a DIFFERENT tenant."""
    from app.models.artifact import Artifact, ArtifactOrigin
    from app.models.client import Client as _Client

    with TestSession() as db:
        other = _Client(legal_name="Other Tenant")
        db.add(other)
        db.flush()
        art = Artifact(
            client_id=other.id,
            title="secret.pdf",
            file_storage_key="k/secret.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            sha256="0" * 64,
            origin=ArtifactOrigin.CLIENT_UPLOAD,
            uploaded_by=uploaded_by,
        )
        db.add(art)
        db.commit()
        return art.id


def _admin_user_id(c: TestClient, bearer: str) -> uuid.UUID:
    return uuid.UUID(c.get("/auth/me", headers={"Authorization": f"Bearer {bearer}"}).json()["id"])


@pytest.mark.unit
def test_attack_evidence_link_cross_tenant_and_missing_404(env) -> None:
    c, _provider, TestSession = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    ).json()["id"]
    a = c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    cov_id = a.json()["coverage"][0]["id"]

    foreign = _foreign_artifact_id(TestSession, _admin_user_id(c, bearer))
    r = c.patch(
        f"/attack/coverage/{cov_id}", headers=h, json={"evidence_artifact_id": str(foreign)}
    )
    assert r.status_code == 404, r.text  # cross-tenant, not leaked/500

    r = c.patch(
        f"/attack/coverage/{cov_id}",
        headers=h,
        json={"evidence_artifact_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404, r.text  # nonexistent, not a 500 IntegrityError


@pytest.mark.unit
def test_csf_evidence_link_cross_tenant_and_missing_404(env) -> None:
    c, _provider, TestSession = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    a = c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    ans_id = a.json()["answers"][0]["id"]

    foreign = _foreign_artifact_id(TestSession, _admin_user_id(c, bearer))
    r = c.patch(f"/csf/answers/{ans_id}", headers=h, json={"evidence_artifact_id": str(foreign)})
    assert r.status_code == 404, r.text
    r = c.patch(
        f"/csf/answers/{ans_id}", headers=h, json={"evidence_artifact_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404, r.text


@pytest.mark.unit
def test_zt_evidence_link_cross_tenant_and_missing_404(env) -> None:
    c, _provider, TestSession = env
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id = c.post(
        "/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"}
    ).json()["id"]
    a = c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    ans_id = a.json()["answers"][0]["id"]

    foreign = _foreign_artifact_id(TestSession, _admin_user_id(c, bearer))
    r = c.patch(f"/zt/answers/{ans_id}", headers=h, json={"evidence_artifact_id": str(foreign)})
    assert r.status_code == 404, r.text
    r = c.patch(
        f"/zt/answers/{ans_id}", headers=h, json={"evidence_artifact_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404, r.text
