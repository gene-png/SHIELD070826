"""G-3: production must refuse fixture (simulated) AI mode.

Running a production engagement in fixture mode would deliver simulated AI
output to a client as if it were real analysis. `assert_safe_for_runtime`
must refuse to boot in that configuration unless SHIELD_DEMO=1.

The guard matrix, all four cases:
    prod + fixture + no flag        -> refuses to boot
    prod + fixture + SHIELD_DEMO=1  -> boots
    development + fixture           -> boots
    prod + live                     -> boots
"""

from __future__ import annotations

import pytest
from app.config import Settings

# A non-placeholder signing secret so the unrelated prod JWT guard doesn't fire
# and mask what we're actually asserting here.
_REAL_SECRET = "x" * 64


@pytest.mark.unit
def test_prod_fixture_no_flag_refuses_to_boot() -> None:
    s = Settings(
        environment="production",
        shield_llm_mode="fixture",
        jwt_signing_secret=_REAL_SECRET,
    )
    with pytest.raises(RuntimeError, match="SHIELD_DEMO"):
        s.assert_safe_for_runtime()


@pytest.mark.unit
def test_prod_fixture_with_demo_flag_boots() -> None:
    s = Settings(
        environment="production",
        shield_llm_mode="fixture",
        shield_demo="1",
        jwt_signing_secret=_REAL_SECRET,
    )
    s.assert_safe_for_runtime()  # must not raise


@pytest.mark.unit
def test_development_fixture_boots() -> None:
    s = Settings(environment="development", shield_llm_mode="fixture")
    s.assert_safe_for_runtime()  # must not raise


@pytest.mark.unit
def test_prod_live_boots() -> None:
    s = Settings(
        environment="production",
        shield_llm_mode="live",
        jwt_signing_secret=_REAL_SECRET,
    )
    s.assert_safe_for_runtime()  # must not raise


@pytest.mark.unit
def test_refusal_message_names_all_three_variables() -> None:
    s = Settings(
        environment="production",
        shield_llm_mode="fixture",
        jwt_signing_secret=_REAL_SECRET,
    )
    with pytest.raises(RuntimeError) as exc:
        s.assert_safe_for_runtime()
    msg = str(exc.value)
    assert "ENVIRONMENT=production" in msg
    assert "SHIELD_LLM_MODE" in msg
    assert "SHIELD_DEMO=1" in msg
