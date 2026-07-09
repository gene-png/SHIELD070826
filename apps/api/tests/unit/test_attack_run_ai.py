"""mitre_map Run-AI: D/P/R suggestions, tool validation, lock-skip (Work Order D2)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.attack.catalog import TECHNIQUES
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


def _seed_tech_debt_versions(
    TestSession: sessionmaker,
    cid: str,
    user_id,
    versions: list[tuple[int, CapabilityListStatus, list[str]]],
) -> None:
    """One Tech Debt service carrying several capability-list versions/statuses."""
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
        for ver, st, tools in versions:
            cl = CapabilityList(service_id=svc.id, version=ver, status=st)
            db.add(cl)
            db.flush()
            for name in tools:
                db.add(CapabilityItem(capability_list_id=cl.id, name=name))
        db.commit()


def _open_assessment(c: TestClient, h: dict) -> tuple[str, list[str]]:
    """Open an ATT&CK service + draft assessment; return (service_id, codes)."""
    svc = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "Acme ATT&CK"}
    )
    svc_id = svc.json()["id"]
    a = c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    codes = [row["technique_code"] for row in a.json()["coverage"]]
    return svc_id, codes


def _recording_echo(status_value: str) -> tuple[object, list[list[str]]]:
    """A mitre_map fixture that records each batch's technique_codes and echoes
    back the requested status for every code in that batch."""
    calls: list[list[str]] = []

    def fn(payload: dict) -> LLMResponse:
        batch = list(payload.get("technique_codes", []))
        calls.append(batch)
        techniques = [{"technique_code": code, "status": status_value} for code in batch]
        return LLMResponse(json.dumps({"techniques": techniques}))

    return fn, calls


# ---------------------------------------------------------------------------
# FIX A-3: chunk mitre_map by tactic; merge; fail loudly on a bad batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_ai_chunks_cover_every_code_exactly_once_and_merges(app_client) -> None:
    c, _TestSession, provider = app_client
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id, _codes = _open_assessment(c, h)

    fn, calls = _recording_echo("gap")
    provider.register("mitre_map", fn)

    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text

    # Chunking actually happened (the full matrix is far past one batch).
    assert len(calls) >= 2
    # Exactly-once coverage: the union of every batch's inputs equals the full
    # technique set, and the concatenation has no duplicates.
    all_codes = sorted(t.id for t in TECHNIQUES)
    union = [code for batch in calls for code in batch]
    assert sorted(set(union)) == all_codes  # nothing omitted
    assert len(union) == len(all_codes)  # nothing duplicated across batches
    # No batch exceeds the Haiku output-cap batch size.
    assert all(len(batch) <= 90 for batch in calls)

    # Merged results applied: every row picked up the suggested status.
    body = r.json()
    assert body["coverage"]
    assert all(row["status"] == "gap" for row in body["coverage"])


@pytest.mark.unit
def test_run_ai_bad_batch_aborts_and_applies_nothing(app_client) -> None:
    c, _TestSession, provider = app_client
    bearer, cid = _admin(c)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id, _codes = _open_assessment(c, h)

    # Return valid JSON for every batch EXCEPT the 2nd, which returns garbage.
    # A tolerant merge would have already applied batch #1's statuses; the
    # fail-loud rule must roll the whole run back instead.
    state = {"n": 0}

    def fn(payload: dict) -> LLMResponse:
        state["n"] += 1
        if state["n"] == 2:
            return LLMResponse("{ this is not valid json")
        techniques = [
            {"technique_code": code, "status": "gap"} for code in payload.get("technique_codes", [])
        ]
        return LLMResponse(json.dumps({"techniques": techniques}))

    provider.register("mitre_map", fn)

    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 502, r.text

    # NOTHING applied: every coverage row is still unscored.
    latest = c.get(f"/attack/services/{svc_id}/assessments/latest", headers=h)
    assert latest.status_code == 200, latest.text
    rows = latest.json()["coverage"]
    assert rows
    assert all(row["status"] is None for row in rows)


# ---------------------------------------------------------------------------
# FIX G-2: only the latest APPROVED/RELEASED capability list is citable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_ai_draft_only_list_yields_empty_tools_and_warns(app_client) -> None:
    c, TestSession, provider = app_client
    bearer, cid = _admin(c)
    me = c.get("/auth/me", headers={"Authorization": f"Bearer {bearer}"}).json()
    # Only a DRAFT list exists — its tools must NOT be citable.
    _seed_tech_debt_versions(
        TestSession,
        cid,
        me["id"],
        [(1, CapabilityListStatus.DRAFT, ["CrowdStrike Falcon", "Splunk"])],
    )
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id, _codes = _open_assessment(c, h)

    provider.register_static("mitre_map", LLMResponse('{"techniques": []}'))
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_available"] == 0
    assert any("no approved capability list" in w.lower() for w in body["warnings"])


@pytest.mark.unit
def test_run_ai_approved_v2_excludes_v1_ghost_items(app_client) -> None:
    c, TestSession, provider = app_client
    bearer, cid = _admin(c)
    me = c.get("/auth/me", headers={"Authorization": f"Bearer {bearer}"}).json()
    # v1 approved cited "GhostTool"; v2 approved replaced it with "RealTool".
    _seed_tech_debt_versions(
        TestSession,
        cid,
        me["id"],
        [
            (1, CapabilityListStatus.APPROVED, ["GhostTool"]),
            (2, CapabilityListStatus.APPROVED, ["RealTool"]),
        ],
    )
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc_id, codes = _open_assessment(c, h)
    code = codes[0]

    # The AI cites BOTH the ghost (superseded) and the real (current) tool.
    provider.register_static(
        "mitre_map",
        LLMResponse(
            '{"techniques": [{"technique_code": "' + code + '", "status": "covered",'
            ' "detection_tools": ["GhostTool", "RealTool"]}]}'
        ),
    )
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # Only the latest approved version's tool is citable.
    assert body["tools_available"] == 1
    assert not body["warnings"]
    row = next(t for t in body["coverage"] if t["technique_code"] == code)
    assert row["detection_tools"] == ["RealTool"]


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
