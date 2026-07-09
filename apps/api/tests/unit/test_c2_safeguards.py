"""Edit-and-rerun safeguards: row lock + 'what changed' diff (Work Order C2)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.diff import changed_fields, diff_keyed_rows
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp

# --- pure diff helpers ------------------------------------------------------


@pytest.mark.unit
def test_changed_fields_reports_only_differences() -> None:
    old = {"a": 1, "b": "x", "c": None}
    new = {"a": 2, "b": "x", "c": "y"}
    changes = changed_fields(old, new, ["a", "b", "c"])
    assert [(c.field, c.old, c.new) for c in changes] == [
        ("a", 1, 2),
        ("c", None, "y"),
    ]


@pytest.mark.unit
def test_diff_keyed_rows_skips_locked_and_unchanged() -> None:
    old = {"r1": {"v": 1}, "r2": {"v": 2}, "r3": {"v": 3}}
    new = {"r1": {"v": 9}, "r2": {"v": 2}, "r3": {"v": 8}}
    diffs = diff_keyed_rows(old, new, ["v"], locked_keys=frozenset({"r3"}))
    # r1 changed (kept), r2 unchanged (dropped), r3 changed but locked (skipped).
    assert [d.key for d in diffs] == ["r1"]
    assert diffs[0].changes[0].old == 1
    assert diffs[0].changes[0].new == 9


# --- row lock round-trip through the API ------------------------------------


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'shield-c2.db'}"
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
        yield c


def _admin_and_service(c: TestClient) -> tuple[str, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "Acme CSF"})
    return bearer, svc.json()["id"]


@pytest.mark.unit
def test_admin_can_lock_a_csf_answer(app_client: TestClient) -> None:
    c = app_client
    bearer, svc_id = _admin_and_service(c)
    h = {"Authorization": f"Bearer {bearer}"}
    # The X-Client-Id is needed for tenant-scoped calls; resolve it from the service.
    cid = c.get(f"/admin/services/{svc_id}", headers=h).json()["client_id"]
    th = {**h, "X-Client-Id": cid}

    a = c.post(f"/csf/services/{svc_id}/assessments", headers=th)
    answer_id = a.json()["answers"][0]["id"]
    assert a.json()["answers"][0]["locked"] is False

    # Lock the row.
    r = c.patch(f"/csf/answers/{answer_id}", headers=th, json={"locked": True})
    assert r.status_code == 200, r.text
    assert r.json()["locked"] is True

    # Unlock it again.
    r2 = c.patch(f"/csf/answers/{answer_id}", headers=th, json={"locked": False})
    assert r2.json()["locked"] is False
