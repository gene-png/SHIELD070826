"""Artifact upload route tests against an ephemeral SQLite + local storage."""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit_entry import AuditEntry
from app.storage.local import LocalFilesystemStorage
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, sessionmaker, Path]]:
    db_path = tmp_path / "shield-artifacts.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url

    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    test_engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    storage_root = tmp_path / "artifacts"
    backend = LocalFilesystemStorage(storage_root)

    from app.db.session import get_db
    from app.main import create_app
    from app.routes.artifacts import _storage_dep

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_storage_dep] = lambda: backend

    # Multi-tenant (post-0013): admin/reviewer callers must name an active
    # tenant via X-Client-Id. Seed one tenant and bake the header into the
    # test client so single-tenant-style tests resolve to it; client-role
    # callers are pinned to their own client and ignore this header.
    from app.models.client import Client as _Client

    _seed = TestSession()
    _tenant = _Client(legal_name="Test Tenant")
    _seed.add(_tenant)
    _seed.commit()
    _cid = str(_tenant.id)
    _seed.close()

    with TestClient(app, headers={"X-Client-Id": _cid}) as c:
        yield c, TestSession, storage_root


def _bearer(client: TestClient) -> str:
    r = client.post(
        "/auth/register",
        json={
            "email": "uploader@example.com",
            "password": "correct horse battery staple!",
            "display_name": "Uploader",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["tokens"]["access_token"]


@pytest.mark.unit
def test_upload_writes_artifact_row_and_storage_object(app_client) -> None:
    client, TestSession, storage_root = app_client
    bearer = _bearer(client)
    payload = b"%PDF-1.7 fake pdf content for test"
    r = client.post(
        "/artifacts",
        headers={"Authorization": f"Bearer {bearer}"},
        files={"file": ("system-inventory.pdf", io.BytesIO(payload), "application/pdf")},
        data={"notes": "Initial inventory."},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "system-inventory.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["size_bytes"] == len(payload)
    assert body["origin"] == "client_upload"
    assert body["notes"] == "Initial inventory."
    assert body["sha256"]

    # File written to storage.
    files = list(storage_root.rglob("system-inventory.pdf"))
    assert len(files) == 1
    assert files[0].read_bytes() == payload

    # Audit row written.
    with TestSession() as db:
        rows = (
            db.execute(select(AuditEntry).where(AuditEntry.action == "artifact.uploaded"))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].details["mime_type"] == "application/pdf"
        assert rows[0].details["size_bytes"] == len(payload)


@pytest.mark.unit
def test_upload_rejects_unknown_mime(app_client) -> None:
    client, _, _ = app_client
    bearer = _bearer(client)
    r = client.post(
        "/artifacts",
        headers={"Authorization": f"Bearer {bearer}"},
        files={"file": ("malware.exe", io.BytesIO(b"MZ\x90\x00"), "application/x-msdownload")},
    )
    assert r.status_code == 415


@pytest.mark.unit
def test_upload_rejects_legacy_xls_with_actionable_message(app_client) -> None:
    """Legacy OLE2 .xls crashes openpyxl downstream, so it is rejected at
    upload with a typed 415 telling the user to re-save as .xlsx (C-2)."""
    client, _, _ = app_client
    bearer = _bearer(client)
    r = client.post(
        "/artifacts",
        headers={"Authorization": f"Bearer {bearer}"},
        files={
            "file": (
                "inventory.xls",
                io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-ole2-bytes"),
                "application/vnd.ms-excel",
            )
        },
    )
    assert r.status_code == 415, r.text
    assert r.json()["error"]["message"] == (
        "Legacy .xls is not supported; re-save the file as .xlsx and upload again."
    )


@pytest.mark.unit
def test_upload_rejects_empty_file(app_client) -> None:
    client, _, _ = app_client
    bearer = _bearer(client)
    r = client.post(
        "/artifacts",
        headers={"Authorization": f"Bearer {bearer}"},
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_upload_sanitizes_filename(app_client) -> None:
    client, _, storage_root = app_client
    bearer = _bearer(client)
    r = client.post(
        "/artifacts",
        headers={"Authorization": f"Bearer {bearer}"},
        files={
            "file": (
                "../../etc/passwd",
                io.BytesIO(b"%PDF-1.7 fake pdf"),
                "application/pdf",
            ),
        },
    )
    assert r.status_code == 201
    body = r.json()
    # The path traversal is stripped down to the basename.
    assert ".." not in body["title"]
    assert "/" not in body["title"]
    # Storage tree under storage_root - no file escaped to /etc.
    assert (storage_root.parent / "etc").exists() is False


@pytest.mark.unit
def test_list_artifacts_only_returns_own(app_client) -> None:
    client, _, _ = app_client
    bearer = _bearer(client)
    payload = b"%PDF-1.7 fake pdf"
    for name in ("a.pdf", "b.pdf"):
        r = client.post(
            "/artifacts",
            headers={"Authorization": f"Bearer {bearer}"},
            files={"file": (name, io.BytesIO(payload), "application/pdf")},
        )
        assert r.status_code == 201

    r = client.get("/artifacts", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200
    titles = sorted(item["title"] for item in r.json()["items"])
    assert titles == ["a.pdf", "b.pdf"]


@pytest.mark.unit
def test_get_artifact_returns_404_for_unknown_id(app_client) -> None:
    import uuid as _uuid

    client, _, _ = app_client
    bearer = _bearer(client)
    r = client.get(
        f"/artifacts/{_uuid.uuid4()}",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_artifact_routes_require_authentication(app_client) -> None:
    client, _, _ = app_client
    r = client.post(
        "/artifacts",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.7"), "application/pdf")},
    )
    assert r.status_code == 401
    r = client.get("/artifacts")
    assert r.status_code == 401
