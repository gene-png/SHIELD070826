"""Per-service messaging thread tests (Work Order C7)."""

from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'shield-msg.db'}"
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


def _register(c: TestClient, email: str) -> dict:
    r = c.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple!",
            "display_name": email.split("@")[0],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _setup(c: TestClient) -> tuple[str, str, str, str]:
    """admin + client at an approved domain + an open CSF service in the tenant.

    Returns (admin_bearer, client_bearer, client_id, service_id)."""
    admin = register_admin(c, "admin@kentro.example")
    admin_bearer = admin["tokens"]["access_token"]
    created = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"legal_name": "Acme"},
    )
    cid = created.json()["id"]
    c.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"domain": "acme.com"},
    )
    client = _register(c, "user@acme.com")
    client_bearer = client["tokens"]["access_token"]
    svc = c.post(
        "/csf/services",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
        json={"kind": "nist_csf", "title": "Acme CSF"},
    )
    assert svc.status_code == 201, svc.text
    return admin_bearer, client_bearer, cid, svc.json()["id"]


@pytest.mark.unit
def test_admin_and_client_exchange_messages(app_client: TestClient) -> None:
    c = app_client
    admin_bearer, client_bearer, cid, svc_id = _setup(c)

    # Admin posts a request for more info.
    r = c.post(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
        json={"body": "Please upload the SIEM contract."},
    )
    assert r.status_code == 201, r.text
    assert r.json()["author_role"] == "admin"

    # Client sees it and replies (pinned to their own tenant; no header needed).
    thread = c.get(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {client_bearer}"},
    )
    assert thread.status_code == 200
    assert len(thread.json()["messages"]) == 1
    assert thread.json()["messages"][0]["body"] == "Please upload the SIEM contract."

    reply = c.post(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {client_bearer}"},
        json={"body": "Uploaded — thanks."},
    )
    assert reply.status_code == 201
    assert reply.json()["author_role"] == "client"

    # Admin sees both, in order.
    full = c.get(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
    )
    bodies = [m["body"] for m in full.json()["messages"]]
    assert bodies == ["Please upload the SIEM contract.", "Uploaded — thanks."]


@pytest.mark.unit
def test_reading_marks_counterparty_messages_read(app_client: TestClient) -> None:
    c = app_client
    admin_bearer, client_bearer, cid, svc_id = _setup(c)
    c.post(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
        json={"body": "hello"},
    )
    # Client reads -> the admin's message gets read_at stamped.
    thread = c.get(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {client_bearer}"},
    )
    assert thread.json()["messages"][0]["read_at"] is not None


@pytest.mark.unit
def test_empty_body_rejected(app_client: TestClient) -> None:
    c = app_client
    admin_bearer, _, cid, svc_id = _setup(c)
    r = c.post(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
        json={"body": "   "},
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_other_client_cannot_read_thread(app_client: TestClient) -> None:
    """Isolation: a different tenant's client user can't read this thread."""
    c = app_client
    admin_bearer, _, cid, svc_id = _setup(c)
    c.post(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
        json={"body": "secret"},
    )
    # Onboard a second, unrelated client.
    other_cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"legal_name": "Beta"},
    ).json()["id"]
    c.post(
        f"/admin/clients/{other_cid}/domains",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"domain": "beta.example"},
    )
    other = _register(c, "user@beta.example")
    r = c.get(
        f"/services/{svc_id}/messages",
        headers={"Authorization": f"Bearer {other['tokens']['access_token']}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_inbox_summarizes_threads_and_unread(app_client: TestClient) -> None:
    c = app_client
    admin_bearer, client_bearer, cid, svc_id = _setup(c)
    ah = {"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid}
    ch = {"Authorization": f"Bearer {client_bearer}"}

    c.post(f"/services/{svc_id}/messages", headers=ah, json={"body": "First"})
    c.post(f"/services/{svc_id}/messages", headers=ah, json={"body": "Second"})

    inbox = c.get("/messages/inbox", headers=ch)
    assert inbox.status_code == 200, inbox.text
    body = inbox.json()
    assert body["unread_total"] == 2
    thread = next(t for t in body["threads"] if t["service_id"] == svc_id)
    assert thread["total"] == 2
    assert thread["unread"] == 2
    assert thread["last_preview"] == "Second"

    # Reading the thread marks the counterparty messages read -> 0 unread.
    c.get(f"/services/{svc_id}/messages", headers=ch)
    assert c.get("/messages/inbox", headers=ch).json()["unread_total"] == 0


@pytest.mark.unit
def test_inbox_empty_when_no_messages(app_client: TestClient) -> None:
    c = app_client
    admin_bearer, _, cid, _ = _setup(c)
    r = c.get(
        "/messages/inbox",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
    )
    assert r.status_code == 200
    assert r.json() == {"threads": [], "unread_total": 0}


@pytest.mark.unit
def test_unknown_service_404(app_client: TestClient) -> None:
    c = app_client
    admin_bearer, _, cid, _ = _setup(c)
    r = c.get(
        f"/services/{_uuid.uuid4()}/messages",
        headers={"Authorization": f"Bearer {admin_bearer}", "X-Client-Id": cid},
    )
    assert r.status_code == 404
