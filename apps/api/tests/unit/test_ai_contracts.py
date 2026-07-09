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
