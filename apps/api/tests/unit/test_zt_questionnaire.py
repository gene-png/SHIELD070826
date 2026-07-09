"""ZT verbatim interview questionnaire: seed data + endpoint (Work Order C8)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.questionnaire import Question
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin

_PKG = Path(__file__).resolve().parents[4] / "packages" / "zt-data" / "source"


# --- seed data integrity (no DB) -------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename,framework_key,framework",
    [
        ("zt_cisa.json", "zt-cisa", "cisa_ztmm_2_0"),
        ("zt_dod.json", "zt-dod", "dod_ztra"),
    ],
)
def test_seed_files_have_twelve_well_formed_questions(
    filename: str, framework_key: str, framework: str
) -> None:
    data = json.loads((_PKG / filename).read_text(encoding="utf-8"))
    assert data["framework_key"] == framework_key
    assert data["framework"] == framework
    qs = data["questions"]
    assert len(qs) == 12
    ids = [q["external_id"] for q in qs]
    assert len(set(ids)) == 12
    for q in qs:
        assert q["stem"].strip()
        assert q["section_name"].strip()
        assert isinstance(q["cues"], list) and q["cues"]
        assert isinstance(q["framework_activities"], list)


# --- endpoint ---------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, sessionmaker]]:
    url = f"sqlite:///{tmp_path / 'shield-ztq.db'}"
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
    with TestClient(app) as c:
        yield c, TestSession


def _seed_question(TestSession: sessionmaker, framework_key: str, ext_id: str, pillar: str) -> None:
    with TestSession() as db:
        db.add(
            Question(
                framework_key=framework_key,
                external_id=ext_id,
                pillar=pillar,
                order_index=1,
                stem=f"Stem for {ext_id}",
                cues=["cue a", "cue b"],
                framework_activities=["Authentication"],
            )
        )
        db.commit()


def _admin_service(c: TestClient, kind: str) -> tuple[str, str]:
    # Self-registration only ever mints `client`-role users, so go through the
    # shared helper that promotes the row to a cross-tenant admin.
    bearer = register_admin(c, "admin@kentro.example")["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post("/zt/services", headers=h, json={"kind": kind, "title": "Acme ZT"})
    return bearer, svc.json()["id"]


@pytest.mark.unit
def test_questionnaire_endpoint_serves_cisa_questions(app_client) -> None:
    c, TestSession = app_client
    _seed_question(TestSession, "zt-cisa", "Z-Q1", "Identity")
    _seed_question(TestSession, "zt-dod", "D-Q1", "User")  # other framework, must not leak
    bearer, svc_id = _admin_service(c, "zero_trust_cisa")
    cid = c.get(f"/admin/services/{svc_id}", headers={"Authorization": f"Bearer {bearer}"}).json()[
        "client_id"
    ]
    r = c.get(
        f"/zt/services/{svc_id}/questionnaire",
        headers={"Authorization": f"Bearer {bearer}", "X-Client-Id": cid},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["framework_key"] == "zt-cisa"
    assert body["framework"] == "cisa_ztmm_2_0"
    assert [q["external_id"] for q in body["questions"]] == ["Z-Q1"]
    assert body["questions"][0]["section_name"] == "Identity"
    assert body["questions"][0]["cues"] == ["cue a", "cue b"]


@pytest.mark.unit
def test_questionnaire_endpoint_serves_dod_questions(app_client) -> None:
    c, TestSession = app_client
    _seed_question(TestSession, "zt-dod", "D-Q1", "User")
    bearer, svc_id = _admin_service(c, "zero_trust_dod")
    cid = c.get(f"/admin/services/{svc_id}", headers={"Authorization": f"Bearer {bearer}"}).json()[
        "client_id"
    ]
    r = c.get(
        f"/zt/services/{svc_id}/questionnaire",
        headers={"Authorization": f"Bearer {bearer}", "X-Client-Id": cid},
    )
    assert r.status_code == 200, r.text
    assert r.json()["framework_key"] == "zt-dod"
    assert [q["external_id"] for q in r.json()["questions"]] == ["D-Q1"]
