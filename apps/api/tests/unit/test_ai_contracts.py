"""FIX A-6: prompt/parser contract tests.

WHY THIS FILE EXISTS
--------------------
Four independent live-mode breaks shipped against a fully green test suite. The
mechanism was always the same: the prompt told the model one response shape, the
route parsed a different one, and nothing in the repository compared the two.
Every run-ai test injected a fixture written to match the *route*, so fixture
mode proved only that the route could parse the route's own shape.

Concretely, before Sprint 1:

  * ``csf_score``'s prompt demanded ``{"subcategories": [{"code": ...}]}`` while
    the route read ``data["scores"]`` keyed by ``tier|subcategory_code``. A
    perfectly compliant live response matched zero rows, returned 200 OK, and
    reported "no changes" -- so the consultant believed the assessment was
    already correct.
  * ``risk_synthesize``'s prompt asked for display labels ("Very Low".."Very
    High") while the enums are lowercase snake_case, so every live likelihood,
    impact and derived tier was silently nulled.

These tests close that class. They assert, mechanically, that every key the
ROUTE consumes is actually declared in the SHAPE constant the PROMPT
interpolates -- and then that a shape-conformant response, fed through the real
route, actually changes a row.

If you change a prompt shape without changing the route (or vice versa), a test
here goes red. That is the entire point: the two can no longer drift silently.

Deliberately imported from source, never restated:
  * shapes from ``app.ai.schemas`` (what the prompt promises the model)
  * field tuples from the routes (what the route actually reads)
  * enum members from ``app.risk.engine`` (the closed vocabularies)
"""

from __future__ import annotations

import json

import pytest
from app.ai.engine import get_job, registered_jobs
from app.ai.schemas import (
    CSF_SCORE_SHAPE,
    MITRE_MAP_SHAPE,
    RISK_SYNTHESIZE_SHAPE,
    TECH_DEBT_EXTRACT_SHAPE,
    ZT_SCORE_SHAPE,
)

# Reusing the harness rather than building a second one; importing the fixture
# into this module's namespace is how pytest shares it across files.
from tests.unit.test_ai_remediation import _bootstrap, csf_ai  # noqa: F401

pytestmark = pytest.mark.unit


_SHAPES = {
    "csf_score": CSF_SCORE_SHAPE,
    "zt_score": ZT_SCORE_SHAPE,
    "mitre_map": MITRE_MAP_SHAPE,
    "risk_synthesize": RISK_SYNTHESIZE_SHAPE,
    "tech_debt_extract": TECH_DEBT_EXTRACT_SHAPE,
}


def test_every_registered_job_has_a_declared_shape() -> None:
    """A new AI job must not ship without a shape constant, or it can drift on day one."""
    assert set(registered_jobs()) == set(_SHAPES), (
        "a job was added or renamed without a matching shape in app/ai/schemas.py; "
        f"registered={registered_jobs()} shapes={sorted(_SHAPES)}"
    )


def test_every_prompt_interpolates_its_shape() -> None:
    """The prompt text must actually contain the shape, not a hand-copied variant."""
    for name, shape in _SHAPES.items():
        if name == "tech_debt_extract":
            # Its prompt lives in app/tech_debt/extract.py; asserted separately below.
            continue
        prompt = get_job(name).prompt
        assert shape in prompt, f"{name}'s prompt does not interpolate its shape constant"


# --- csf_score -------------------------------------------------------------


def test_csf_shape_declares_every_key_the_route_reads() -> None:
    """The A-2 regression, asserted mechanically.

    The route keys rows by ``tier|subcategory_code`` and reads the five dimension
    fields plus ``what_we_found``. If the prompt stops promising any of those,
    a live response silently matches zero rows.
    """
    from app.routes.csf import _RUN_FIELDS

    assert '"scores"' in CSF_SCORE_SHAPE, "route reads data['scores']"
    assert '"tier"' in CSF_SCORE_SHAPE, "route keys rows by tier"
    assert '"subcategory_code"' in CSF_SCORE_SHAPE, "route keys rows by subcategory_code"
    for field in _RUN_FIELDS:
        assert field in CSF_SCORE_SHAPE, f"route reads {field!r} but the prompt never asks for it"

    # And the shape that caused the original bug must be gone for good.
    assert '"subcategories"' not in CSF_SCORE_SHAPE
    assert '"code":' not in CSF_SCORE_SHAPE


def test_csf_route_applies_a_shape_conformant_response(csf_ai) -> None:  # noqa: F811
    """Generate a response conforming to the documented shape; assert a row changed.

    This is the test that would have caught A-2. Under the old prompt the model
    would have returned ``{"subcategories": [...]}`` and this assertion would
    fail with zero changes -- exactly the silent no-op users hit in live mode.
    """
    from app.routes.csf import _DIM_FIELDS

    c, provider = csf_ai.client, csf_ai.provider
    h, svc_id = _bootstrap(c)

    # Discover the real (tier, subcategory_code) pairs the route seeded.
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    assert rows, "no seeded rows to score"
    target = rows[0]

    def conformant(_payload: dict):
        from app.ai.llm import LLMResponse

        return LLMResponse(
            json.dumps(
                {
                    "scores": [
                        {
                            "tier": "high",
                            "subcategory_code": target["subcategory_code"],
                            **dict.fromkeys(_DIM_FIELDS, 2),
                            "what_we_found": "Documented and consistently exercised.",
                        }
                    ],
                    "executive_summary": "ok",
                }
            )
        )

    provider.register("csf_score", conformant)
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text

    changed = r.json()["changed"]
    assert changed, (
        "a shape-conformant response changed ZERO rows -- the prompt and the "
        "route parser have drifted apart (this is the A-2 defect)"
    )

    after = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    hit = next(x for x in after if x["subcategory_code"] == target["subcategory_code"])
    assert all(hit[d] == 2 for d in _DIM_FIELDS)
    assert hit["what_we_found"] == "Documented and consistently exercised."


# --- risk_synthesize -------------------------------------------------------


def test_risk_shape_uses_the_real_enum_tokens_not_display_labels() -> None:
    """The A-4 regression, asserted mechanically.

    The prompt used to ask for "Very Low".."Very High" and "Negligible"..
    "Catastrophic" while the enums are lowercase snake_case, so the coercion
    helper returned None and every likelihood, impact and derived tier was
    silently nulled. Note the middle likelihood token is ``medium``, not
    ``moderate`` -- read from the enum, never from the remediation document.
    """
    from app.risk.engine import Impact, Likelihood

    for member in list(Likelihood) + list(Impact):
        assert (
            member.value in RISK_SYNTHESIZE_SHAPE
        ), f"{member.value!r} is a valid enum token but the prompt never offers it"

    for bad in ("Very Low", "Very High", "Negligible", "Catastrophic"):
        assert bad not in RISK_SYNTHESIZE_SHAPE, (
            f"display label {bad!r} is back in the prompt; the enums are snake_case "
            "and the coercion helper will null the value"
        )


# --- mitre_map / zt_score --------------------------------------------------


def test_mitre_shape_declares_the_keys_the_attack_route_reads() -> None:
    assert '"techniques"' in MITRE_MAP_SHAPE
    assert '"technique_code"' in MITRE_MAP_SHAPE
    assert '"status"' in MITRE_MAP_SHAPE


def test_zt_shape_declares_the_keys_the_zt_route_reads() -> None:
    assert '"capabilities"' in ZT_SCORE_SHAPE
    assert '"code"' in ZT_SCORE_SHAPE
    assert '"current"' in ZT_SCORE_SHAPE
    assert '"target"' in ZT_SCORE_SHAPE


def test_tech_debt_shape_declares_the_keys_the_extractor_reads() -> None:
    assert '"items"' in TECH_DEBT_EXTRACT_SHAPE
    for field in ("name", "vendor", "annual_cost_usd", "license_count", "confidence_pct"):
        assert field in TECH_DEBT_EXTRACT_SHAPE


# --- per-model output ceilings (A-3 / X-1) ---------------------------------


def test_every_pinned_job_fits_its_model_output_ceiling() -> None:
    """A per-job model pin must never request more output than the model allows.

    Haiku 4.5 caps at 64K output while the full ATT&CK map is ~65K tokens even
    when terse. Pinning `mitre_map` to Haiku without chunking (and without a
    cap under 64K) would be rejected by the API -- or, on a provider that
    clamps, would truncate the response mid-JSON. That is precisely the defect
    FIX A-3 exists to close, and it is one careless `model=` away from
    returning. This test is the guard.
    """
    from app.ai.llm import max_output_tokens

    for name in registered_jobs():
        job = get_job(name)
        if job.model is None:
            continue  # inherits the env default; checked at call time
        ceiling = max_output_tokens(job.model)
        requested = job.max_tokens if job.max_tokens is not None else ceiling
        assert requested <= ceiling, (
            f"job {name!r} is pinned to {job.model!r} (max output {ceiling}) but "
            f"requests max_tokens={requested}; it would truncate or be rejected"
        )


def test_unknown_model_gets_the_conservative_ceiling() -> None:
    """An unrecognised model id must fail safe (assume the small ceiling)."""
    from app.ai.llm import max_output_tokens

    assert max_output_tokens("claude-haiku-4-5") == 64_000
    assert max_output_tokens("claude-sonnet-5") == 128_000
    assert max_output_tokens("some-model-released-after-this-code-was-written") == 64_000


def test_provider_refuses_max_tokens_above_the_model_ceiling() -> None:
    """Fail loudly, never clamp: a clamp truncates the response mid-JSON."""
    import pytest as _pytest
    from app.ai.llm import AnthropicProvider, LLMConfigurationError

    provider = AnthropicProvider(model="claude-haiku-4-5", api_key="sk-fake")
    with _pytest.raises(LLMConfigurationError, match="exceeds the maximum output"):
        provider.complete("p", {}, max_tokens=128_000)


def test_outgoing_payload_block_is_labeled() -> None:
    """The payload must reach the model as *labeled* data, not a bare blob.

    Found by the live smoke test. ``complete`` sends the prompt and the payload
    as two text blocks. When the payload block was an unlabeled
    ``json.dumps(payload)``, ``claude-haiku-4-5`` failed to connect it to a
    prompt that says "from the supplied interview answers" and answered in
    prose -- "I don't see the assessment data in your message" -- which
    ``parse_json`` cannot parse. Both Haiku-pinned jobs (``csf_score``,
    ``mitre_map``) took that path in production.

    No fixture test can catch this: fixture mode never builds a request. This
    guard runs offline by intercepting the SDK client, so the framing cannot be
    dropped again without a red test.
    """
    from app.ai.llm import PAYLOAD_PREAMBLE, AnthropicProvider

    captured: dict = {}

    class _FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get_final_message(self):
            class _Block:
                type = "text"
                text = '{"ok": true}'

            class _Msg:
                content = [_Block()]
                usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

            return _Msg()

    class _FakeMessages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _FakeStream()

    class _FakeClient:
        messages = _FakeMessages()

    provider = AnthropicProvider(model="claude-haiku-4-5", api_key="sk-fake")
    provider._client = _FakeClient()
    provider.complete("the prompt", {"items": [{"subcategory_code": "GV.OC-01"}]})

    blocks = captured["messages"][0]["content"]
    assert blocks[0]["text"] == "the prompt"

    payload_block = blocks[1]["text"]
    assert payload_block.startswith(PAYLOAD_PREAMBLE), (
        "the payload block lost its label; Haiku will answer in prose and "
        f"parse_json will raise. Got: {payload_block[:60]!r}"
    )
    # The real payload still egresses intact, immediately after the label.
    assert json.loads(payload_block[len(PAYLOAD_PREAMBLE) :].strip()) == {
        "items": [{"subcategory_code": "GV.OC-01"}]
    }
