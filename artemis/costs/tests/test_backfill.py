"""Tests for artemis.costs.backfill — historical data seeding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.backfill import (
    _backfill_agent_runs,
    _backfill_fa_messages,
    _backfill_workflow_runs,
)
from artemis.costs.models import CostEvent


async def _seed_agent_run(session: AsyncSession, *, input_tokens: int = 100) -> int:
    """Insert a minimal agent_runs row and return its id."""
    # Insert without FK to agents (agent_id nullable)
    result = await session.execute(
        text("""
            INSERT INTO agent_runs (run_id, agent_id, status, cost_input_tokens, cost_output_tokens)
            VALUES (:run_id, NULL, 'completed', :inp, :out)
            RETURNING id
        """),
        {"run_id": f"test-run-{datetime.now(UTC).timestamp()}", "inp": input_tokens, "out": 50},
    )
    row_id = result.scalar_one()
    await session.commit()
    return int(row_id)


async def _seed_fa_message(session: AsyncSession) -> int:
    """Insert minimal floating_artemis_sessions + messages rows. Returns message id."""
    import uuid

    sid = str(uuid.uuid4())
    await session.execute(
        text("""
            INSERT INTO floating_artemis_sessions (session_id, provider, model)
            VALUES (:sid, 'anthropic', 'claude-sonnet-4-6')
        """),
        {"sid": sid},
    )
    result = await session.execute(
        text("""
            INSERT INTO floating_artemis_messages
                (session_id, role, content, cost_input_tokens, cost_output_tokens)
            VALUES (:sid, 'assistant', '[]'::jsonb, 200, 100)
            RETURNING id
        """),
        {"sid": sid},
    )
    msg_id = result.scalar_one()
    await session.commit()
    return int(msg_id)


async def _seed_workflow_run(session: AsyncSession) -> int:
    """Insert a minimal workflow_runs row with total_cost_usd > 0. Returns id."""
    import uuid

    result = await session.execute(
        text("""
            INSERT INTO workflow_runs (run_id, status, total_cost_usd)
            VALUES (:run_id, 'completed', 1.50)
            RETURNING id
        """),
        {"run_id": str(uuid.uuid4())},
    )
    row_id = result.scalar_one()
    await session.commit()
    return int(row_id)


@pytest.fixture
async def clean_session(db_session: AsyncSession) -> AsyncSession:
    """Session with cost_events truncated (db_session fixture handles it)."""
    return db_session


@pytest.mark.asyncio
async def test_backfill_agent_runs_produces_rows(db_session: AsyncSession) -> None:
    """N agent_runs with tokens → N cost_events rows."""
    # Seed 2 agent run rows
    await _seed_agent_run(db_session, input_tokens=100)
    await _seed_agent_run(db_session, input_tokens=200)

    count = await _backfill_agent_runs(db_session, dry_run=False)
    await db_session.commit()

    assert count == 2
    result = await db_session.execute(select(CostEvent).where(CostEvent.source_kind == "agent_run"))
    rows = result.scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_backfill_agent_runs_idempotent(db_session: AsyncSession) -> None:
    """Re-running backfill produces no duplicate rows."""
    await _seed_agent_run(db_session, input_tokens=100)

    count1 = await _backfill_agent_runs(db_session, dry_run=False)
    await db_session.commit()
    count2 = await _backfill_agent_runs(db_session, dry_run=False)
    await db_session.commit()

    assert count1 == 1
    assert count2 == 0  # idempotent — no new rows on re-run

    result = await db_session.execute(select(CostEvent).where(CostEvent.source_kind == "agent_run"))
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_backfill_dry_run_no_writes(db_session: AsyncSession) -> None:
    """Dry-run reports count > 0 but writes no rows."""
    await _seed_agent_run(db_session, input_tokens=100)

    count = await _backfill_agent_runs(db_session, dry_run=True)
    # dry_run does not commit, but even if it flushed, it shouldn't write
    # We don't commit in the dry-run path, so no rows should persist.
    assert count == 1

    # Verify nothing was written
    result = await db_session.execute(select(CostEvent))
    assert len(result.scalars().all()) == 0


@pytest.mark.asyncio
async def test_backfill_fa_messages(db_session: AsyncSession) -> None:
    """FA assistant messages with non-zero tokens get backfilled."""
    await _seed_fa_message(db_session)

    count = await _backfill_fa_messages(db_session, dry_run=False)
    await db_session.commit()

    assert count == 1
    result = await db_session.execute(
        select(CostEvent).where(CostEvent.source_kind == "fa_message")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].feature_tag == "floating_artemis"


@pytest.mark.asyncio
async def test_backfill_workflow_runs(db_session: AsyncSession) -> None:
    """Workflow runs with total_cost_usd > 0 get a lossy pre-aggregated row."""
    await _seed_workflow_run(db_session)

    count = await _backfill_workflow_runs(db_session, dry_run=False)
    await db_session.commit()

    assert count == 1
    result = await db_session.execute(
        select(CostEvent).where(CostEvent.source_kind == "workflow_run")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].cost_usd == pytest.approx(1.50)
    assert rows[0].error_kind == "backfill_lossy"
