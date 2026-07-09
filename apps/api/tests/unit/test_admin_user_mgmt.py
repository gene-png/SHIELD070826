"""Admin user-management endpoints + bootstrap admin tests."""

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

PASSWORD = "correct horse battery staple!"


@pytest.fixture()
def ctx(tmp_path) -> Iterator[tuple[TestClient, sessionmaker]]:
    db_path = tmp_path / "shield-admin-users.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    test_engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app
    from app.models._common import utcnow
    from app.models.user import User, UserRole
    from app.security.password import hash_password

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    # Seed the platform admin directly (the only way admins exist now).
    seed = TestSession()
    seed.add(
        User(
            email="admin@kentro.example",
            password_hash=hash_password(PASSWORD),
            role=UserRole.ADMIN,
            display_name="Seed Admin",
            timezone="UTC",
            last_login_at=utcnow(),
        )
    )
    seed.commit()
    seed.close()

    with TestClient(app) as c:
        yield c, TestSession


def _admin_token(client: TestClient) -> str:
    r = client.post("/auth/login", json={"email": "admin@kentro.example", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
def test_self_registration_is_client_not_admin(ctx) -> None:
    client, _ = ctx
    r = client.post(
        "/auth/register",
        json={"email": "someone@newco.com", "password": PASSWORD, "display_name": "Some One"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["role"] == "client"


@pytest.mark.unit
def test_admin_can_create_admin(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    r = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "new.admin@kentro.example",
            "password": PASSWORD,
            "display_name": "New Admin",
            "role": "admin",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "admin"
    assert body["client_id"] is None


@pytest.mark.unit
def test_create_client_requires_existing_client_id(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    # Missing client_id -> 422.
    r = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "c1@acme.com",
            "password": PASSWORD,
            "display_name": "C1",
            "role": "client",
        },
    )
    assert r.status_code == 422

    # Create a client tenant, then a client user attached to it.
    created = client.post("/admin/clients", headers=_h(token), json={"legal_name": "Acme Corp"})
    cid = created.json()["id"]
    r = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "c1@acme.com",
            "password": PASSWORD,
            "display_name": "C1",
            "role": "client",
            "client_id": cid,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["client_id"] == cid


@pytest.mark.unit
def test_admin_with_client_id_rejected(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    created = client.post("/admin/clients", headers=_h(token), json={"legal_name": "Acme Corp"})
    cid = created.json()["id"]
    r = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "bad.admin@kentro.example",
            "password": PASSWORD,
            "display_name": "Bad",
            "role": "admin",
            "client_id": cid,
        },
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_create_user_duplicate_email_conflict(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    r = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "admin@kentro.example",
            "password": PASSWORD,
            "display_name": "Dup",
            "role": "admin",
        },
    )
    assert r.status_code == 409


@pytest.mark.unit
def test_create_user_weak_password_422(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    r = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "weak@kentro.example",
            "password": "short",
            "display_name": "Weak",
            "role": "admin",
        },
    )
    assert r.status_code == 422


@pytest.mark.unit
def test_non_admin_cannot_list_or_create_users(ctx) -> None:
    client, _ = ctx
    reg = client.post(
        "/auth/register",
        json={"email": "u@newco.com", "password": PASSWORD, "display_name": "U"},
    )
    client_token = reg.json()["tokens"]["access_token"]
    assert client.get("/admin/users", headers=_h(client_token)).status_code == 403
    assert (
        client.post(
            "/admin/users",
            headers=_h(client_token),
            json={
                "email": "x@kentro.example",
                "password": PASSWORD,
                "display_name": "X",
                "role": "admin",
            },
        ).status_code
        == 403
    )


@pytest.mark.unit
def test_cannot_deactivate_self(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    me = client.get("/auth/me", headers=_h(token)).json()
    r = client.delete(f"/admin/users/{me['id']}", headers=_h(token))
    assert r.status_code == 400


@pytest.mark.unit
def test_cannot_remove_the_last_admin(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)  # the seed admin is the only admin
    me = client.get("/auth/me", headers=_h(token)).json()

    # The sole admin can't deactivate themselves (would orphan the platform).
    assert client.delete(f"/admin/users/{me['id']}", headers=_h(token)).status_code == 400

    # Add a second admin; deactivating it is fine (one admin still remains).
    second = client.post(
        "/admin/users",
        headers=_h(token),
        json={
            "email": "second.admin@kentro.example",
            "password": PASSWORD,
            "display_name": "Second",
            "role": "admin",
        },
    ).json()
    assert client.delete(f"/admin/users/{second['id']}", headers=_h(token)).status_code == 204

    # Back to a single admin, who still cannot remove themselves, so at least one
    # active admin is always preserved.
    assert client.delete(f"/admin/users/{me['id']}", headers=_h(token)).status_code == 400
    users = client.get("/admin/users", headers=_h(token)).json()["users"]
    active_admins = [u for u in users if u["role"] == "admin" and u["is_active"]]
    assert len(active_admins) == 1


@pytest.mark.unit
def test_deactivate_then_reactivate(ctx) -> None:
    client, _ = ctx
    token = _admin_token(client)
    reg = client.post(
        "/auth/register",
        json={"email": "client@newco.com", "password": PASSWORD, "display_name": "Client"},
    )
    uid = reg.json()["user"]["id"]
    # Deactivate -> can't log in.
    assert client.delete(f"/admin/users/{uid}", headers=_h(token)).status_code == 204
    login = client.post("/auth/login", json={"email": "client@newco.com", "password": PASSWORD})
    assert login.status_code == 401
    # Reactivate -> can log in again.
    r = client.post(f"/admin/users/{uid}/reactivate", headers=_h(token))
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    assert (
        client.post(
            "/auth/login", json={"email": "client@newco.com", "password": PASSWORD}
        ).status_code
        == 200
    )
