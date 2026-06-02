"""Tests for campaign_brief.write tool — permission check."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.tools.campaign_brief import _factory
from artemis.tools.context import ToolContext


def _ctx(session: AsyncSession, agent_id: str) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


@pytest.mark.asyncio
async def test_permission_denied(db_session: AsyncSession) -> None:
    """Non-brief_assembler agent → PERMISSION_DENIED."""
    _, impl = _factory(_ctx(db_session, "marketing.scout.regional_news"))
    result = await impl({"candidateId": 1, "content": {}})
    assert result.startswith("PERMISSION_DENIED")


@pytest.mark.asyncio
async def test_missing_candidate_id(db_session: AsyncSession) -> None:
    """Missing candidateId → VALIDATION_ERROR."""
    _, impl = _factory(_ctx(db_session, "marketing.content.brief_assembler"))
    result = await impl({"content": {}})
    assert result.startswith("VALIDATION_ERROR")
