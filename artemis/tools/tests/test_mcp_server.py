"""CC1 — Artemis Tools MCP Server tests.

Targets the pure, testable ``build_tool_set`` + the name-mapping helpers — no
live stdio transport required. Uses the shared tools-test DB fixture
(``db_session``), seeding throwaway agents per test rather than mutating any
seeded rows.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.db import SessionLocal
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.models import SignalQueue
from artemis.tools.mcp_server import (
    artemis_tool_name,
    build_tool_set,
    mcp_tool_name,
)

# Verbatim dev-DB tool lists for the two claude-code scout agents.
_REGIONAL_NEWS_TOOLS = [
    "news_api.search",
    "board_minutes.fetch",
    "state_doe.fetch",
    "pdf_extractor.extract",
    "signal_queue.write",
    "memory_layer.upsert_last_seen",
    "territory_config.get_priority_states",
    "reason_codes.get_allowlist",
]
_LEGISLATIVE_TOOLS = [
    "legiscan.search",
    "legiscan.get_bill",
    "contact_db_stub.has_contact",
    "signal_queue.write",
    "memory_layer.upsert_last_seen",
    "territory_config.get_priority_states",
    "territory_config.get_watch_keywords",
    "reason_codes.get_allowlist",
]


async def _seed_agent(session: AsyncSession, agent_id: str, tools: list[str]) -> None:
    async with session.begin():
        await repo.create_agent(
            session,
            agent_id=agent_id,
            name=agent_id,
            system_prompt="test agent",
            tools=tools,
            model="claude-sonnet-4-6",
            provider="claude-code",
        )


def _valid_regional_news_payload() -> dict[str, Any]:
    spec = parse_spec()
    codes = reason_codes_for_scout(spec, "regional_news")
    first_code = codes[0].code if codes else "VENDOR_DISSATISFACTION"
    return {
        "sourceType": "news_article",
        "headline": "District evaluates new reading curriculum",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [first_code],
        "evidence": "Board announced publicly.",
        "sourceUrl": "https://example-news.com/district",
    }


@pytest.mark.asyncio
async def test_scoping_regional_news(db_session: AsyncSession) -> None:
    """regional_news exposes exactly its declared tools (MCP-named), nothing else."""
    await _seed_agent(db_session, "marketing.scout.regional_news", _REGIONAL_NEWS_TOOLS)
    tool_set = await build_tool_set(db_session, "marketing.scout.regional_news", "run-rn-1")

    expected = {mcp_tool_name(n) for n in _REGIONAL_NEWS_TOOLS}
    assert set(tool_set) == expected
    assert "signal_queue_write" in tool_set
    assert "news_api_search" in tool_set
    assert "legiscan_search" not in tool_set
    assert "legiscan_get_bill" not in tool_set


@pytest.mark.asyncio
async def test_scoping_legislative(db_session: AsyncSession) -> None:
    """legislative exposes legiscan_* but NOT news_api_search."""
    await _seed_agent(db_session, "marketing.scout.legislative", _LEGISLATIVE_TOOLS)
    tool_set = await build_tool_set(db_session, "marketing.scout.legislative", "run-leg-1")

    assert set(tool_set) == {mcp_tool_name(n) for n in _LEGISLATIVE_TOOLS}
    assert "legiscan_search" in tool_set
    assert "legiscan_get_bill" in tool_set
    assert "news_api_search" not in tool_set


@pytest.mark.asyncio
async def test_tool_call_writes_signal(db_session: AsyncSession) -> None:
    """signal_queue_write through the scoped set + commit lands a row.

    Verified by querying in a FRESH session — proving the per-write commit, as
    the real server (separate process, own session) does.
    """
    await _seed_agent(db_session, "marketing.scout.regional_news", _REGIONAL_NEWS_TOOLS)
    tool_set = await build_tool_set(db_session, "marketing.scout.regional_news", "run-write-1")

    _tool_def, impl = tool_set["signal_queue_write"]
    result = await impl(_valid_regional_news_payload())
    assert '"status": "written"' in result
    await db_session.commit()

    async with SessionLocal() as fresh:
        rows = (await fresh.execute(select(SignalQueue))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.discovered_by == "regional_news"
        assert row.signal_status == "pending_qualification"
        assert row.provenance is not None
        assert row.provenance["agent_run_id"] == "run-write-1"
        assert row.provenance["agent_id"] == "marketing.scout.regional_news"


@pytest.mark.asyncio
async def test_permission_denied_for_non_scout(db_session: AsyncSession) -> None:
    """A non-scout agent calling signal_queue_write → PERMISSION_DENIED, no row.

    The underlying P2/P3 impl keys permission off ``ctx.agent_id`` starting with
    ``marketing.scout.`` — so a non-scout agent id is rejected even though it
    declares the tool.
    """
    await _seed_agent(db_session, "marketing.qualifier.cross_reference", ["signal_queue.write"])
    tool_set = await build_tool_set(db_session, "marketing.qualifier.cross_reference", "run-perm-1")

    _tool_def, impl = tool_set["signal_queue_write"]
    result = await impl(_valid_regional_news_payload())
    assert result.startswith("PERMISSION_DENIED")

    async with SessionLocal() as fresh:
        rows = (await fresh.execute(select(SignalQueue))).scalars().all()
        assert len(rows) == 0


def test_mcp_tool_name_round_trips() -> None:
    """signal_queue.write ↔ signal_queue_write in both directions."""
    assert mcp_tool_name("signal_queue.write") == "signal_queue_write"
    assert artemis_tool_name("signal_queue_write") == "signal_queue.write"
    # underscores already in a segment must survive the round trip
    assert artemis_tool_name(mcp_tool_name("news_api.search")) == "news_api.search"


@pytest.mark.asyncio
async def test_unknown_tool_is_skipped(db_session: AsyncSession) -> None:
    """A bogus tool name in agent.tools is skipped, not raised; the rest stay."""
    await _seed_agent(
        db_session,
        "marketing.scout.regional_news",
        ["signal_queue.write", "totally.bogus_tool"],
    )
    tool_set = await build_tool_set(db_session, "marketing.scout.regional_news", "run-skip-1")

    assert set(tool_set) == {"signal_queue_write"}
    assert "totally_bogus_tool" not in tool_set
