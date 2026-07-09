"""Admin AI surfaces: ai-status validation (A-5) + ai-usage cost report (H-5)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from app.config import Settings
from app.models.client import Client
from app.models.llm_call import LLMCall, LLMCallMode, LLMCallStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin


@pytest.fixture()
def admin_app(tmp_path) -> Iterator[SimpleNamespace]:
    url = f"sqlite:///{tmp_path / 'shield-adminai.db'}"
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
    from app.models.client_domain import ClientDomain

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    seed = TestSession()
    tenant = Client(legal_name="(pending intake)")
    seed.add(tenant)
    seed.flush()
    seed.add(ClientDomain(client_id=tenant.id, domain="example.com"))
    seed.commit()
    seed.close()

    with TestClient(app) as c:
        yield SimpleNamespace(client=c, Session=TestSession)


def _admin_headers(c: TestClient) -> dict:
    tok = register_admin(c, "admin@example.com")["tokens"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# --- A-5: ai-status validates live config in all three states --------------


@pytest.mark.unit
def test_ai_status_fixture_state(admin_app, monkeypatch) -> None:
    h = _admin_headers(admin_app.client)
    monkeypatch.setattr(
        "app.routes.admin.get_settings",
        lambda: Settings(shield_llm_mode="fixture"),
    )
    body = admin_app.client.get("/admin/ai-status", headers=h).json()
    assert body["mode"] == "fixture"
    assert body["ready"] is False
    # E-5: fixtures are SIMULATED, not "disabled".
    assert "simulated" in body["detail"].lower()
    assert "disabled" not in body["detail"].lower()
    # A-5: per-job models come from the registry; the Haiku-pinned jobs show it.
    assert body["job_models"]["csf_score"] == "claude-haiku-4-5"
    assert body["job_models"]["mitre_map"] == "claude-haiku-4-5"
    assert set(body["job_models"]) >= {
        "tech_debt_extract",
        "csf_score",
        "zt_score",
        "mitre_map",
        "risk_synthesize",
    }
    # The key itself is never returned.
    assert "anthropic_api_key" not in body and "api_key" not in body


@pytest.mark.unit
def test_ai_status_live_ready_state(admin_app, monkeypatch) -> None:
    h = _admin_headers(admin_app.client)
    monkeypatch.setattr(
        "app.routes.admin.get_settings",
        lambda: Settings(shield_llm_mode="live", anthropic_api_key="sk-test-key"),
    )
    body = admin_app.client.get("/admin/ai-status", headers=h).json()
    assert body["mode"] == "live"
    assert body["ready"] is True
    assert body["api_key_present"] is True
    assert body["sdk_importable"] is True


@pytest.mark.unit
def test_ai_status_live_misconfigured_state(admin_app, monkeypatch) -> None:
    h = _admin_headers(admin_app.client)
    monkeypatch.setattr(
        "app.routes.admin.get_settings",
        lambda: Settings(shield_llm_mode="live", anthropic_api_key=""),
    )
    body = admin_app.client.get("/admin/ai-status", headers=h).json()
    assert body["mode"] == "live"
    assert body["ready"] is False
    assert body["api_key_present"] is False
    assert "ANTHROPIC_API_KEY" in body["detail"]


# --- H-5: ai-usage math + unknown-model null cost --------------------------


def _seed_call(
    db: Session,
    *,
    client_id: uuid.UUID | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
    when: datetime,
) -> None:
    db.add(
        LLMCall(
            client_id=client_id,
            purpose="csf_score",
            prompt_version="v1",
            provider="anthropic",
            model=model,
            mode=LLMCallMode.LIVE,
            status=LLMCallStatus.COMPLETED,
            requested_by=uuid.uuid4(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            requested_at=when,
        )
    )


@pytest.mark.unit
def test_ai_usage_math_and_unknown_model(admin_app) -> None:
    h = _admin_headers(admin_app.client)
    when = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    with admin_app.Session() as db:
        cust = Client(legal_name="Acme Corp")
        db.add(cust)
        db.flush()
        cid = cust.id
        # Two sonnet calls in the same month: totals 1,000,000 in / 200,000 out.
        _seed_call(
            db,
            client_id=cid,
            model="claude-sonnet-5",
            input_tokens=500_000,
            output_tokens=100_000,
            when=when,
        )
        _seed_call(
            db,
            client_id=cid,
            model="claude-sonnet-5",
            input_tokens=500_000,
            output_tokens=100_000,
            when=when,
        )
        # One call on a model with no price on file -> null cost.
        _seed_call(
            db,
            client_id=cid,
            model="mystery-model-9",
            input_tokens=1_000,
            output_tokens=1_000,
            when=when,
        )
        db.commit()

    body = admin_app.client.get("/admin/ai-usage", headers=h).json()
    rows = body["rows"]

    sonnet = next(r for r in rows if r["model"] == "claude-sonnet-5" and r["client_id"] == str(cid))
    assert sonnet["month"] == "2026-05"
    assert sonnet["client_name"] == "Acme Corp"
    assert sonnet["calls"] == 2
    assert sonnet["input_tokens"] == 1_000_000
    assert sonnet["output_tokens"] == 200_000
    # 1.0M in * $3 + 0.2M out * $15 = 3.00 + 3.00 = 6.00
    assert sonnet["estimated_cost_usd"] == pytest.approx(6.00)
    assert sonnet["cost_estimated"] is True

    mystery = next(r for r in rows if r["model"] == "mystery-model-9")
    assert mystery["estimated_cost_usd"] is None
    assert mystery["cost_estimated"] is False
    assert mystery["note"] and "mystery-model-9" in mystery["note"]


@pytest.mark.unit
def test_ai_usage_admin_only(admin_app) -> None:
    # A client-role token is rejected.
    reg = admin_app.client.post(
        "/auth/register",
        json={
            "email": "client@example.com",
            "password": "correct horse battery staple!",
            "display_name": "client",
        },
    )
    assert reg.status_code == 201, reg.text
    client_tok = reg.json()["tokens"]["access_token"]
    resp = admin_app.client.get(
        "/admin/ai-usage", headers={"Authorization": f"Bearer {client_tok}"}
    )
    assert resp.status_code == 403
