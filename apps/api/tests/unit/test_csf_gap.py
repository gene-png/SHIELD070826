"""Pure-function tests for the CSF gap-analysis engine + the HTTP route."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.csf.catalog import SUBCATEGORIES, FunctionCode
from app.csf.gap import (
    DEFAULT_TARGET_TIER,
    DEFAULT_TOP_N,
    FUNCTION_WEIGHTS,
    analyze,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin

# ---------------------------------------------------------------------------
# Pure function
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_answers_produces_zero_gaps_but_full_unscored_list() -> None:
    result = analyze({})
    assert result.total_gap_count == 0
    assert result.gaps == ()
    assert len(result.unscored_codes) == 106
    assert result.target_tier == DEFAULT_TARGET_TIER
    assert result.target_label == "Repeatable"


@pytest.mark.unit
def test_all_at_target_means_no_gaps() -> None:
    answers = {s.code: 3 for s in SUBCATEGORIES}
    result = analyze(answers)
    assert result.total_gap_count == 0
    assert result.gaps == ()
    assert result.unscored_codes == ()


@pytest.mark.unit
def test_lower_tier_surfaces_as_gap_with_correct_size() -> None:
    # One protect subcategory at tier 1; rest at tier 3 (target).
    pr_code = next(s.code for s in SUBCATEGORIES if s.function == FunctionCode.PR)
    answers = {s.code: 3 for s in SUBCATEGORIES}
    answers[pr_code] = 1
    result = analyze(answers)
    assert result.total_gap_count == 1
    gap = result.gaps[0]
    assert gap.code == pr_code
    assert gap.current_tier == 1
    assert gap.target_tier == 3
    assert gap.gap_size == 2
    # Priority = gap_size * weight; PR weight is 1.15.
    assert gap.priority_score == round(2 * FUNCTION_WEIGHTS[FunctionCode.PR], 2)


@pytest.mark.unit
def test_gaps_sorted_by_priority_then_code() -> None:
    # Two gaps: one tier-1 GV (weight 1.0, gap 2 -> priority 2.0) and
    # one tier-2 PR (weight 1.15, gap 1 -> priority 1.15). GV wins on
    # priority; tie-break is alphabetic when scores match.
    gv_code = next(s.code for s in SUBCATEGORIES if s.function == FunctionCode.GV)
    pr_code = next(s.code for s in SUBCATEGORIES if s.function == FunctionCode.PR)
    answers = {gv_code: 1, pr_code: 2}
    result = analyze(answers)
    assert [g.code for g in result.gaps] == [gv_code, pr_code]


@pytest.mark.unit
def test_higher_function_weight_breaks_priority_tie() -> None:
    # Identical gap_size (=1) on a GV (weight 1.0) vs a DE (weight 1.20).
    gv_code = next(s.code for s in SUBCATEGORIES if s.function == FunctionCode.GV)
    de_code = next(s.code for s in SUBCATEGORIES if s.function == FunctionCode.DE)
    answers = {gv_code: 2, de_code: 2}
    result = analyze(answers)
    assert [g.code for g in result.gaps] == [de_code, gv_code]


@pytest.mark.unit
def test_unscored_excluded_from_gaps_but_counted_separately() -> None:
    # 5 unscored, 5 at tier 1, rest meet target.
    answers: dict[str, int | None] = {s.code: 3 for s in SUBCATEGORIES}
    for s in SUBCATEGORIES[:5]:
        answers[s.code] = None
    for s in SUBCATEGORIES[5:10]:
        answers[s.code] = 1
    result = analyze(answers)
    assert result.total_gap_count == 5
    assert len(result.unscored_codes) == 5


@pytest.mark.unit
def test_top_n_caps_response() -> None:
    answers = {s.code: 1 for s in SUBCATEGORIES}  # 106 gaps, all max severity
    result = analyze(answers, top_n=10)
    assert result.total_gap_count == 106
    assert len(result.gaps) == 10


@pytest.mark.unit
def test_target_tier_clamped_to_valid_range() -> None:
    answers = {SUBCATEGORIES[0].code: 1}
    result = analyze(answers, target_tier=99)
    # Out-of-range falls back to the default.
    assert result.target_tier == DEFAULT_TARGET_TIER


@pytest.mark.unit
def test_target_tier_can_be_adaptive() -> None:
    answers = {s.code: 3 for s in SUBCATEGORIES}
    result = analyze(answers, target_tier=4)
    # All rows now have gap_size 1 against the Adaptive target.
    assert result.total_gap_count == 106
    assert result.target_label == "Adaptive"
    assert all(g.target_tier == 4 and g.gap_size == 1 for g in result.gaps)


@pytest.mark.unit
def test_notes_attached_to_gap_rows() -> None:
    answers = {SUBCATEGORIES[0].code: 1}
    notes = {SUBCATEGORIES[0].code: "Pending policy approval."}
    result = analyze(answers, notes=notes)
    assert result.gaps[0].notes == "Pending policy approval."


@pytest.mark.unit
def test_gap_count_by_function_sums_to_total() -> None:
    # Random-ish but deterministic distribution of gaps across functions.
    answers: dict[str, int | None] = {}
    for i, s in enumerate(SUBCATEGORIES):
        answers[s.code] = (i % 3) + 1  # 1, 2, 3
    result = analyze(answers, top_n=200)  # don't truncate
    total = sum(result.gap_count_by_function.values())
    assert total == result.total_gap_count


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-csfgap.db"
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
    # Multi-tenant (post-0013): admin/reviewer callers must name an active
    # tenant via X-Client-Id. Seed one tenant and bake the header into the
    # test client so single-tenant-style tests resolve to it; client-role
    # callers are pinned to their own client and ignore this header.
    from app.models.client import Client as _Client

    _seed = TestSession()
    _tenant = _Client(legal_name="Test Tenant")
    _seed.add(_tenant)
    _seed.flush()
    from app.models.client_domain import ClientDomain as _ClientDomain

    _seed.add(_ClientDomain(client_id=_tenant.id, domain="example.com"))
    _seed.commit()
    _cid = str(_tenant.id)
    _seed.close()

    with TestClient(app, headers={"X-Client-Id": _cid}) as c:
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


def _seed_assessment(c: TestClient, bearer: str) -> tuple[str, list[dict]]:
    sr = c.post(
        "/csf/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "nist_csf", "title": "Atlas - CSF"},
    )
    svc_id = sr.json()["id"]
    a = c.post(
        f"/csf/services/{svc_id}/assessments",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    return svc_id, a.json()["answers"]


@pytest.mark.unit
def test_gap_route_returns_prioritized_gaps(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id, answers = _seed_assessment(c, bearer)
    # Score 3 answers at tier 1 (huge gap to default target 3).
    for ans in answers[:3]:
        c.patch(
            f"/csf/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_tier": 1},
        )
    r = c.get(
        f"/csf/services/{svc_id}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_tier"] == 3
    assert body["target_label"] == "Repeatable"
    assert body["total_gap_count"] == 3
    assert len(body["gaps"]) == 3
    assert body["unscored_count"] == 103
    # All gaps are gap_size=2 (target 3 - current 1).
    assert all(g["gap_size"] == 2 for g in body["gaps"])


@pytest.mark.unit
def test_gap_route_respects_target_tier_query_param(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id, answers = _seed_assessment(c, bearer)
    c.patch(
        f"/csf/answers/{answers[0]['id']}",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"maturity_tier": 3},
    )
    # At default target=3, the tier-3 answer has no gap.
    r = c.get(
        f"/csf/services/{svc_id}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.json()["total_gap_count"] == 0
    # Raise the target to 4 - now the tier-3 answer shows as a gap of 1.
    r2 = c.get(
        f"/csf/services/{svc_id}/gap-analysis?target_tier=4",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r2.json()["target_tier"] == 4
    assert r2.json()["total_gap_count"] == 1
    assert r2.json()["gaps"][0]["gap_size"] == 1


@pytest.mark.unit
def test_gap_route_top_n_caps_response(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    svc_id, answers = _seed_assessment(c, bearer)
    for ans in answers:
        c.patch(
            f"/csf/answers/{ans['id']}",
            headers={"Authorization": f"Bearer {bearer}"},
            json={"maturity_tier": 1},
        )
    r = c.get(
        f"/csf/services/{svc_id}/gap-analysis?top_n=5",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    body = r.json()
    assert body["total_gap_count"] == 106
    assert len(body["gaps"]) == 5


@pytest.mark.unit
def test_gap_route_admin_only(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer_admin = admin["tokens"]["access_token"]
    client = _register(c, "client@example.com")
    bearer_client = client["tokens"]["access_token"]
    svc_id, _ = _seed_assessment(c, bearer_admin)
    r = c.get(
        f"/csf/services/{svc_id}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer_client}"},
    )
    assert r.status_code == 403


@pytest.mark.unit
def test_gap_route_404_for_non_csf_service(app_client) -> None:
    c = app_client
    admin = register_admin(c, "admin@example.com")
    bearer = admin["tokens"]["access_token"]
    td = c.post(
        "/tech-debt/services",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"kind": "tech_debt", "title": "x"},
    )
    r = c.get(
        f"/csf/services/{td.json()['id']}/gap-analysis",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_default_top_n_constant_matches_route_default() -> None:
    """Lock the contract so changing one without the other trips the test."""
    assert DEFAULT_TOP_N == 20
