"""Admin client-domain management + service archive (Work Order B1/B2)."""

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
    db_path = tmp_path / "shield-domains.db"
    url = f"sqlite:///{db_path}"
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


def _admin_bearer(c: TestClient) -> str:
    return register_admin(c, "admin@kentro.example")["tokens"]["access_token"]


def _make_client(c: TestClient, bearer: str, name: str = "Acme Corp") -> str:
    r = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.unit
def test_domain_add_list_remove_round_trip(app_client: TestClient) -> None:
    c = app_client
    bearer = _admin_bearer(c)
    cid = _make_client(c, bearer)
    h = {"Authorization": f"Bearer {bearer}"}

    add = c.post(f"/admin/clients/{cid}/domains", headers=h, json={"domain": "ACME.com"})
    assert add.status_code == 201, add.text
    assert add.json()["domain"] == "acme.com"  # normalized lowercase
    domain_id = add.json()["id"]

    lst = c.get(f"/admin/clients/{cid}/domains", headers=h)
    assert lst.status_code == 200
    assert [d["domain"] for d in lst.json()["domains"]] == ["acme.com"]

    rm = c.delete(f"/admin/clients/{cid}/domains/{domain_id}", headers=h)
    assert rm.status_code == 204
    lst2 = c.get(f"/admin/clients/{cid}/domains", headers=h)
    assert lst2.json()["domains"] == []


@pytest.mark.unit
def test_domain_accepts_full_email_and_strips_to_domain(app_client: TestClient) -> None:
    c = app_client
    bearer = _admin_bearer(c)
    cid = _make_client(c, bearer)
    add = c.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"domain": "person@acme.com"},
    )
    assert add.status_code == 201
    assert add.json()["domain"] == "acme.com"


@pytest.mark.unit
def test_generic_provider_rejected(app_client: TestClient) -> None:
    c = app_client
    bearer = _admin_bearer(c)
    cid = _make_client(c, bearer)
    r = c.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"domain": "gmail.com"},
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_duplicate_domain_rejected(app_client: TestClient) -> None:
    c = app_client
    bearer = _admin_bearer(c)
    h = {"Authorization": f"Bearer {bearer}"}
    cid1 = _make_client(c, bearer, "Acme")
    cid2 = _make_client(c, bearer, "Beta")
    assert (
        c.post(f"/admin/clients/{cid1}/domains", headers=h, json={"domain": "acme.com"}).status_code
        == 201
    )
    dup = c.post(f"/admin/clients/{cid2}/domains", headers=h, json={"domain": "acme.com"})
    assert dup.status_code == 409


@pytest.mark.unit
def test_client_user_cannot_manage_domains(app_client: TestClient) -> None:
    """Isolation: a client-role user cannot reach the admin domain endpoints."""
    c = app_client
    bearer = _admin_bearer(c)
    cid = _make_client(c, bearer)
    c.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"domain": "acme.com"},
    )
    client_user = _register(c, "user@acme.com")
    cbearer = client_user["tokens"]["access_token"]
    ch = {"Authorization": f"Bearer {cbearer}"}
    assert c.get(f"/admin/clients/{cid}/domains", headers=ch).status_code == 403
    assert (
        c.post(f"/admin/clients/{cid}/domains", headers=ch, json={"domain": "evil.com"}).status_code
        == 403
    )


@pytest.mark.unit
def test_archive_service(app_client: TestClient) -> None:
    c = app_client
    bearer = _admin_bearer(c)
    cid = _make_client(c, bearer)
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "x"})
    assert svc.status_code == 201, svc.text
    svc_id = svc.json()["id"]
    arch = c.delete(f"/admin/services/{svc_id}", headers={"Authorization": f"Bearer {bearer}"})
    assert arch.status_code == 204
    detail = c.get(f"/admin/services/{svc_id}", headers={"Authorization": f"Bearer {bearer}"})
    assert detail.status_code == 200
    assert detail.json()["status"] == "archived"


@pytest.mark.unit
def test_archive_unknown_service_404(app_client: TestClient) -> None:
    c = app_client
    bearer = _admin_bearer(c)
    r = c.delete(
        f"/admin/services/{_uuid.uuid4()}",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404
