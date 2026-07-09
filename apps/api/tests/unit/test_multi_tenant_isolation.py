"""Cross-tenant isolation tests.

After migration 0013 the platform supports many `client` rows per
deployment. Each client-role user is pinned to their own `User.client_id`
at registration; admin/reviewer users select the active tenant via the
`X-Client-Id` header. These tests verify:

  1. A fresh client-role registration creates a brand-new Client row.
  2. Two client users registered separately end up in different tenants.
  3. Admin routes that mutate per-tenant data require X-Client-Id.
  4. A client user cannot see another client's services (404, not 403,
     to avoid leaking existence).
  5. An admin scoped to tenant B cannot fetch a service that belongs to
     tenant A by guessing its id.
"""

from __future__ import annotations

import os
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
    db_path = tmp_path / "shield-mt.db"
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


def _bearer(reg: dict) -> str:
    return reg["tokens"]["access_token"]


def _onboard_client(c: TestClient, admin_bearer: str, email: str) -> dict:
    """B1/B2 flow: admin creates the org + approves the email's domain, then
    the client-role user self-registers and auto-joins that org."""
    domain = email.split("@", 1)[1]
    created = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"legal_name": f"Org {domain}"},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    dom = c.post(
        f"/admin/clients/{cid}/domains",
        headers={"Authorization": f"Bearer {admin_bearer}"},
        json={"domain": domain},
    )
    assert dom.status_code == 201, dom.text
    return _register(c, email)


def _me(c: TestClient, bearer: str) -> dict:
    r = c.get("/auth/me", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.unit
def test_admin_registers_with_null_client_id(app_client: TestClient) -> None:
    """First user becomes admin with no client_id (platform-wide)."""
    admin = register_admin(app_client, "admin@example.com")
    me = _me(app_client, _bearer(admin))
    assert me["role"] == "admin"
    assert me["client_id"] is None


@pytest.mark.unit
def test_each_client_user_gets_their_own_tenant(app_client: TestClient) -> None:
    """Two client users at different approved domains land in different tenants."""
    admin = register_admin(app_client, "admin@example.com")
    a = _onboard_client(app_client, _bearer(admin), "alpha@a.example")
    b = _onboard_client(app_client, _bearer(admin), "bravo@b.example")
    me_a = _me(app_client, _bearer(a))
    me_b = _me(app_client, _bearer(b))
    assert me_a["role"] == "client"
    assert me_b["role"] == "client"
    assert me_a["client_id"] is not None
    assert me_b["client_id"] is not None
    assert me_a["client_id"] != me_b["client_id"]


@pytest.mark.unit
def test_admin_csf_create_requires_x_client_id(app_client: TestClient) -> None:
    """Admin/reviewer routes that operate on a tenant must declare it."""
    admin = register_admin(app_client, "admin@example.com")
    r = app_client.post(
        "/csf/services",
        headers={"Authorization": f"Bearer {_bearer(admin)}"},
        json={"kind": "nist_csf", "title": "no header"},
    )
    assert r.status_code == 400
    assert "X-Client-Id" in r.text


@pytest.mark.unit
def test_client_cannot_see_another_clients_service(app_client: TestClient) -> None:
    """A client-role user sees only their own tenant's services (404)."""
    admin = register_admin(app_client, "admin@example.com")
    a = _onboard_client(app_client, _bearer(admin), "alpha@a.example")
    b = _onboard_client(app_client, _bearer(admin), "bravo@b.example")
    me_a = _me(app_client, _bearer(a))

    # Admin opens a CSF service under tenant A.
    r = app_client.post(
        "/csf/services",
        headers={
            "Authorization": f"Bearer {_bearer(admin)}",
            "X-Client-Id": me_a["client_id"],
        },
        json={"kind": "nist_csf", "title": "tenant A service"},
    )
    assert r.status_code == 201, r.text
    svc_id = r.json()["id"]

    # Tenant B's user shouldn't see it.
    r = app_client.post(
        f"/csf/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {_bearer(b)}"},
    )
    # Client users hit /assessments which is admin-only -> 403. The point of
    # this test is the data-access path: try the public-to-client "latest".
    r = app_client.get(
        f"/csf/services/{svc_id}/assessments/latest",
        headers={"Authorization": f"Bearer {_bearer(b)}"},
    )
    assert r.status_code == 404
    # And tenant A's user can at least reach it (will be 404 for "no
    # assessment yet" — but a 200/404-no-assessment beats a 404-tenant-deny).
    r = app_client.get(
        f"/csf/services/{svc_id}/assessments/latest",
        headers={"Authorization": f"Bearer {_bearer(a)}"},
    )
    # No assessment exists yet, but the service IS in their tenant.
    assert r.status_code == 404
    assert "No assessment yet" in r.text


@pytest.mark.unit
def test_admin_scoped_to_wrong_tenant_cannot_fetch_service(
    app_client: TestClient,
) -> None:
    """An admin pointed at tenant B can't access tenant A's service by id."""
    admin = register_admin(app_client, "admin@example.com")
    a = _onboard_client(app_client, _bearer(admin), "alpha@a.example")
    b = _onboard_client(app_client, _bearer(admin), "bravo@b.example")
    me_a = _me(app_client, _bearer(a))
    me_b = _me(app_client, _bearer(b))

    r = app_client.post(
        "/csf/services",
        headers={
            "Authorization": f"Bearer {_bearer(admin)}",
            "X-Client-Id": me_a["client_id"],
        },
        json={"kind": "nist_csf", "title": "tenant A service"},
    )
    assert r.status_code == 201
    svc_id = r.json()["id"]

    # Same admin, X-Client-Id pointed at tenant B -> 404.
    r = app_client.post(
        f"/csf/services/{svc_id}/assessments",
        headers={
            "Authorization": f"Bearer {_bearer(admin)}",
            "X-Client-Id": me_b["client_id"],
        },
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_admin_can_list_and_create_clients(app_client: TestClient) -> None:
    """The new /admin/clients endpoints round-trip."""
    admin = register_admin(app_client, "admin@example.com")
    bearer = _bearer(admin)

    # Listing without any client-role registrations yet -> empty.
    r = app_client.get("/admin/clients", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200
    assert r.json() == {"clients": []}

    # Create one.
    r = app_client.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme Holdings, Inc.", "industry": "Energy"},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["legal_name"] == "Acme Holdings, Inc."
    assert created["industry"] == "Energy"

    # And it shows up in the list.
    r = app_client.get("/admin/clients", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 200
    names = [c["legal_name"] for c in r.json()["clients"]]
    assert "Acme Holdings, Inc." in names
