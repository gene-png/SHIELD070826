"""FIX H-6: see what leaves the platform, before it leaves.

The Master Spec (§12) promises the consultant can inspect what egresses.
Today redaction happens invisibly inside ``LLMClient.invoke``, so the first
moment an admin could look at the outgoing payload was never. With live mode
arriving, operators need to confirm redaction quality on real client data
*before* it is sent -- both because the spec says so and because it is the
first question a FedRAMP assessor asks.

``POST /zt/services/{id}/run-ai?preview=true`` builds the exact payload, runs
the same redactor with the same settings, and returns it with a per-rule count
of what was removed. It must make NO provider call and write NO llm_calls row.

The assertion that matters is the negative one: nothing egressed.
"""

from __future__ import annotations

import pytest
from app.ai.llm import LLMResponse

from tests.unit.test_zt_run_ai import _admin_service, app_client  # noqa: F401

pytestmark = pytest.mark.unit


def test_preview_redacts_and_never_calls_the_provider(app_client) -> None:  # noqa: F811
    c, provider = app_client
    h, svc_id, _ = _admin_service(c, "zero_trust_cisa")
    a = c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    code = a.json()["answers"][0]["capability_code"]

    # Put something that MUST be redacted into the payload the model would see.
    answer_id = a.json()["answers"][0]["id"]
    c.patch(
        f"/zt/answers/{answer_id}",
        headers=h,
        json={"notes": "Ask jane.doe@acme.example about the SSO rollout."},
    )

    called: list[str] = []

    def tripwire(_payload: dict) -> LLMResponse:
        called.append("provider")
        return LLMResponse('{"capabilities": []}')

    provider.register("zt_score", tripwire)

    r = c.post(f"/zt/services/{svc_id}/run-ai?preview=true", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    # Nothing egressed. This is the whole point of the fix.
    assert called == [], "preview called the provider -- client data left the platform"

    assert body["job"] == "zt_score"
    # The per-job model pin is visible, so an operator can see which model would
    # receive the data (csf_score / mitre_map run on Haiku, the rest on Sonnet).
    assert body["model"]
    assert "prompt" in body

    payload = body["redacted_payload"]
    flat = str(payload)
    assert "jane.doe@acme.example" not in flat, "the email was NOT redacted before preview"
    assert "[EMAIL]" in flat, "expected the redactor's email placeholder"
    assert body["redaction_counts"].get("email", 0) >= 1
    assert body["redacted_total"] >= 1
    assert body["payload_bytes"] > 0

    # The capability under test is still present -- preview shows the real
    # payload, not a stub.
    assert code in flat


def test_preview_writes_no_llm_calls_row(app_client) -> None:  # noqa: F811
    import os

    from app.models.llm_call import LLMCall
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import sessionmaker

    c, provider = app_client
    h, svc_id, _ = _admin_service(c, "zero_trust_cisa")
    c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    provider.register_static("zt_score", LLMResponse('{"capabilities": []}'))

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as s:
        before = s.execute(select(func.count()).select_from(LLMCall)).scalar_one()

    assert c.post(f"/zt/services/{svc_id}/run-ai?preview=true", headers=h).status_code == 200

    with Session() as s:
        after = s.execute(select(func.count()).select_from(LLMCall)).scalar_one()
    assert after == before, "preview wrote an llm_calls row; it must not touch the audit trail"

    # And a real run still does write one, so the assertion above is not vacuous.
    assert c.post(f"/zt/services/{svc_id}/run-ai", headers=h).status_code == 200
    with Session() as s:
        real = s.execute(select(func.count()).select_from(LLMCall)).scalar_one()
    assert real == before + 1, "a non-preview run must still write exactly one audit row"
