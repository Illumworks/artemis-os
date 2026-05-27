"""M5 — Marketing agent seed tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.seeds.marketing_agents import (
    DEFAULT_DOCS_ROOT,
    MARKETING_AGENT_SPECS,
    MarketingAgentSpec,
    _extract_section,
    _failure_modes,
    _row,
    _urgency_tiers_v2,
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
    "SELECT persona, tools, owner_user_id, reason_codes_emitted FROM agents "
    "WHERE agent_id = 'marketing.content.asset_selector'"
)
SELECT_STARBRIDGE_CODES = text(
    "SELECT reason_codes_emitted FROM agents "
    "WHERE agent_id = 'marketing.scout.starbridge_researcher'"
)
SELECT_BLUEPRINT = text(
    "SELECT cadence_seconds, lifecycle_status, urgency_tiers, failure_modes, "
    "db_tables_touched, implementation_notes, inputs_required "
    "FROM agents WHERE agent_id = :agent_id"
)
UPDATE_BLUEPRINT = text(
    "UPDATE agents SET cadence_seconds = 123, lifecycle_status = 'operator_edit' "
    "WHERE agent_id = 'marketing.scout.starbridge_researcher'"
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


async def test_urgency_tiers_v2_accepts_bold_form() -> None:
    section = "- **hot** — RFPs and board votes\n- **standard** — speculation"
    assert _urgency_tiers_v2(section) == {
        "hot": "RFPs and board votes",
        "standard": "speculation",
    }


async def test_urgency_tiers_v2_accepts_reserve_for_form() -> None:
    section = _extract_section(
        (DEFAULT_DOCS_ROOT / "scout/1.2-regional-news-scout.md").read_text(encoding="utf-8"),
        "Urgency tiers",
    )
    tiers = _urgency_tiers_v2(section)
    assert tiers is not None
    assert tiers["standard"] == "speculation"
    assert tiers["hot"] == (
        "Formal RFPs; Board votes (passed); Official transitions (announced); "
        "Gubernatorial directives"
    )


async def test_urgency_tiers_v2_empty_returns_none() -> None:
    assert _urgency_tiers_v2("") is None


async def test_failure_modes_parses_regional_news_plain_bullets() -> None:
    section = _extract_section(
        (DEFAULT_DOCS_ROOT / "scout/1.2-regional-news-scout.md").read_text(encoding="utf-8"),
        "Failure modes",
    )
    modes = _failure_modes(section)
    assert modes is not None and len(modes) >= 4
    assert all(mode["name"] and "description" in mode for mode in modes)


async def test_row_extracts_implementation_notes_without_codex_suffix(tmp_path: Path) -> None:
    docs_root = tmp_path / "agents"
    (docs_root / "scout").mkdir(parents=True)
    (docs_root / "scout/test.md").write_text(
        "\n".join(
            [
                "# Agent 1.1 — Test Scout",
                "",
                "## Purpose",
                "Test purpose.",
                "",
                "## Prompt scaffolding",
                "Prompt.",
                "",
                "## Tools required",
                "- tool.call",
                "",
                "## Implementation notes",
                "Plain implementation notes.",
            ]
        ),
        encoding="utf-8",
    )
    row = _row(
        MarketingAgentSpec(
            "marketing.scout.starbridge_researcher",
            "scout/test.md",
            "haiku",
            "persistent",
            "auto",
            "signal",
        ),
        docs_root,
    )
    assert row["implementation_notes"] == "Plain implementation notes."


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


async def test_scout_reason_codes_emitted_sourced_from_josh_spec(
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    await seed_marketing_agents(db_session)
    expected_codes = [
        rc.code for rc in reason_codes_for_scout(parse_spec(), "starbridge_researcher")
    ]
    codes = (await db_session.execute(SELECT_STARBRIDGE_CODES)).scalar_one()
    assert codes == expected_codes
    await db_session.execute(
        text(
            "UPDATE agents SET reason_codes_emitted = '[\"CUSTOM_CODE\"]'::jsonb "
            "WHERE agent_id = 'marketing.scout.starbridge_researcher'"
        )
    )
    await db_session.commit()
    await seed_marketing_agents(db_session)
    assert (await db_session.execute(SELECT_STARBRIDGE_CODES)).scalar_one() == expected_codes


async def test_seed_extracts_operating_blueprint_fields(db_session: AsyncSession) -> None:
    await _reset(db_session)
    await seed_marketing_agents(db_session)

    scout = (
        (
            await db_session.execute(
                SELECT_BLUEPRINT, {"agent_id": "marketing.scout.starbridge_researcher"}
            )
        )
        .mappings()
        .one()
    )
    assert scout["cadence_seconds"] == 14400
    assert scout["lifecycle_status"] == "bench_test"
    assert scout["urgency_tiers"]["hot"].startswith("RFP deadline")
    assert scout["failure_modes"][0]["name"] == "Result doesn't map to known district"
    assert "signal_queue" in scout["db_tables_touched"]
    assert "STARBRIDGE_API_KEY" in {item["key"] for item in scout["inputs_required"]}
    assert "Credit usage logging" in scout["implementation_notes"]

    qualifier = (
        (
            await db_session.execute(
                SELECT_BLUEPRINT, {"agent_id": "marketing.qualifier.cross_reference"}
            )
        )
        .mappings()
        .one()
    )
    assert qualifier["cadence_seconds"] == 300
    assert qualifier["urgency_tiers"] is None

    content = (
        (
            await db_session.execute(
                SELECT_BLUEPRINT, {"agent_id": "marketing.content.brief_assembler"}
            )
        )
        .mappings()
        .one()
    )
    assert content["cadence_seconds"] is None
    assert "campaign_workspaces" in content["db_tables_touched"]


async def test_reseed_preserves_existing_blueprint_when_markdown_is_silent(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _reset(db_session)
    docs_root = tmp_path / "agents"
    shutil.copytree(DEFAULT_DOCS_ROOT, docs_root)
    await seed_marketing_agents(db_session, docs_root=docs_root)
    await db_session.execute(UPDATE_BLUEPRINT)
    await db_session.commit()

    path = docs_root / "scout/1.1-starbridge-researcher.md"
    body = path.read_text(encoding="utf-8")
    body = body.replace("**Status:** **Bench-test for 6–12 months.**", "**No status:**")
    body = body.replace("## Cadence", "## Cadence removed")
    path.write_text(body, encoding="utf-8")

    await seed_marketing_agents(db_session, docs_root=docs_root)
    row = (
        (
            await db_session.execute(
                SELECT_BLUEPRINT, {"agent_id": "marketing.scout.starbridge_researcher"}
            )
        )
        .mappings()
        .one()
    )
    assert row["cadence_seconds"] == 123
    assert row["lifecycle_status"] == "operator_edit"
