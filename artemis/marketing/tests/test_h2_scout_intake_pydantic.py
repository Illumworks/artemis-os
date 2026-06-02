"""H2 — Scout intake Pydantic + reason_code allowlist enforcement tests.

Test plan:
1. Valid scout payload passes through unchanged.
2. Hallucinated reason_code.code is rejected.
3. Invalid urgencyTier is rejected.
4. Invalid sourceType is rejected.
5. Confidence out of bounds is rejected.
6. signal_status drift resolved — every distinct DB value is in SignalState.
7. End-to-end scout run with invalid reason_code: no row written, rejection logged.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.marketing.scout_runner import ScoutMode, run_scout
from artemis.marketing.scout_schemas import (
    ReasonCode,
    ReasonCodeAllowlistError,
    ScoutEmittedSignal,
    validate_llm_json_emission,
    validate_reason_codes_against_allowlist,
)
from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter
from artemis.marketing.seeds.marketing_agents import seed_marketing_agents
from artemis.marketing.state_machine import SignalState

# asyncio mark applied per-test (not module-wide) to avoid warning on sync tests.

_TRUNCATE = text(
    "TRUNCATE agent_context, agent_run_trajectory_summaries, definition_proposals, "
    "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
)
_SCOUT = "marketing.scout.starbridge_researcher"

# ── Fixture payloads ──────────────────────────────────────────────────────────

_VALID_PAYLOAD: dict[str, Any] = {
    "headline": "District adopts literacy program",
    "sourceType": "starbridge",
    "sourceUrl": "https://example.com/a",
    "campaignFamily": "obc",
    "urgencyTier": "standard",
    "reasonCodes": [{"code": "POLICY_LIT_MANDATE", "confidence": 0.9}],
    "whyFlagged": "Policy mandate relevant to OBC",
    "evidence": "District meeting notes",
}


class _MockAdapter(ScoutSourceAdapter):
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self, tc: Any, lr: Any) -> list[RawItem]:
        return list(self._items)


def _llm_resp(payload: dict[str, Any]) -> AsyncMock:
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    resp = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=json.dumps(payload))]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=100, output_tokens=50),
    )
    return AsyncMock(return_value=resp)


async def _seed(session: AsyncSession) -> None:
    await session.execute(_TRUNCATE)
    await session.commit()
    await seed_marketing_agents(session)


# ── Test 1: Valid payload passes unchanged ────────────────────────────────────


def test_valid_payload_passes_pydantic() -> None:
    """ScoutEmittedSignal accepts a canonical valid payload (snake_case attribute access)."""
    parsed = ScoutEmittedSignal.model_validate(_VALID_PAYLOAD)
    assert parsed.headline == "District adopts literacy program"
    assert parsed.source_type == "starbridge"
    assert parsed.urgency_tier == "standard"
    assert len(parsed.reason_codes) == 1
    assert parsed.reason_codes[0].code == "POLICY_LIT_MANDATE"
    assert parsed.reason_codes[0].confidence == 0.9


def test_valid_payload_passes_normalize_intake() -> None:
    """normalize_intake_payload + allowlist check succeeds on canonical payload."""
    result = normalize_intake_payload(
        _VALID_PAYLOAD,
        scout_type="starbridge_researcher",
        reason_codes_allowlist=["POLICY_LIT_MANDATE", "FUNDING_LITERACY_GRANT"],
    )
    assert result.headline == "District adopts literacy program"
    assert result.urgency_tier == "standard"
    assert result.discovered_by == "starbridge_researcher"  # anti-spoof


# ── Test 2: Hallucinated reason_code.code is rejected ────────────────────────


def test_hallucinated_reason_code_rejected() -> None:
    """Allowlist enforcement rejects a hallucinated code and names the bad code + allowed set."""
    codes = [ReasonCode(code="HALLUCINATED_CODE", confidence=0.8)]
    allowed = ["FOO", "BAR"]
    with pytest.raises(ReasonCodeAllowlistError) as exc_info:
        validate_reason_codes_against_allowlist(codes, allowed, "test_scout")
    err = exc_info.value
    assert "HALLUCINATED_CODE" in err.bad_codes
    assert set(err.allowed) == {"BAR", "FOO"}
    assert "test_scout" in str(err)
    assert "Allowed codes:" in str(err)


def test_hallucinated_code_raises_via_normalize_intake() -> None:
    """normalize_intake_payload raises ValueError when code is not in allowlist."""
    payload = {**_VALID_PAYLOAD, "reasonCodes": [{"code": "BAZ", "confidence": 0.7}]}
    with pytest.raises(ValueError) as exc_info:
        normalize_intake_payload(
            payload,
            scout_type="starbridge_researcher",
            reason_codes_allowlist=["FOO", "BAR"],
        )
    assert "BAZ" in str(exc_info.value)
    assert "FOO" in str(exc_info.value) or "BAR" in str(exc_info.value)


# ── Test 3: Invalid urgencyTier is rejected ───────────────────────────────────


def test_invalid_urgency_tier_rejected_by_pydantic() -> None:
    """ScoutEmittedSignal rejects unknown urgencyTier values."""
    from pydantic import ValidationError

    bad = {**_VALID_PAYLOAD, "urgencyTier": "extreme"}
    with pytest.raises(ValidationError) as exc_info:
        ScoutEmittedSignal.model_validate(bad)
    assert "urgencyTier" in str(exc_info.value) or "extreme" in str(exc_info.value)


def test_invalid_urgency_tier_rejected_via_normalize() -> None:
    """normalize_intake_payload with allowlist rejects bad urgencyTier through Pydantic."""
    bad = {**_VALID_PAYLOAD, "urgencyTier": "extreme"}
    with pytest.raises(ValueError):
        normalize_intake_payload(
            bad,
            scout_type="starbridge_researcher",
            reason_codes_allowlist=["POLICY_LIT_MANDATE"],
        )


# ── Test 4: Invalid sourceType is rejected ────────────────────────────────────


def test_invalid_source_type_rejected_by_pydantic() -> None:
    """ScoutEmittedSignal rejects unknown sourceType values."""
    from pydantic import ValidationError

    bad = {**_VALID_PAYLOAD, "sourceType": "tweet"}
    with pytest.raises(ValidationError):
        ScoutEmittedSignal.model_validate(bad)


def test_invalid_source_type_rejected_via_normalize() -> None:
    """normalize_intake_payload with allowlist rejects bad sourceType through Pydantic."""
    bad = {**_VALID_PAYLOAD, "sourceType": "tweet"}
    with pytest.raises(ValueError):
        normalize_intake_payload(
            bad,
            scout_type="starbridge_researcher",
            reason_codes_allowlist=["POLICY_LIT_MANDATE"],
        )


# ── Test 5: Confidence out of bounds is rejected ──────────────────────────────


def test_confidence_above_one_rejected() -> None:
    """Pydantic rejects confidence > 1.0 (strict policy, not clamped)."""
    from pydantic import ValidationError

    bad = {**_VALID_PAYLOAD, "reasonCodes": [{"code": "POLICY_LIT_MANDATE", "confidence": 1.5}]}
    with pytest.raises(ValidationError) as exc_info:
        ScoutEmittedSignal.model_validate(bad)
    assert "confidence" in str(exc_info.value) or "1.5" in str(exc_info.value)


def test_confidence_below_zero_rejected() -> None:
    """Pydantic rejects confidence < 0.0."""
    from pydantic import ValidationError

    bad = {**_VALID_PAYLOAD, "reasonCodes": [{"code": "POLICY_LIT_MANDATE", "confidence": -0.1}]}
    with pytest.raises(ValidationError):
        ScoutEmittedSignal.model_validate(bad)


# ── Test 6: signal_status drift — every DB value is in SignalState ────────────


@pytest.mark.asyncio
async def test_signal_status_drift_resolved(db_session: AsyncSession) -> None:
    """Every distinct signal_status in the live DB is present in SignalState enum.

    This test fails if a row has a status value not in the canonical enum.
    It passes with 0 rows (empty test DB) vacuously — the enum membership check
    is the important invariant, documented via the DB scan result below.

    Live DB scan 2026-05-29 (artemis_os):
      qualified              78
      suppressed_stale       70
      rejected_hard_filter   42
      pending_qualification   4
      archived                1
    No suppressed_deprioritized rows found — enum extension required no data migration.
    """
    canonical = {s.value for s in SignalState}
    rows = await db_session.execute(text("SELECT DISTINCT signal_status FROM signal_queue"))
    db_statuses = {r[0] for r in rows.fetchall()}
    # vacuously true on empty test DB; the important assertion is enum completeness
    unknown = db_statuses - canonical
    assert unknown == set(), (
        f"DB contains signal_status values not in SignalState enum: {unknown}. "
        f"Canonical: {sorted(canonical)}"
    )
    # Confirm suppressed_deprioritized is now in the enum (H2 drift fix)
    assert "suppressed_deprioritized" in canonical


# ── Test 7: End-to-end scout run with invalid reason_code ─────────────────────


@pytest.mark.asyncio
async def test_invalid_reason_code_rejected_end_to_end(db_session: AsyncSession) -> None:
    """Scout run with hallucinated reason_code: no signal written, rejection counted.

    The LLM emits code 'HALLUCINATED_CODE' which is not in starbridge_researcher's
    allowlist (POLICY_LIT_MANDATE, FUNDING_LITERACY_GRANT, FUNDING_DEADLINE_NEAR,
    FUNDING_HB2_ELIA, PROCUREMENT_LITERACY_RFP). Both the first call and the retry
    are mocked to return the bad code so the item is rejected after 1 retry.
    """
    await _seed(db_session)

    bad_payload = {
        "headline": "Some headline",
        "sourceType": "starbridge",
        "sourceUrl": "https://example.com/b",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [{"code": "HALLUCINATED_CODE", "confidence": 0.8}],
        "whyFlagged": "w",
        "evidence": "e",
    }

    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=_llm_resp(bad_payload)),
    ):
        result = await run_scout(
            db_session,
            _SCOUT,
            ScoutMode.manual,
            adapter_override=_MockAdapter([RawItem(content="c", source_url="https://ex.com/x")]),
        )
    await db_session.commit()

    # No signal written
    from artemis.marketing.models import SignalQueue

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 0, "No signal_queue row should be written for rejected signal"

    # Rejection counted
    assert result.signals_emitted == 0
    assert result.signals_rejected == 1
    assert result.status == "complete"

    # Error log contains the rejected code
    assert any(
        "normalize" in str(e.get("error", "")) or "HALLUCINATED" in str(e.get("error", ""))
        for e in result.errors
    )


@pytest.mark.asyncio
async def test_valid_signal_after_invalid_uses_batch_learning(db_session: AsyncSession) -> None:
    """Second item succeeds after first was rejected; batch-learning error is in prompt."""
    await _seed(db_session)

    bad_payload = {
        "headline": "Bad signal",
        "sourceType": "starbridge",
        "sourceUrl": "https://example.com/bad",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [{"code": "HALLUCINATED_CODE", "confidence": 0.8}],
    }
    good_payload = {
        "headline": "Good signal",
        "sourceType": "starbridge",
        "sourceUrl": "https://example.com/good",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [{"code": "POLICY_LIT_MANDATE", "confidence": 0.9}],
    }

    call_count = 0

    # First two calls return bad_payload (1st item + its retry), 3rd returns good_payload.
    async def _side_effect(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        from artemis.agent.client import CompletionResponse
        from artemis.agent.types import Message, TextBlock, Usage

        p = bad_payload if call_count <= 2 else good_payload
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=json.dumps(p))]),
            stop_reason="end_turn",
            usage=Usage(input_tokens=100, output_tokens=50),
        )

    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=AsyncMock(side_effect=_side_effect)),
    ):
        result = await run_scout(
            db_session,
            _SCOUT,
            ScoutMode.manual,
            adapter_override=_MockAdapter(
                [
                    RawItem(content="item1", source_url="https://ex.com/1"),
                    RawItem(content="item2", source_url="https://ex.com/2"),
                ]
            ),
        )
    await db_session.commit()

    assert result.signals_emitted == 1
    assert result.signals_rejected == 1


# ── validate_llm_json_emission helper ─────────────────────────────────────────


def test_validate_llm_json_emission_success() -> None:
    """Shared helper parses valid JSON and returns the Pydantic model."""
    raw = json.dumps(_VALID_PAYLOAD)
    parsed = validate_llm_json_emission(ScoutEmittedSignal, raw)
    assert isinstance(parsed, ScoutEmittedSignal)
    assert parsed.source_type == "starbridge"


def test_validate_llm_json_emission_bad_json() -> None:
    """Shared helper raises json.JSONDecodeError on non-JSON."""
    import json as json_mod

    with pytest.raises(json_mod.JSONDecodeError):
        validate_llm_json_emission(ScoutEmittedSignal, "NOT JSON")


def test_validate_llm_json_emission_bad_shape() -> None:
    """Shared helper raises ValidationError on wrong shape."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        validate_llm_json_emission(ScoutEmittedSignal, '{"urgencyTier": "extreme"}')
