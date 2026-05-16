"""Migration smoke test — confirms 0006 tables exist in pg_class after upgrade."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_EXPECTED_TABLES = {
    "agents",
    "agent_runs",
    "agent_context",
    "skills",
    "workflows",
    "workflow_runs",
    "agent_chains",
    "agent_dags",
}


@pytest.mark.asyncio
async def test_builder_tables_exist(db_session: AsyncSession) -> None:
    """All 8 tables from migration 0006 should be in pg_class."""
    result = await db_session.execute(
        text("SELECT relname FROM pg_class WHERE relkind = 'r' AND relname = ANY(:names)"),
        {"names": list(_EXPECTED_TABLES)},
    )
    found = {row[0] for row in result.fetchall()}
    missing = _EXPECTED_TABLES - found
    assert not missing, f"Missing tables after migration 0006: {missing}"


@pytest.mark.asyncio
async def test_agents_table_columns(db_session: AsyncSession) -> None:
    """Spot-check key columns exist on the agents table."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'agents' AND table_schema = 'public'"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    for col in ("agent_id", "model", "provider", "max_iterations", "owner_user_id", "tools"):
        assert col in cols, f"Column '{col}' missing from agents table"


@pytest.mark.asyncio
async def test_agent_runs_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'agent_runs' AND table_schema = 'public'"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    for col in ("run_id", "agent_id", "status", "cost_input_tokens", "cost_output_tokens"):
        assert col in cols, f"Column '{col}' missing from agent_runs table"


@pytest.mark.asyncio
async def test_agent_context_unique_constraint(db_session: AsyncSession) -> None:
    """uq_agent_context_run_key constraint should exist."""
    result = await db_session.execute(
        text("SELECT conname FROM pg_constraint WHERE conname = 'uq_agent_context_run_key'")
    )
    row = result.fetchone()
    assert row is not None, "uq_agent_context_run_key constraint not found"
