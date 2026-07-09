"""Risk Register route: enum normalization + drift visibility (defect A-4).

In live mode an AI-drafted entry whose likelihood/impact used display casing
("Very Low") or a hyphen ("Very-High") coerced to None, which blanked the
code-derived tier with no signal to anyone. These tests pin the route-side fix:
_enum_or_none normalizes casing/whitespace/separators before coercing, and the
generate response carries a warning when any enum fails to coerce.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-risk-routes.db'}"
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
    from app.routes.artifacts import _storage_dep
    from app.routes.risk import _llm_dep
    from app.storage.local import LocalFilesystemStorage

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_llm_dep] = lambda: LLMClient(provider)
    app.dependency_overrides[_storage_dep] = lambda: storage
    with TestClient(app) as c:
        yield c, provider


def _admin(c: TestClient) -> tuple[str, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    return bearer, cid


def _seed_attack_and_zt(c: TestClient, bearer: str, cid: str) -> tuple[str, str]:
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    asvc = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    )
    a = c.post(f"/attack/services/{asvc.json()['id']}/assessments", headers=h)
    cov = a.json()["coverage"][0]
    technique = cov["technique_code"]
    c.patch(f"/attack/coverage/{cov['id']}", headers=h, json={"status": "gap"})

    zsvc = c.post("/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"})
    za = c.post(f"/zt/services/{zsvc.json()['id']}/assessments", headers=h)
    zans = za.json()["answers"][0]
    capability = zans["capability_code"]
    c.patch(f"/zt/answers/{zans['id']}", headers=h, json={"maturity_stage": 1})
    return technique, capability


@pytest.mark.unit
def test_display_cased_enums_coerce_and_derive_tier(app_client) -> None:
    """(a) Display-cased labels with spaces coerce and yield a non-None tier.

    "Very Low" likelihood + "Catastrophic" impact must reach the engine and
    produce a code-derived tier (this is exactly the case the bug nulled).
    """
    c, provider = app_client
    bearer, cid = _admin(c)
    _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}

    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Legacy exposure",'
            ' "likelihood": "Very Low", "impact": "Catastrophic",'
            ' "axis": "detection", "recommended_action": "remediate"}]}'
        ),
    )
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    body = r.json()
    e = body["entries"][0]
    # Normalized to canonical tokens...
    assert e["likelihood"] == "very_low"
    assert e["impact"] == "catastrophic"
    # ...and the tier is code-derived, not None. (very_low x catastrophic:
    # score (0+1)*(4+1) = 5 -> Low.)
    assert e["tier"] is not None
    assert e["tier"] == "low"
    # A clean coercion raises no warning.
    assert body["coercion_failures"] == 0
    assert body["warnings"] == []


@pytest.mark.unit
def test_hyphenated_case_also_normalizes(app_client) -> None:
    """A hyphen + mixed case ("Very-High") normalizes to very_high."""
    c, provider = app_client
    bearer, cid = _admin(c)
    _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}

    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Ransomware",'
            ' "likelihood": " Very-High ", "impact": "Major",'
            ' "recommended_action": "remediate"}]}'
        ),
    )
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    e = r.json()["entries"][0]
    assert e["likelihood"] == "very_high"
    assert e["impact"] == "major"
    # very_high x major -> Critical (special-cased in the engine).
    assert e["tier"] == "critical"


@pytest.mark.unit
def test_unknown_token_returns_none_and_warns(app_client) -> None:
    """(b) A genuinely unknown token is NOT fabricated, and the drift surfaces."""
    c, provider = app_client
    bearer, cid = _admin(c)
    _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}

    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Mystery risk",'
            ' "likelihood": "extremely_high", "impact": "catastrophic",'
            ' "recommended_action": "remediate"}]}'
        ),
    )
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    body = r.json()
    e = body["entries"][0]
    # Normalization must not invent a value for unknown vocabulary.
    assert e["likelihood"] is None
    # No likelihood -> no code-derived tier.
    assert e["tier"] is None
    # The drift is now VISIBLE.
    assert body["coercion_failures"] == 1
    assert len(body["warnings"]) == 1
    assert "did not recognize" in body["warnings"][0]


@pytest.mark.unit
def test_canonical_lowercase_tokens_still_work(app_client) -> None:
    """(c) No regression: canonical snake_case tokens coerce and warn-free."""
    c, provider = app_client
    bearer, cid = _admin(c)
    _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}

    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Phishing",'
            ' "likelihood": "high", "impact": "catastrophic",'
            ' "axis": "detection", "recommended_action": "remediate"}]}'
        ),
    )
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    body = r.json()
    e = body["entries"][0]
    assert e["likelihood"] == "high"
    assert e["impact"] == "catastrophic"
    assert e["tier"] == "critical"
    assert body["coercion_failures"] == 0
    assert body["warnings"] == []
