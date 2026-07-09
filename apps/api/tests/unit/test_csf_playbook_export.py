"""CSF full-Playbook XLSX export (Work Order D4)."""

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
    url = f"sqlite:///{tmp_path / 'shield-csfxlsx.db'}"
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
    from app.routes.artifacts import _storage_dep
    from app.storage.local import LocalFilesystemStorage

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_storage_dep] = lambda: LocalFilesystemStorage(tmp_path / "storage")
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, cid


@pytest.mark.unit
def test_playbook_export_produces_downloadable_xlsx(app_client) -> None:
    c, cid = app_client
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)

    # Locked before seeding.
    assert c.post(f"/csf/services/{svc_id}/playbook/export", headers=h).status_code == 409

    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high", "moderate"]})
    # Give one subcategory some scores so the sheets have content.
    code = SUBCATEGORIES[0].code
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    sid = next(x["id"] for x in rows if x["subcategory_code"] == code)
    c.patch(
        f"/csf/dimension-scores/{sid}",
        headers=h,
        json={"governance": 2, "policy": 2, "has_evidence": True, "target_level": 4},
    )

    ex = c.post(f"/csf/services/{svc_id}/playbook/export", headers=h)
    assert ex.status_code == 200, ex.text
    arts = {a["kind"]: a for a in ex.json()["artifacts"]}
    # XLSX workbook + executive briefing (PDF+Word) + full playbook (PDF+Word).
    assert set(arts) == {"xlsx", "exec_pdf", "exec_docx", "full_pdf", "full_docx"}

    dh = {**h, "X-Client-Id": cid}
    magic = {
        "xlsx": b"PK",
        "exec_pdf": b"%PDF-",
        "exec_docx": b"PK",
        "full_pdf": b"%PDF-",
        "full_docx": b"PK",
    }
    for kind, art in arts.items():
        dl = c.get(f"/artifacts/{art['artifact_id']}/download", headers=dh)
        assert dl.status_code == 200, f"{kind}: {dl.status_code}"
        assert dl.content.startswith(magic[kind]), f"{kind} wrong magic bytes"
