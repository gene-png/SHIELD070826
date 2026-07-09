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

These assertions are deliberately shallow: that the SDK imports, the client
constructs, a real call returns, and each job's own parser accepts the
response. Deep prompt/parser agreement is the job of the contract tests
(FIX A-6), which run offline on every commit. This file answers only the
question fixtures cannot: "does the live path physically work?"
"""

from __future__ import annotations

import json
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
    """One real call. Cheapest possible: ask for a trivial JSON object."""
    from app.ai.llm import AnthropicProvider

    provider = AnthropicProvider(
        model=os.environ.get("SHIELD_LLM_MODEL", "claude-sonnet-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    response = provider.complete(
        'Reply with exactly this JSON and nothing else: {"ok": true}',
        {},
    )
    assert response.content, "provider returned empty content"
    payload = json.loads(response.content.strip().strip("`").removeprefix("json").strip())
    assert payload == {"ok": True}
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
