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

from tests.unit.test_ai_remediation import csf_ai  # noqa: F401
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


# --- H-6 completion: the two CHUNKED jobs --------------------------------------
#
# csf_score (per tier) and mitre_map (per tactic) are chunked, so a
# single-payload preview would answer the wrong question: what leaves the
# platform is the UNION of every chunk. Showing only the first would understate
# the egress -- a comforting half-truth, which is what this fix exists to kill.


def test_csf_preview_covers_every_chunk_and_never_calls_the_provider(csf_ai) -> None:  # noqa: F811
    from tests.unit.test_ai_remediation import _bootstrap

    c, provider = csf_ai.client, csf_ai.provider
    h, svc_id = _bootstrap(c)

    called: list[str] = []

    def tripwire(_payload: dict) -> LLMResponse:
        called.append("provider")
        return LLMResponse('{"scores": []}')

    provider.register("csf_score", tripwire)

    r = c.post(f"/csf/services/{svc_id}/run-ai?preview=true", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert called == [], "CSF preview called the provider -- client data left the platform"
    assert body["job"] == "csf_score"
    assert body["model"] == "claude-haiku-4-5"
    assert body["chunk_count"] >= 1
    assert len(body["redacted_chunks"]) == body["chunk_count"], (
        "every chunk that would egress must appear in the preview; showing a "
        "subset understates what leaves the platform"
    )
    # Real payload, not a stub: the grounded per-row context is present.
    assert "items" in body["redacted_chunks"][0]
    assert body["payload_bytes"] > 0


# --- H-6 completion: the one-time per-client acknowledgment gate ---------------


def test_live_egress_is_blocked_until_the_client_ack_is_recorded() -> None:
    """The first LIVE call for a tenant must not egress before someone looked.

    Gated inside ``LLMClient.invoke`` -- the single blessed path to a provider --
    so every job is covered, including jobs written after the gate. The check
    runs BEFORE the RUNNING audit row is committed and before the provider call:
    nothing leaves, and nothing is recorded as having tried to.
    """
    import uuid as _uuid

    import pytest as _pytest
    from app.ai.llm import FixtureProvider, LLMClient, RedactionAckRequiredError
    from app.config import Settings

    provider = FixtureProvider()
    provider.register_static("csf_score", LLMResponse('{"scores": []}'))

    called: list[str] = []

    class _TripwireProvider(FixtureProvider):
        def complete(self, *a, **k):  # noqa: ANN002, ANN003
            called.append("provider")
            return LLMResponse('{"scores": []}')

    live_settings = Settings(shield_llm_mode="live", shield_llm_provider="anthropic")
    client = LLMClient(_TripwireProvider(), live_settings)

    class _Tenant:
        redaction_preview_ack_at = None

    class _FakeSession:
        def get(self, model, _id):  # noqa: ANN001
            return _Tenant()

        def add(self, _row):
            raise AssertionError("an audit row was written before the ack gate ran")

        def commit(self):
            pass

        def close(self):
            pass

    import app.db.session as _sess

    original = _sess.open_autonomous_session
    _sess.open_autonomous_session = lambda _bind: _FakeSession()  # type: ignore[assignment]
    try:

        class _Bind:
            def get_bind(self):
                return None

        with _pytest.raises(RedactionAckRequiredError, match="has not been reviewed"):
            client.invoke(
                _Bind(),  # type: ignore[arg-type]
                purpose="csf_score",
                prompt="p",
                payload={},
                requested_by=_uuid.uuid4(),
                client_id=_uuid.uuid4(),
            )
    finally:
        _sess.open_autonomous_session = original  # type: ignore[assignment]

    assert called == [], "the provider was called despite a missing redaction ack"


def test_fixture_mode_is_exempt_from_the_ack_gate(app_client) -> None:  # noqa: F811
    """Nothing leaves the platform in fixture mode, so the gate must not fire.

    If it did, the entire test suite (which runs in fixture mode) would 409.
    """
    c, provider = app_client
    h, svc_id, _ = _admin_service(c, "zero_trust_cisa")
    c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    provider.register_static("zt_score", LLMResponse('{"capabilities": []}'))

    r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "fixture"
