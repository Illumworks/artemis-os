"""M5 — Marketing agent seed tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401
from artemis.marketing.seeds.marketing_agents import (
    DEFAULT_DOCS_ROOT,
    MARKETING_AGENT_SPECS,
    seed_marketing_agents,
)

pytestmark = pytest.mark.asyncio
EXPECTED = {spec.agent_id for spec in MARKETING_AGENT_SPECS}
TRUNCATE = text(
    "TRUNCATE agent_context, agent_run_trajectory_summaries, definition_proposals, "
    "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
)
SELECT_IDS = text("SELECT agent_id FROM agents WHERE agent_id LIKE 'marketing.%' ORDER BY agent_id")
SELECT_CREATED = text(
    "SELECT created_at FROM agents WHERE agent_id = 'marketing.scout.starbridge_researcher'"
)
SELECT_EDITED = text(
    "SELECT description, created_at FROM agents "
    "WHERE agent_id = 'marketing.scout.starbridge_researcher'"
)
UPDATE_OWNER = text(
    "UPDATE agents SET owner_user_id = 42 WHERE agent_id = 'marketing.content.asset_selector'"
)
SELECT_CONTRACTS = text(
    "SELECT persona, tools, owner_user_id FROM agents "
    "WHERE agent_id = 'marketing.content.asset_selector'"
)


async def _reset(session: AsyncSession) -> None:
    await session.execute(TRUNCATE)
    await session.commit()


async def test_populates_canonical_16_and_is_idempotent(db_session: AsyncSession) -> None:
    await _reset(db_session)
    first = await seed_marketing_agents(db_session)
    second = await seed_marketing_agents(db_session)
    rows = (await db_session.execute(SELECT_IDS)).scalars().all()
    assert (first["inserted"], second["inserted"], second["updated"], len(rows)) == (16, 0, 16, 16)
    assert set(rows) == EXPECTED


async def test_markdown_edit_then_reseed_updates_description(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _reset(db_session)
    docs_root = tmp_path / "agents"
    shutil.copytree(DEFAULT_DOCS_ROOT, docs_root)
    await seed_marketing_agents(db_session, docs_root=docs_root)
    created_at = (await db_session.execute(SELECT_CREATED)).scalar_one()
    path = docs_root / "scout/1.1-starbridge-researcher.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Convert Starbridge's stream of legislative and funding data into structured, district-attributed signals — same schema as all other scouts.",
            "Convert edited Starbridge data into durable, district-attributed campaign signals.",
        ),
        encoding="utf-8",
    )
    await seed_marketing_agents(db_session, docs_root=docs_root)
    row = (await db_session.execute(SELECT_EDITED)).mappings().one()
    assert (
        row["description"]
        == "Convert edited Starbridge data into durable, district-attributed campaign signals."
    )
    assert row["created_at"] == created_at


async def test_persona_owner_and_tools_contracts(db_session: AsyncSession) -> None:
    await _reset(db_session)
    await seed_marketing_agents(db_session)
    await db_session.execute(UPDATE_OWNER)
    await db_session.commit()
    await seed_marketing_agents(db_session)
    row = (await db_session.execute(SELECT_CONTRACTS)).mappings().one()
    assert set(row["persona"]) == {
        "name",
        "purpose",
        "voice_notes",
        "ghostwrite",
        "profile_image_path",
    }
    assert row["persona"]["purpose"] and row["persona"]["ghostwrite"] is False
    assert row["owner_user_id"] == 42
    assert isinstance(row["tools"], list) and all(isinstance(tool, str) for tool in row["tools"])
