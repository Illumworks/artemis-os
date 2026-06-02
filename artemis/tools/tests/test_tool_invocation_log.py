"""CC17 — MCP tool invocation log tests.

Verifies that every tool dispatch through the MCP server writes a row to
tool_invocations with the correct agent_run_id, artemis-style tool_name,
and success flag — including failure paths (exception raised, result that
starts with VALIDATION_ERROR / PERMISSION_DENIED / STUB:).

Strategy: build a server via _build_server (no stdio transport needed),
then invoke the registered CallToolRequest handler directly via
``server.request_handlers[mcp_types.CallToolRequest]``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import mcp.types as mcp_types
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.types import Tool
from artemis.builders import repository as repo
from artemis.db import SessionLocal
from artemis.tools.mcp_server import _build_server, build_tool_set, mcp_tool_name
from artemis.tools.models import ToolInvocation

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


def _valid_signal_payload() -> dict[str, Any]:
    from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout

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


async def _dispatch(server: Any, tool_name: str, args: dict[str, Any]) -> str:
    """Invoke the server's CallToolRequest handler and return the result text."""
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=tool_name, arguments=args),
    )
    handler = server.request_handlers[mcp_types.CallToolRequest]
    server_result = await handler(req)
    # ServerResult wraps a CallToolResult.
    call_result = server_result.root
    return call_result.content[0].text if call_result.content else ""


def _fake_tool_set(impl: Any) -> dict[str, tuple[Tool, Any]]:
    """Build a single-tool set with the given impl, artemis-named signal_queue.write."""
    fake_def = Tool(
        name="signal_queue.write",
        description="fake",
        input_schema={"type": "object", "properties": {}},
    )
    return {mcp_tool_name("signal_queue.write"): (fake_def, impl)}


# ── Test 1: successful tool call logs one row with correct fields ─────────────


@pytest.mark.asyncio
async def test_successful_tool_call_logs_invocation(db_session: AsyncSession) -> None:
    """A real signal_queue.write through _build_server writes one tool_invocations row.

    Confirms: agent_run_id matches, tool_name is artemis-style (not MCP-prefixed),
    success=True.  Verified via a FRESH session (proves durable independent commit).
    """
    run_id = "cc17-success-run-00000001"
    await _seed_agent(db_session, "marketing.scout.regional_news", _REGIONAL_NEWS_TOOLS)
    tool_set = await build_tool_set(db_session, "marketing.scout.regional_news", run_id)
    server = _build_server(db_session, tool_set, run_id, None)

    text = await _dispatch(server, "signal_queue_write", _valid_signal_payload())
    assert "TOOL_ERROR" not in text
    assert "UNKNOWN_TOOL" not in text

    async with SessionLocal() as fresh:
        rows = (
            (
                await fresh.execute(
                    select(ToolInvocation).where(ToolInvocation.agent_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1
    row = rows[0]
    assert row.agent_run_id == run_id
    assert row.tool_name == "signal_queue.write"  # artemis-style, not "signal_queue_write"
    assert row.success is True
    assert row.result_preview is not None


# ── Test 2: exception in tool impl → success=False, exception summary ────────


@pytest.mark.asyncio
async def test_failing_tool_logs_success_false(db_session: AsyncSession) -> None:
    """A tool impl that raises logs success=False and the exception message."""
    run_id = "cc17-fail-run-00000002"
    bad_impl = AsyncMock(side_effect=RuntimeError("simulated tool explosion"))
    server = _build_server(db_session, _fake_tool_set(bad_impl), run_id, None)

    text = await _dispatch(server, "signal_queue_write", {"x": 1})
    # Server never crashes — returns error text.
    assert text  # non-empty

    async with SessionLocal() as fresh:
        rows = (
            (
                await fresh.execute(
                    select(ToolInvocation).where(ToolInvocation.agent_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "signal_queue.write"
    assert row.success is False
    assert "simulated tool explosion" in (row.result_preview or "")


# ── Test 3: STUB / VALIDATION_ERROR / PERMISSION_DENIED → success=False ──────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_prefix",
    [
        "STUB: not implemented",
        "VALIDATION_ERROR: missing field",
        "PERMISSION_DENIED: agent not a scout",
    ],
)
async def test_failure_prefix_results_log_success_false(
    db_session: AsyncSession, failure_prefix: str
) -> None:
    """Results starting with known failure prefixes are logged with success=False."""
    # Use prefix-based run_id to avoid cross-test contamination.
    run_id = f"cc17-prefix-{abs(hash(failure_prefix)) % 10**8:08d}"
    stub_impl = AsyncMock(return_value=failure_prefix)
    server = _build_server(db_session, _fake_tool_set(stub_impl), run_id, None)

    text = await _dispatch(server, "signal_queue_write", {})
    assert text == failure_prefix

    async with SessionLocal() as fresh:
        rows = (
            (
                await fresh.execute(
                    select(ToolInvocation).where(ToolInvocation.agent_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1
    assert rows[0].success is False
    assert failure_prefix[:20] in (rows[0].result_preview or "")


# ── Test 4: UNKNOWN_TOOL → success=False ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_tool_call_logs_success_false(db_session: AsyncSession) -> None:
    """Calling an MCP name not in the tool_set logs success=False."""
    run_id = "cc17-unknown-run-00000004"
    server = _build_server(db_session, {}, run_id, None)

    text = await _dispatch(server, "totally_bogus_tool", {})
    assert "UNKNOWN_TOOL" in text

    async with SessionLocal() as fresh:
        rows = (
            (
                await fresh.execute(
                    select(ToolInvocation).where(ToolInvocation.agent_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1
    assert rows[0].success is False
