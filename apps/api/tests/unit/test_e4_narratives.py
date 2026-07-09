"""FIX E-4: the AI narrative work must survive a reload.

ZT Run AI returned pillar narratives, an executive summary and a roadmap
summary; the ATT&CK ``mitre_map`` prompt asks for an executive summary and top
blind spots. Neither was ever stored. The consultant saw them once, reloaded
the page, and lost them -- which quietly pushed people into re-running the AI
(and re-paying for the output tokens) just to read the narrative again.

These tests assert the values are readable from a FRESH session after the
request that produced them has finished. Asserting only on the HTTP response
would pass against the old, broken code -- the response always carried the
narrative; it was the database that never did.
"""

from __future__ import annotations

import pytest
from app.ai.llm import LLMResponse
from sqlalchemy import select

from tests.unit.test_zt_run_ai import _admin_service, app_client  # noqa: F401

pytestmark = pytest.mark.unit


def test_zt_run_ai_persists_narratives_across_a_reload(app_client) -> None:  # noqa: F811
    c, provider = app_client
    h, svc_id, _ = _admin_service(c, "zero_trust_cisa")
    a = c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    assessment_id = a.json()["id"]
    code = a.json()["answers"][0]["capability_code"]

    provider.register_static(
        "zt_score",
        LLMResponse(
            '{"capabilities": [{"code": "' + code + '", "current": 2, "target": 4}],'
            ' "pillar_narratives": {"ID": "Identity is partial."},'
            ' "executive_summary": "Exec draft.", "roadmap_summary": "12-month plan"}'
        ),
    )
    assert c.post(f"/zt/services/{svc_id}/run-ai", headers=h).status_code == 200

    # The HTTP response has ALWAYS carried these; it was the database that never
    # did. So assert against a fresh connection to the test's own database --
    # bound from DATABASE_URL, because the fixture overrides get_db with a
    # per-test engine and the module-level SessionLocal points somewhere else.
    import os
    import uuid as _uuid

    from app.models.zt_assessment import ZtAssessment
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with sessionmaker(bind=engine, future=True)() as fresh:
        row = fresh.execute(
            select(ZtAssessment).where(ZtAssessment.id == _uuid.UUID(assessment_id))
        ).scalar_one()
        assert row.narratives is not None, (
            "ZT narratives were not persisted; a reload still loses the AI's work "
            "and the consultant must re-run (and re-pay for) the job"
        )
        assert row.narratives["pillar_narratives"]["ID"] == "Identity is partial."
        assert row.narratives["executive_summary"] == "Exec draft."
        assert row.narratives["roadmap_summary"] == "12-month plan"
