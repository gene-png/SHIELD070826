"""Env-gated live smoke test: the smallest real Anthropic call per AI job.

WHY THIS EXISTS
---------------
Every existing run-ai test injects a pytest fixture shaped like the *route's*
expected response. None of them exercise the *prompt*. That is precisely how
four independent live-mode breaks shipped against a green suite: fixture-mode
green proves nothing about live mode.

This module is the only place that touches the real provider. It is SKIPPED by
default and costs nothing in CI. It runs only when BOTH are true:

    SHIELD_LIVE_SMOKE=1
    ANTHROPIC_API_KEY=<a real key>

Run it with:

    SHIELD_LIVE_SMOKE=1 SHIELD_LLM_MODE=live ANTHROPIC_API_KEY=sk-... \
        python -m pytest tests/live -v

Note `tests/conftest.py` sets SHIELD_LLM_MODE via os.environ.setdefault, so an
externally-exported `live` wins and no conftest change is needed.

What runs here: the SDK imports, the client constructs, a real call returns,
and -- the part fixtures structurally cannot reach -- each job's ACTUAL prompt
goes to the ACTUAL provider on its ACTUAL pinned model, and the reply is fed to
that job's ACTUAL parser. Static prompt/parser agreement is the job of the
contract tests (FIX A-6), which run offline on every commit. This file answers
the question fixtures cannot: "does the live path physically work?"

It earns its keep. It found the unlabeled-payload bug: `complete` sent the
payload as a bare JSON blob with no label, and `claude-haiku-4-5` -- the pinned
model for both `csf_score` and `mitre_map` -- answered in prose ("I don't see
the assessment data in your message") instead of JSON. See `_frame_payload` in
app/ai/llm.py.
"""

from __future__ import annotations

import os

import pytest

_LIVE_ENABLED = os.environ.get("SHIELD_LIVE_SMOKE") == "1"
_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

pytestmark = pytest.mark.skipif(
    not (_LIVE_ENABLED and _HAS_KEY),
    reason=(
        "live smoke disabled: set SHIELD_LIVE_SMOKE=1 and ANTHROPIC_API_KEY to run. "
        "This is expected to skip in CI and on any machine without a key."
    ),
)


def test_sdk_is_importable() -> None:
    """FIX A-1's boot-time guarantee, asserted directly.

    A live-mode container that cannot import `anthropic` currently fails at the
    first Run AI click rather than at boot, because the import is lazy inside
    AnthropicProvider._ensure_client.
    """
    import anthropic  # noqa: F401

    assert hasattr(anthropic, "Anthropic")


def test_provider_constructs_with_real_key() -> None:
    from app.ai.llm import AnthropicProvider

    provider = AnthropicProvider(
        model=os.environ.get("SHIELD_LLM_MODEL", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    assert provider.name == "anthropic"
    # Force the lazy import + client construction without spending a token.
    assert provider._ensure_client() is not None


def test_minimal_live_call_returns_parseable_json() -> None:
    """One real call, parsed the way production parses.

    Two things were wrong with the original version of this test and both
    mattered.

    First, it passed ``{}`` as the payload. ``AnthropicProvider.complete``
    sends prompt and payload as two separate text blocks, so the model saw a
    bare ``{}`` after its instruction and dutifully echoed it: the reply was
    ``{"ok": true}{}``. A live call had physically succeeded, and the test
    still went red. Give the model a payload with something in it.

    Second, it hand-rolled its own fence-stripping and called ``json.loads``
    directly. Production does not do that -- it calls ``parse_json``. A test
    that parses more leniently than the product cannot detect a response the
    product would choke on, and one that parses differently just invents its
    own failures. Parse with the code that ships.
    """
    from app.ai.engine import parse_json
    from app.ai.llm import AnthropicProvider

    provider = AnthropicProvider(
        model=os.environ.get("SHIELD_LLM_MODEL", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    response = provider.complete(
        'Return strictly JSON of the form {"ok": true} and nothing else. '
        "The payload below is context you should ignore.",
        {"context": "none"},
    )
    assert response.content, "provider returned empty content"
    assert parse_json(response.content) == {"ok": True}
    # Token accounting must survive, or FIX H-5's per-tenant cost report is built
    # on sand.
    assert response.input_tokens is not None
    assert response.output_tokens is not None


@pytest.mark.parametrize(
    "job_name",
    ["tech_debt_extract", "csf_score", "zt_score", "mitre_map", "risk_synthesize"],
)
def test_every_registered_job_has_a_prompt_and_parser(job_name: str) -> None:
    """Offline guard that runs in the live lane too: the registry is intact.

    If a job is renamed or dropped, the live lane should fail loudly rather
    than silently smoke-testing four of five jobs.
    """
    import app.ai.jobs  # noqa: F401  - import registers the jobs
    from app.ai.engine import get_job, registered_jobs

    assert job_name in registered_jobs()
    job = get_job(job_name)
    assert job.prompt.strip(), f"{job_name} has an empty prompt"
    assert callable(job.parser)


# --- Each job's REAL prompt, against the REAL provider, through its REAL parser -


# The smallest payload that is still *grounded* -- shaped like what the routes
# actually build (FIX A-2), not a stub. A stub payload would let a prompt that
# ignores its input still pass.
_LIVE_PAYLOADS: dict[str, dict] = {
    "tech_debt_extract": {
        "rows": [
            {"Tool": "CrowdStrike Falcon", "Vendor": "CrowdStrike", "Annual Cost": "120000"},
            {"Tool": "", "Vendor": "", "Annual Cost": ""},
        ],
        "context": {"sheet_name": "Inventory"},
    },
    "csf_score": {
        "items": [
            {
                "tier": "high",
                "subcategory_code": "GV.OC-01",
                "subcategory_text": "The organizational mission is understood.",
                "answers": ["Mission is documented in the charter and reviewed annually."],
                "evidence": ["charter.pdf"],
            }
        ]
    },
    "zt_score": {
        "framework": "cisa_ztmm_2",
        "capabilities": [
            {
                "code": "ID-1.1",
                "pillar_code": "identity",
                "question": "How are identities authenticated?",
                "answer": "MFA is enforced for all staff via Okta; no legacy auth remains.",
            }
        ],
    },
    "mitre_map": {
        "capabilities": [{"name": "CrowdStrike Falcon", "category": "EDR"}],
        "techniques": [
            {
                "technique_code": "T1003",
                "name": "OS Credential Dumping",
                "tactic": "credential-access",
            },
            {
                "technique_code": "T1195",
                "name": "Supply Chain Compromise",
                "tactic": "initial-access",
            },
        ],
    },
    "risk_synthesize": {
        "findings": [
            {
                "source": "coverage_finding",
                "source_id": "T1195",
                "technique_code": "T1195",
                "status": "gap",
                "description": "No supply-chain compromise detection or prevention coverage.",
            }
        ],
        "capabilities": [{"name": "CrowdStrike Falcon", "category": "EDR"}],
    },
}

# The top-level key each job's parser must yield. Taken from the SHAPE constants
# in app.ai.schemas, which are the same strings the prompts interpolate.
_TOP_KEY = {
    "tech_debt_extract": None,  # its parser returns list[ExtractedCapability]
    "csf_score": "scores",
    "zt_score": "capabilities",
    "mitre_map": "techniques",
    "risk_synthesize": "entries",
}


@pytest.mark.parametrize("job_name", sorted(_LIVE_PAYLOADS))
def test_live_response_satisfies_the_jobs_own_parser(job_name: str) -> None:
    """The assertion fixtures structurally cannot make.

    Every run-ai test injects a fixture shaped like the route's expectation, so
    fixture mode only ever proves the route can parse a string the test wrote.
    This sends each job's ACTUAL prompt to the ACTUAL provider on the ACTUAL
    pinned model, then feeds the reply to that job's ACTUAL parser.

    A prompt/parser disagreement -- the exact defect class behind FIX A-2 and
    FIX A-6 -- fails here and nowhere else.
    """
    import app.ai.jobs  # noqa: F401  - import registers the jobs
    from app.ai.engine import get_job
    from app.ai.llm import AnthropicProvider

    job = get_job(job_name)
    provider = AnthropicProvider(
        model=os.environ.get("SHIELD_LLM_MODEL", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    response = provider.complete(
        job.prompt,
        _LIVE_PAYLOADS[job_name],
        model=job.model,
        max_tokens=job.max_tokens,
    )
    assert response.content, f"{job_name}: provider returned empty content"

    # The parser is the product's own. If it raises, live mode is broken for
    # this job no matter how green the fixture suite is.
    parsed = job.parser(response.content)

    top = _TOP_KEY[job_name]
    if top is None:
        # tech_debt_extract's parser returns a list[ExtractedCapability]. It must
        # find the real tool row and skip the blank one.
        assert parsed, "tech_debt_extract parsed zero capabilities"
        assert any(
            "crowdstrike" in c.name.lower() for c in parsed
        ), f"the real inventory row was not extracted; got {[c.name for c in parsed]}"
        return

    assert isinstance(parsed, dict), f"{job_name}: parser returned {type(parsed)!r}"
    assert top in parsed, (
        f"{job_name}: live response has no {top!r} key -- the prompt and the "
        f"route disagree. Got keys: {sorted(parsed)}"
    )
    assert parsed[top], f"{job_name}: {top!r} came back empty; the model matched zero rows"


def test_live_risk_synthesize_uses_the_closed_vocabularies() -> None:
    """FIX A-4, asserted against a real model rather than a fixture.

    The original prompt asked for display labels ("Very Low".."Very High") while
    the enums are lowercase snake_case, so every live likelihood, impact and
    derived tier was silently nulled -- a 200 OK with the risk scoring gone.
    Only a live call can prove the model now returns tokens the enums accept.
    """
    import app.ai.jobs  # noqa: F401  - import registers the jobs
    from app.ai.engine import get_job
    from app.ai.llm import AnthropicProvider
    from app.risk.engine import Impact, Likelihood, RecommendedAction

    job = get_job("risk_synthesize")
    provider = AnthropicProvider(
        model=os.environ.get("SHIELD_LLM_MODEL", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    parsed = job.parser(
        provider.complete(
            job.prompt,
            _LIVE_PAYLOADS["risk_synthesize"],
            model=job.model,
            max_tokens=job.max_tokens,
        ).content
    )

    entries = parsed["entries"]
    assert entries, "risk_synthesize returned zero entries for a real gap finding"
    for entry in entries:
        # Constructing the enum is the same coercion the route performs. A
        # display label like "Very Low" raises ValueError here, exactly as it
        # would silently null the column in production.
        Likelihood(entry["likelihood"])
        Impact(entry["impact"])
        RecommendedAction(entry["recommended_action"])
        assert entry["axis"] in {"detection", "prevention", "response"}
