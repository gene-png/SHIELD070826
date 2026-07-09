"""mitre_map Run-AI: D/P/R suggestions, tool validation, lock-skip (Work Order D2)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.models.capability import CapabilityItem, CapabilityList, CapabilityListStatus
from app.models.service import Service, ServiceKind, ServiceStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, sessionmaker, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-attackai.db'}"
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
    from app.routes.attack import _llm_dep

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
    with TestClient(app) as c:
        yield c, TestSession, provider


def _admin(c: TestClient) -> tuple[str, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    return bearer, cid


def _seed_tech_debt_tools(TestSession: sessionmaker, cid: str, user_id, tools: list[str]) -> None:
    """A Tech Debt service + approved capability list with the given tool names."""
    import uuid as _uuid

    with TestSession() as db:
        svc = Service(
            kind=ServiceKind.TECH_DEBT,
            status=ServiceStatus.IN_PROGRESS,
            title="Acme Tech Debt",
            client_id=_uuid.UUID(cid),
            opened_by=_uuid.UUID(user_id),
        )
        db.add(svc)
        db.flush()
        cl = CapabilityList(service_id=svc.id, version=1, status=CapabilityListStatus.APPROVED)
        db.add(cl)
        db.flush()
        for name in tools:
            db.add(CapabilityItem(capability_list_id=cl.id, name=name))
        db.commit()


@pytest.mark.unit
def test_run_ai_applies_validated_dpr_and_reports_changes(app_client) -> None:
    c, TestSession, provider = app_client
    bearer, cid = _admin(c)
    me = c.get("/auth/me", headers={"Authorization": f"Bearer {bearer}"}).json()
    _seed_tech_debt_tools(TestSession, cid, me["id"], ["CrowdStrike Falcon", "Splunk"])

    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "Acme ATT&CK"}
    )
    svc_id = svc.json()["id"]
    a = c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    code = a.json()["coverage"][0]["technique_code"]

    # The AI suggests covered + D/P/R, citing one real tool and one not in the list.
    provider.register_static(
        "mitre_map",
        LLMResponse(
            '{"techniques": [{"technique_code": "' + code + '", "status": "covered",'
            ' "detection_tools": ["CrowdStrike Falcon", "Nonexistent Tool"],'
            ' "prevention_tools": [], "response_tools": ["Splunk"],'
            ' "rationale": "EDR detects, SIEM responds."}]}'
        ),
    )

    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_available"] == 2
    row = next(t for t in body["coverage"] if t["technique_code"] == code)
    assert row["status"] == "covered"
    # The invented tool was dropped; only the validated one remains.
    assert row["detection_tools"] == ["CrowdStrike Falcon"]
    assert row["response_tools"] == ["Splunk"]
    assert row["rationale"] == "EDR detects, SIEM responds."
    # The change list reflects what the AI changed.
    fields = {ch["field"] for ch in body["changed"] if ch["technique_code"] == code}
    assert {"status", "detection_tools", "response_tools", "rationale"} <= fields


@pytest.mark.unit
def test_run_ai_skips_locked_rows(app_client) -> None:
    c, TestSession, provider = app_client
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post("/attack/services", headers=h, json={"kind": "attack_coverage", "title": "Acme"})
    svc_id = svc.json()["id"]
    a = c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    cov = a.json()["coverage"][0]
    code, cov_id = cov["technique_code"], cov["id"]

    # Lock the row.
    c.patch(f"/attack/coverage/{cov_id}", headers=h, json={"locked": True})

    provider.register_static(
        "mitre_map",
        LLMResponse('{"techniques": [{"technique_code": "' + code + '", "status": "covered"}]}'),
    )
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # Locked row untouched + absent from the change list.
    row = next(t for t in body["coverage"] if t["technique_code"] == code)
    assert row["status"] is None
    assert all(ch["technique_code"] != code for ch in body["changed"])


@pytest.mark.unit
def test_run_ai_marks_documents_stale(app_client) -> None:
    c, _TestSession, provider = app_client
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post("/attack/services", headers=h, json={"kind": "attack_coverage", "title": "Acme"})
    svc_id = svc.json()["id"]
    a = c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    code = a.json()["coverage"][0]["technique_code"]
    assert a.json()["documents_stale"] is False

    provider.register_static(
        "mitre_map",
        LLMResponse('{"techniques": [{"technique_code": "' + code + '", "status": "covered"}]}'),
    )
    c.post(f"/attack/services/{svc_id}/run-ai", headers=h)

    latest = c.get(f"/attack/services/{svc_id}/assessments/latest", headers=h)
    assert latest.status_code == 200, latest.text
    assert latest.json()["documents_stale"] is True  # Work Order C3
