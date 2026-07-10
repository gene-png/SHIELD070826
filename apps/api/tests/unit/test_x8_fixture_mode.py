"""FIX X-8: fixture mode must produce a WORKING app offline, without fabricating.

``SHIELD_LLM_MODE=fixture`` is the ``docker compose up`` default. Before this
fix, ``_build_provider`` returned a ``FixtureProvider`` with an EMPTY registry,
so every Run-AI / extract raised ``KeyError: No fixture registered`` and 500'd.
A fresh stack had no working AI at all — verified directly against the running
container.

The fix registers a grounded builder per purpose (``app/ai/fixtures.py``). These
tests hold the two properties that make the fix acceptable rather than dangerous:

1. **It works.** The real route path (``_llm_dep`` -> ``from_settings`` ->
   ``_build_provider`` -> ``register_default_fixtures``) returns 200 and changes
   rows. Proven non-vacuous: the same route with a bare, unregistered provider
   still 500s, so the defaults are demonstrably what make it work.
2. **It does not fabricate.** Every entity in a response is echoed from the
   payload; nothing names a tool/technique/control/vendor it was not handed.
   This is the C-1 line the whole platform exists to hold, so it is asserted
   mechanically, not trusted.
"""

from __future__ import annotations

import json

import pytest
from app.ai.engine import get_job
from app.ai.fixtures import (
    _build_csf_score,
    _build_mitre_map,
    _build_risk_synthesize,
    _build_tech_debt_extract,
    _build_zt_score,
    register_default_fixtures,
)
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse

# Reuse the battle-tested CSF route harness for the end-to-end proof.
from tests.unit.test_csf_run_ai import _bootstrap, app_client  # noqa: F401

pytestmark = pytest.mark.unit


_PURPOSES = ("csf_score", "zt_score", "mitre_map", "risk_synthesize", "extract.capabilities")


# Representative payloads, shaped exactly as the routes build them (verified
# against app/routes/{csf,zt,attack,risk}.py and app/tech_debt/extract.py).
def _csf_payload() -> dict:
    return {
        "tier": "high",
        "items": [
            {"tier": "high", "subcategory_code": "GV.OC-01", "subcategory_name": "Mission"},
            {"tier": "high", "subcategory_code": "GV.OC-02", "subcategory_name": "Stakeholders"},
        ],
    }


def _zt_payload() -> dict:
    return {
        "framework": "cisa_ztmm_2",
        "capabilities": ["ID-1.2", "ID-1.1"],  # deliberately unsorted
        "answers": {"ID-1.1": {"notes": "n", "current": 1}},
    }


def _mitre_payload() -> dict:
    return {"capability_list": ["CrowdStrike Falcon"], "technique_codes": ["T1003", "T1195"]}


def _risk_payload() -> dict:
    return {
        "findings": [
            {
                "source": "coverage_finding",
                "source_id": "T1195",
                "kind": "attack",
                "label": "ATT&CK T1195: gap",
            },
            {
                "source": "questionnaire_response",
                "source_id": "GV.OC-01",
                "kind": "csf",
                "label": "CSF GV.OC-01: tier 1",
            },
        ],
        "valid_techniques": ["T1195"],
        "valid_controls": ["GV.OC-01"],
    }


def _extract_payload() -> dict:
    return {
        "rows": [
            {"Tool": "CrowdStrike Falcon", "Vendor": "CrowdStrike", "Annual Cost": "120000"},
            {"Tool": "", "Vendor": "", "Annual Cost": ""},  # blank -> skipped
            {"Tool": "Splunk Enterprise", "Vendor": "Splunk", "Seats": "500"},
        ],
        "context": {"sheet_name": "Inventory"},
    }


# --- Grounding + shape-conformance, per purpose ----------------------------


def test_csf_builder_covers_exactly_the_payload_codes_with_valid_scores() -> None:
    from app.routes.csf import _DIM_FIELDS

    payload = _csf_payload()
    data = json.loads(_build_csf_score(payload).content)

    got = {(s["tier"], s["subcategory_code"]) for s in data["scores"]}
    want = {("high", "GV.OC-01"), ("high", "GV.OC-02")}
    assert got == want, "csf fixture did not echo exactly the payload's (tier, code) pairs"
    for s in data["scores"]:
        for dim in _DIM_FIELDS:
            assert s[dim] in (0, 1, 2), f"{dim} out of the 0-2 range the route clamps to"
        assert "SIMULATED" in s["what_we_found"], "narrative must be visibly synthetic"


def test_zt_builder_echoes_every_capability_code() -> None:
    data = json.loads(_build_zt_score(_zt_payload()).content)
    assert {c["code"] for c in data["capabilities"]} == {"ID-1.1", "ID-1.2"}
    for c in data["capabilities"]:
        # 1 and 2 are valid on both CISA (1-4) and DoD (1-3).
        assert isinstance(c["current"], int) and isinstance(c["target"], int)


def test_mitre_builder_marks_every_technique_gap_and_invents_no_coverage() -> None:
    payload = _mitre_payload()
    data = json.loads(_build_mitre_map(payload).content)
    assert {t["technique_code"] for t in data["techniques"]} == {"T1003", "T1195"}
    for t in data["techniques"]:
        assert t["status"] == "gap", "offline mode must not claim coverage it cannot ground"
        # Compact gap entries carry no tool arrays — nothing to fabricate.
        assert "detection_tools" not in t and "prevention_tools" not in t


def test_risk_builder_grounds_links_and_uses_real_enum_tokens() -> None:
    from app.risk.engine import Impact, Likelihood, RecommendedAction

    payload = _risk_payload()
    data = json.loads(_build_risk_synthesize(payload).content)
    assert len(data["entries"]) == 2, "one entry per finding"

    valid_techs = set(payload["valid_techniques"])
    for e in data["entries"]:
        # Enums must construct — a display label like "Very Low" would raise here,
        # exactly as it nulled the column in production before FIX A-4.
        Likelihood(e["likelihood"])
        Impact(e["impact"])
        RecommendedAction(e["recommended_action"])
        assert e["axis"] in {"detection", "prevention", "response"}
        # No invented techniques: every linked technique was in valid_techniques.
        assert set(e["linked_techniques"]) <= valid_techs

    coverage = next(e for e in data["entries"] if e["source"] == "coverage_finding")
    assert coverage["linked_techniques"] == ["T1195"], "the finding's own technique must link"


def test_extract_builder_skips_blanks_and_grounds_every_field_in_its_row() -> None:
    payload = _extract_payload()
    items = get_job("tech_debt_extract").parser(_build_tech_debt_extract(payload).content)

    assert len(items) == 2, "the one blank row must be skipped"
    names = {i.name for i in items}
    assert names == {"CrowdStrike Falcon", "Splunk Enterprise"}

    rows = payload["rows"]
    for i in items:
        # source_row_index is a real index, and the name it reports actually
        # appears in THAT row — no tool is conjured from nowhere.
        assert 0 <= i.source_row_index < len(rows)
        row_text = " ".join(str(v) for v in rows[i.source_row_index].values())
        assert i.name in row_text, f"{i.name!r} is not present in its source row"


@pytest.mark.parametrize(
    "builder,payload",
    [
        (_build_csf_score, _csf_payload()),
        (_build_zt_score, _zt_payload()),
        (_build_mitre_map, _mitre_payload()),
        (_build_risk_synthesize, _risk_payload()),
        (_build_tech_debt_extract, _extract_payload()),
    ],
)
def test_builders_are_deterministic(builder, payload) -> None:
    """Same payload in -> byte-identical JSON out. No randomness, no clocks."""
    assert builder(payload).content == builder(payload).content


# --- Registration + provider-level round trip through the real parsers ------


def test_from_settings_registers_all_five_purposes_and_each_parses() -> None:
    """The running-app path builds a provider that answers every purpose.

    ``LLMClient.from_settings()`` in fixture mode runs the exact
    ``_build_provider`` -> ``register_default_fixtures`` wiring the app uses.
    Feed each purpose its real payload and parse with that job's OWN parser.
    """
    client = LLMClient.from_settings()
    assert client.provider.name == "fixture"

    payloads = {
        "csf_score": _csf_payload(),
        "zt_score": _zt_payload(),
        "mitre_map": _mitre_payload(),
        "risk_synthesize": _risk_payload(),
        "extract.capabilities": _extract_payload(),
    }
    # Map the fixture purpose back to the job whose parser validates it.
    parser_for = {
        "csf_score": get_job("csf_score").parser,
        "zt_score": get_job("zt_score").parser,
        "mitre_map": get_job("mitre_map").parser,
        "risk_synthesize": get_job("risk_synthesize").parser,
        "extract.capabilities": get_job("tech_debt_extract").parser,
    }
    for purpose, payload in payloads.items():
        resp = client.provider.complete("prompt", {**payload, "__purpose__": purpose})
        parsed = parser_for[purpose](resp.content)  # must not raise
        assert parsed, f"{purpose}: default fixture parsed to an empty result"


def test_bare_provider_still_raises_so_the_defaults_are_what_fix_it() -> None:
    """Non-vacuity of the registration: without register_default_fixtures, every
    purpose still raises. This is the X-8 defect, and it proves the defaults —
    not some incidental registration — are what make fixture mode work."""
    bare = FixtureProvider()
    for purpose in _PURPOSES:
        with pytest.raises(KeyError, match="No fixture registered"):
            bare.complete("p", {"__purpose__": purpose})

    # And once registered, none of them raise.
    register_default_fixtures(bare)
    for purpose in _PURPOSES:
        assert isinstance(bare.complete("p", {"__purpose__": purpose}), LLMResponse)


# --- The real X-8 regression: an actual route, end to end -------------------


def test_csf_run_ai_works_with_default_fixtures(app_client) -> None:  # noqa: F811
    """CSF Run-AI returns 200 and changes rows using ONLY the default fixtures.

    The harness normally overrides ``_llm_dep`` with its own provider; we remove
    that override so the route takes the real ``from_settings`` path — the same
    one a `docker compose up` stack takes.
    """
    from app.routes.csf import _llm_dep

    c, _provider = app_client
    c.app.dependency_overrides.pop(_llm_dep, None)

    h, svc_id = _bootstrap(c)
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "fixture"
    assert body["changed"], "the default CSF fixture changed zero rows"


def test_csf_run_ai_500s_without_the_defaults(app_client) -> None:  # noqa: F811
    """The same route, same seeding, but a bare provider -> the KeyError 500.

    This is X-8 as it shipped. It makes the test above non-vacuous: swap the
    grounded defaults for an empty registry and the route breaks exactly the way
    the running container did. The harness TestClient re-raises server errors, so
    a second client with ``raise_server_exceptions=False`` is used to observe the
    500 the operator would actually receive.
    """
    from app.routes.csf import _llm_dep
    from starlette.testclient import TestClient

    c, _provider = app_client
    c.app.dependency_overrides[_llm_dep] = lambda: LLMClient(FixtureProvider())

    h, svc_id = _bootstrap(c)
    soft = TestClient(c.app, raise_server_exceptions=False, headers=dict(c.headers))
    r = soft.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code >= 500, "an unregistered purpose must surface as a server error"
