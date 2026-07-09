"""Test fixtures."""

from __future__ import annotations

import os

import pytest

# Keep tests fully offline: no DB, no Redis, no LLM. Routes that need those
# will set up their own ephemeral resources in later stages.
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SHIELD_LLM_MODE", "fixture")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
# Tests override the DB session per-test; the startup maintenance job (bootstrap
# admin + retention purge) would otherwise hit the module-level engine.
os.environ.setdefault("SHIELD_RUN_STARTUP_MAINTENANCE", "false")


@pytest.fixture()
def client():
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as c:
        yield c


def register_admin(
    client,
    email: str = "admin@example.com",
    password: str = "correct horse battery staple!",
) -> dict:
    """Register a user, then promote them to a cross-tenant admin in the DB.

    Self-registration only ever creates `client` users now, but many tests need
    an admin. Authorization is DB-role-based (require_role re-loads the user via
    current_user), so promoting the row is enough - the issued token keeps
    working. Promotion uses a throwaway engine bound to the per-test
    DATABASE_URL the calling fixture set.
    """
    import os
    import uuid

    from app.models.client import Client
    from app.models.client_domain import ClientDomain
    from app.models.user import User, UserRole
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import Session

    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    orig_client_id = body["user"]["client_id"]
    # is_primary_poc is True iff registration created a brand-new org for this
    # user. Platform admins are cross-tenant with no org, so we tear that org
    # back down to match the old "first user is admin, no org" baseline.
    created_org = bool(body["is_primary_poc"])

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine, future=True) as s:
        user = s.get(User, uuid.UUID(body["user"]["id"]))
        user.role = UserRole.ADMIN
        user.client_id = None
        s.flush()
        if created_org and orig_client_id:
            cid = uuid.UUID(orig_client_id)
            s.execute(delete(ClientDomain).where(ClientDomain.client_id == cid))
            org = s.get(Client, cid)
            if org is not None:
                s.delete(org)
        s.commit()
    engine.dispose()

    body["user"]["role"] = "admin"
    body["user"]["client_id"] = None
    body["is_primary_poc"] = True
    return body


class _AdminResp:
    """Response-shaped wrapper so inline `c.post('/auth/register', ...)` call
    sites can swap to `register_admin_resp(c, ...)` without rewriting their
    `.json()` / `.status_code` access."""

    status_code = 201

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


def register_admin_resp(
    client,
    email: str = "admin@example.com",
    password: str = "correct horse battery staple!",
) -> _AdminResp:
    return _AdminResp(register_admin(client, email, password))
