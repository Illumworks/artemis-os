"""P2 — signal_queue.write tool tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.models import SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.signal_queue import _factory

_SCOUT_AGENT_ID = "marketing.scout.regional_news"
_SCOUT_SLUG = "regional_news"


def _ctx(session: AsyncSession, agent_id: str = _SCOUT_AGENT_ID) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-test-001",
        pipeline_run_id=None,
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    spec = parse_spec()
    codes = reason_codes_for_scout(spec, _SCOUT_SLUG)
    first_code = codes[0].code if codes else "VENDOR_DISSATISFACTION"
    base: dict[str, Any] = {
        "sourceType": "news_article",
        "headline": "District signs new contract",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [first_code],
        "evidence": "Announced publicly.",
        "sourceUrl": "https://example.com/article",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_valid_signal_written(db_session: AsyncSession) -> None:
    """Valid signal lands in signal_queue with correct provenance."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)
    result = await impl(_valid_payload())
    data = json.loads(result)
    assert data["status"] == "written"
    row = await db_session.get(SignalQueue, data["signal_id"])
    assert row is not None
    assert row.signal_status == "pending_qualification"
    assert row.provenance is not None
    assert row.provenance["agent_run_id"] == "run-test-001"


@pytest.mark.asyncio
async def test_antispoof_discovered_by(db_session: AsyncSession) -> None:
    """discoveredBy in LLM payload is overridden to the agent's slug."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)
    payload = _valid_payload()
    payload["discoveredBy"] = "someone_else"
    result = await impl(payload)
    data = json.loads(result)
    row = await db_session.get(SignalQueue, data["signal_id"])
    assert row is not None
    assert row.discovered_by == _SCOUT_SLUG


@pytest.mark.asyncio
async def test_reason_code_outside_allowlist(db_session: AsyncSession) -> None:
    """Reason code not in scout's allowlist → VALIDATION_ERROR, no row written."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)
    result = await impl(_valid_payload(reasonCodes=["TOTALLY_FAKE_CODE_XYZ"]))
    assert result.startswith("VALIDATION_ERROR")
    assert "TOTALLY_FAKE_CODE_XYZ" in result
    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_non_scout_agent_permission_denied(db_session: AsyncSession) -> None:
    """Non-scout agent → PERMISSION_DENIED, no row written."""
    ctx = _ctx(db_session, agent_id="marketing.qualifier.cross_reference")
    _, impl = _factory(ctx)
    result = await impl(_valid_payload())
    assert result.startswith("PERMISSION_DENIED")
    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_invalid_source_type_validation_error(db_session: AsyncSession) -> None:
    """Invalid sourceType → VALIDATION_ERROR from normalize_intake_payload."""
    ctx = _ctx(db_session)
    _, impl = _factory(ctx)
    result = await impl(_valid_payload(sourceType="not_a_real_type"))
    assert result.startswith("VALIDATION_ERROR")
    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 0
