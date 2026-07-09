"""Backend AI-remediation invariants (E-2, E-1a, E-1b, H-5).

E-2: a failed provider call must leave a durable llm_calls audit row even though
     the request transaction rolls back — proven by reading a FRESH session.
E-1a: the pooled DB connection is returned to the pool across the provider call.
E-1b: a provider timeout maps to a typed 504 and applies nothing.
H-5: every job type's llm_calls row carries a client_id.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.config import Config
from app.ai.engine import registered_jobs, run_job
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.models.client import Client
from app.models.llm_call import LLMCall, LLMCallStatus
from app.models.service import Service, ServiceKind, ServiceStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


def _upgrade(url: str) -> None:
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture()
def csf_ai(tmp_path) -> Iterator[SimpleNamespace]:
    """A CSF Run-AI harness exposing the TestClient, the fixture provider, the
    test engine (for pool assertions) and the session factory (for fresh reads).

    raise_server_exceptions=False so an unhandled provider error surfaces as the
    app's real 500 response rather than being re-raised into the test."""
    url = f"sqlite:///{tmp_path / 'shield-airem.db'}"
    os.environ["DATABASE_URL"] = url
    _upgrade(url)
    engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app
    from app.models.client_domain import ClientDomain
    from app.routes.csf import _llm_dep

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

    seed = TestSession()
    tenant = Client(legal_name="Test Tenant")
    seed.add(tenant)
    seed.flush()
    seed.add(ClientDomain(client_id=tenant.id, domain="example.com"))
    seed.commit()
    tenant_id = tenant.id
    seed.close()

    with TestClient(
        app, headers={"X-Client-Id": str(tenant_id)}, raise_server_exceptions=False
    ) as c:
        yield SimpleNamespace(
            client=c,
            provider=provider,
            engine=engine,
            Session=TestSession,
            tenant_id=tenant_id,
        )


def _bootstrap(c: TestClient) -> tuple[dict, str]:
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})
    return h, svc_id


# --- E-2 -------------------------------------------------------------------


@pytest.mark.unit
def test_failed_llm_call_survives_request_rollback(csf_ai) -> None:
    """THE E-2 test: a provider crash 500s the request (transaction rolls back),
    yet a FRESH session still finds the FAILED llm_calls row with its error +
    duration. This is exactly what the old flush-only code lost."""
    c, provider = csf_ai.client, csf_ai.provider
    h, svc_id = _bootstrap(c)

    def boom(_payload: dict) -> LLMResponse:
        raise RuntimeError("provider exploded")

    provider.register("csf_score", boom)
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 500, r.text

    # Read from a session opened AFTER the request already rolled back.
    with csf_ai.Session() as fresh:
        failed = [
            x
            for x in fresh.execute(select(LLMCall)).scalars().all()
            if x.status == LLMCallStatus.FAILED
        ]
        assert failed, "the FAILED llm_calls row must survive the request rollback"
        row = failed[0]
        assert "provider exploded" in row.error_message
        assert row.duration_ms is not None and row.duration_ms >= 0
        assert row.purpose == "csf_score"


# --- E-1a ------------------------------------------------------------------


@pytest.mark.unit
def test_pooled_connection_released_across_provider_call(csf_ai) -> None:
    """The synchronous provider call must NOT hold a pooled DB connection: the
    route returns it before calling, and invoke's audit session releases its own
    after the RUNNING commit. Prove it: zero connections checked out at call time."""
    c, provider, engine = csf_ai.client, csf_ai.provider, csf_ai.engine
    h, svc_id = _bootstrap(c)
    seen: dict[str, int] = {}

    def probe(_payload: dict) -> LLMResponse:
        seen["checkedout"] = engine.pool.checkedout()
        return LLMResponse('{"scores": []}')

    provider.register("csf_score", probe)
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    assert seen.get("checkedout") == 0, (
        "a pooled connection was held across the provider call "
        f"(checkedout={seen.get('checkedout')})"
    )


# --- E-1b ------------------------------------------------------------------


@pytest.mark.unit
def test_provider_timeout_maps_to_504_and_applies_nothing(csf_ai) -> None:
    import anthropic

    c, provider = csf_ai.client, csf_ai.provider
    h, svc_id = _bootstrap(c)

    def timed_out(_payload: dict) -> LLMResponse:
        raise anthropic.APITimeoutError(httpx.Request("POST", "http://anthropic.local"))

    provider.register("csf_score", timed_out)
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 504, r.text
    assert "timed out" in r.json()["error"]["message"]

    # Nothing applied: every high-tier row is still at its seeded 0 default.
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    assert rows
    assert all(x["governance"] == 0 and x["policy"] == 0 for x in rows)

    # And the timeout was still recorded as a FAILED audit row.
    with csf_ai.Session() as fresh:
        failed = [
            x
            for x in fresh.execute(select(LLMCall)).scalars().all()
            if x.status == LLMCallStatus.FAILED
        ]
        assert failed


# --- H-5: client_id lands for every job type -------------------------------


@pytest.fixture()
def engine_db(tmp_path) -> Iterator[sessionmaker]:
    url = f"sqlite:///{tmp_path / 'shield-h5.db'}"
    os.environ["DATABASE_URL"] = url
    _upgrade(url)
    engine = create_engine(url, future=True)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _fixture_client() -> LLMClient:
    provider = FixtureProvider()
    # One default fixture serves every job: "{}" parses to {} for the parse_json
    # jobs and to [] for the tech-debt extract parser (no "items" key).
    provider.register_static("default", LLMResponse("{}"))
    return LLMClient(provider)


@pytest.mark.unit
@pytest.mark.parametrize("job_name", sorted(registered_jobs()))
def test_client_id_lands_on_every_job_type_explicit(engine_db, job_name: str) -> None:
    """The four run-ai/generate routes pass client_id explicitly; every job type
    must persist it on the llm_calls row."""
    cid = uuid.uuid4()
    with engine_db() as db:
        run_job(
            db,
            _fixture_client(),
            job_name,
            inputs={"x": 1},
            requested_by=uuid.uuid4(),
            client_id=cid,
        )
    with engine_db() as fresh:
        row = fresh.execute(select(LLMCall)).scalars().one()
        assert row.client_id == cid


@pytest.mark.unit
def test_client_id_derived_from_service_when_not_passed(engine_db) -> None:
    """The tech-debt extract path (in off-limits code) passes service_id but not
    client_id; invoke must derive the tenant from the service so that job type
    still attributes its spend."""
    with engine_db() as db:
        client = Client(legal_name="Acme")
        db.add(client)
        db.flush()
        svc = Service(
            kind=ServiceKind.TECH_DEBT,
            status=ServiceStatus.IN_PROGRESS,
            title="TD",
            client_id=client.id,
            opened_by=uuid.uuid4(),
        )
        db.add(svc)
        db.commit()
        client_id, service_id = client.id, svc.id

    with engine_db() as db:
        run_job(
            db,
            _fixture_client(),
            "tech_debt_extract",
            inputs={"rows": []},
            requested_by=uuid.uuid4(),
            service_id=service_id,
            # client_id intentionally omitted — must be derived.
        )

    with engine_db() as fresh:
        row = fresh.execute(select(LLMCall)).scalars().one()
        assert row.service_id == service_id
        assert row.client_id == client_id
