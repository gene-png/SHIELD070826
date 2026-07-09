"""LLMClient invariants: redaction-before-egress + audit-row-on-every-call."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.config import get_settings
from app.models.llm_call import LLMCall, LLMCallMode, LLMCallStatus
from app.models.user import User, UserRole
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def db_factory(tmp_path) -> Iterator[sessionmaker]:
    db_path = tmp_path / "shield-llm.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _new_admin(db: Session) -> User:
    u = User(
        email="admin@example.com",
        password_hash="x" * 64,
        role=UserRole.ADMIN,
        display_name="Admin",
    )
    db.add(u)
    db.flush()
    return u


@pytest.mark.unit
def test_invoke_writes_llm_call_row_with_completed_status(db_factory) -> None:
    provider = FixtureProvider()
    captured: dict = {}

    def fake(payload: dict) -> LLMResponse:
        captured.update(payload)
        return LLMResponse("ok", input_tokens=12, output_tokens=34)

    provider.register("extract.capabilities", fake)
    client = LLMClient(provider, settings=get_settings())

    with db_factory() as db:
        admin = _new_admin(db)
        response, row = client.invoke(
            db,
            purpose="extract.capabilities",
            prompt="Extract the capability list.",
            payload={
                "filename": "inventory.csv",
                "contact": "alice@example.gov",
                "ssn": "123-45-6789",
            },
            requested_by=admin.id,
        )
        db.commit()

        assert response.content == "ok"
        assert row.status == LLMCallStatus.COMPLETED
        assert row.input_tokens == 12
        assert row.output_tokens == 34
        assert row.purpose == "extract.capabilities"
        assert row.provider == "fixture"
        assert row.mode == LLMCallMode.FIXTURE
        assert row.requested_by == admin.id
        # Redacted counts captured.
        assert row.redacted_counts is not None
        assert row.redacted_counts["email"] == 1
        assert row.redacted_counts["ssn"] == 1

    # Provider received the REDACTED payload, never the raw one.
    assert "alice@example.gov" not in captured.values()
    assert "123-45-6789" not in captured.values()
    assert captured["contact"] == "[EMAIL]"
    assert captured["ssn"] == "[SSN]"


@pytest.mark.unit
def test_invoke_records_failure_with_error_message(db_factory) -> None:
    provider = FixtureProvider()

    def boom(_payload: dict) -> LLMResponse:
        raise RuntimeError("upstream down")

    provider.register("extract.capabilities", boom)
    client = LLMClient(provider)

    with db_factory() as db:
        admin = _new_admin(db)
        with pytest.raises(RuntimeError, match="upstream down"):
            client.invoke(
                db,
                purpose="extract.capabilities",
                prompt="x",
                payload={"a": 1},
                requested_by=admin.id,
            )
        db.commit()

        row = db.execute(select(LLMCall)).scalar_one()
        assert row.status == LLMCallStatus.FAILED
        assert "upstream down" in row.error_message
        # Duration was still recorded so debugging "slow failures" is possible.
        assert row.duration_ms is not None and row.duration_ms >= 0


@pytest.mark.unit
def test_invoke_routes_redacted_payload_in_dict_keys_preserved(db_factory) -> None:
    """Field names like "email" stay readable; only values redact."""
    provider = FixtureProvider()
    seen: dict = {}

    def cap(payload: dict) -> LLMResponse:
        seen.update(payload)
        return LLMResponse("ok")

    provider.register("default", cap)
    client = LLMClient(provider)
    with db_factory() as db:
        admin = _new_admin(db)
        client.invoke(
            db,
            purpose="extract.capabilities",
            prompt="x",
            payload={"poc_email": "a@b.gov", "poc_phone": "555-867-5309"},
            requested_by=admin.id,
        )
    assert "poc_email" in seen and seen["poc_email"] == "[EMAIL]"
    assert "poc_phone" in seen and seen["poc_phone"] == "[PHONE]"


@pytest.mark.unit
def test_fixture_provider_raises_when_purpose_unregistered(db_factory) -> None:
    """Forgetting to register a fixture fails loudly, not silently."""
    provider = FixtureProvider()  # no registrations
    client = LLMClient(provider)
    with db_factory() as db:
        admin = _new_admin(db)
        with pytest.raises(KeyError, match="No fixture registered"):
            client.invoke(
                db,
                purpose="some.unregistered",
                prompt="x",
                payload={},
                requested_by=admin.id,
            )
        db.rollback()


@pytest.mark.unit
def test_correlation_id_threaded_through_llm_call_row(db_factory) -> None:
    from app.logging import correlation_id_var

    provider = FixtureProvider()
    provider.register("p", lambda _p: LLMResponse("ok"))
    client = LLMClient(provider)
    token = correlation_id_var.set("cid-llm-001")
    try:
        with db_factory() as db:
            admin = _new_admin(db)
            client.invoke(
                db,
                purpose="p",
                prompt="x",
                payload={},
                requested_by=admin.id,
            )
            db.commit()
            row = db.execute(select(LLMCall)).scalar_one()
            assert row.correlation_id == "cid-llm-001"
    finally:
        correlation_id_var.reset(token)
