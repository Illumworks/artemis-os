"""F2 — Runtime Injection tests.

Tests for _build_system_prompt and _cached_josh_spec.
All tests are pure in-memory — no DB session needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from artemis.builders.executor import _build_system_prompt, _cached_josh_spec
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout


def _agent(**kwargs: Any) -> Any:
    """Build a minimal mock Agent with defaults for all fields."""
    defaults: dict[str, Any] = {
        "agent_id": "marketing.scout.regional_news",
        "system_prompt": None,
        "goal": None,
        "persona": None,
        "urgency_tiers": None,
        "failure_modes": None,
        "implementation_notes": None,
        "inputs_required": None,
    }
    defaults.update(kwargs)
    agent = MagicMock()
    for key, val in defaults.items():
        setattr(agent, key, val)
    return agent


# 1. Voice + persona injection
def test_persona_voice_and_purpose_injected() -> None:
    agent = _agent(
        agent_id="marketing.scout.regional_news",
        persona={"voice_notes": "Curious, conversational", "purpose": "Catch local signals"},
    )
    result = _build_system_prompt(agent, None)
    assert result is not None
    assert "Curious, conversational" in result
    assert "Catch local signals" in result


# 2. Josh-spec reason codes for scout
def test_scout_reason_codes_injected() -> None:
    agent = _agent(agent_id="marketing.scout.regional_news")
    result = _build_system_prompt(agent, None)
    assert result is not None
    assert "You may emit ONLY these reason codes" in result
    spec = parse_spec()
    codes = reason_codes_for_scout(spec, "regional_news")
    assert len(codes) >= 5
    injected_count = sum(1 for rc in codes if rc.code in result)
    assert injected_count >= 5


# 3. Non-scout omits reason codes section
def test_non_scout_omits_reason_codes() -> None:
    agent = _agent(agent_id="marketing.qualifier.cross_reference")
    result = _build_system_prompt(agent, None)
    assert result is None or "You may emit ONLY these reason codes" not in (result or "")


# 4. State nuances for scout
def test_scout_state_nuances_injected() -> None:
    agent = _agent(agent_id="marketing.scout.regional_news")
    result = _build_system_prompt(agent, None)
    assert result is not None
    assert "Florida" in result
    assert "Texas" in result
    assert "All states" in result


# 5. Urgency tiers injection
def test_urgency_tiers_injected() -> None:
    agent = _agent(
        agent_id="marketing.qualifier.cross_reference",
        urgency_tiers={"hot": "RFPs and board votes only", "standard": "speculation"},
    )
    result = _build_system_prompt(agent, None)
    assert result is not None
    assert "## Urgency discipline" in result
    assert "hot" in result
    assert "RFPs and board votes only" in result
    assert "standard" in result
    assert "speculation" in result


# 6. Failure modes injection
def test_failure_modes_injected() -> None:
    agent = _agent(
        agent_id="marketing.qualifier.cross_reference",
        failure_modes=[{"name": "PDF garbage", "description": "Skip the row"}],
    )
    result = _build_system_prompt(agent, None)
    assert result is not None
    assert "## Failure modes" in result
    assert "PDF garbage" in result
    assert "Skip the row" in result


# 7. Defensive on None fields — no empty section stubs
def test_none_fields_omit_sections() -> None:
    agent = _agent(
        agent_id="marketing.qualifier.cross_reference",
        persona=None,
        urgency_tiers=None,
        failure_modes=None,
        implementation_notes=None,
    )
    result = _build_system_prompt(agent, None)
    text = result or ""
    assert "## Voice" not in text
    assert "## Urgency" not in text
    assert "## Failure modes" not in text
    assert "## Implementation notes" not in text


# 8. lru_cache works — same object identity
def test_cached_josh_spec_identity() -> None:
    spec1 = _cached_josh_spec()
    spec2 = _cached_josh_spec()
    assert spec1 is spec2
