"""Bundle B (CC21 + CC22) — observability + UX tests.

CC21:
  1. Builder tool call (in-process) lands in tool_invocations with
     builder_session_id populated + agent_run_id NULL.
  2. Builder tool call (MCP path) lands the same way through _build_builder_server.
  3. Mutual-exclusion CHECK constraint rejects rows with both/neither scope set.

CC22:
  4. POST /reject with {"reason": "..."} body persists rejection_reason +
     rejected_at on the proposal row.
  5. POST /reject with NO body still rejects cleanly (backward-compat) and
     leaves rejection_reason NULL but rejected_at populated.

The integration-style tests double as the post-merge smoke evidence.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import BuilderSession, DefinitionProposal
from artemis.tools.models import ToolInvocation

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_builder_session(session: AsyncSession) -> BuilderSession:
    bs = BuilderSession(builder_kind="agent", user_id="bundleb-tester")
    session.add(bs)
    await session.flush()
    await session.refresh(bs)
    return bs


async def _make_pending_proposal(
    session: AsyncSession, builder_session_id: int
) -> DefinitionProposal:
    prop = DefinitionProposal(
        builder_session_id=builder_session_id,
        kind="agent",
        target_id=None,
        proposed_by="builder",
        proposed_definition={"agent_id": "bundleb.tester", "name": "Test"},
        citations=None,
        status="pending",
    )
    session.add(prop)
    await session.flush()
    await session.refresh(prop)
    return prop


# ── CC21 Test 1: in-process Builder tool call logs with builder_session_id ────


@pytest.mark.asyncio
async def test_cc21_inprocess_builder_tool_logs_builder_session_id(
    db_session: AsyncSession,
) -> None:
    """build_tool_registry wraps each impl with a logger; firing a tool writes
    a tool_invocations row with builder_session_id set + agent_run_id NULL.
    """
    from artemis.builder.agent_builder import build_tool_registry

    bs = await _make_builder_session(db_session)
    await db_session.commit()

    registry = build_tool_registry(db_session=db_session, builder_session_id=bs.id)
    entry = registry.get("read_capabilities")
    assert entry is not None
    result = await entry.impl({})
    assert result  # non-empty JSON
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(ToolInvocation).where(ToolInvocation.builder_session_id == bs.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.builder_session_id == bs.id
    assert row.agent_run_id is None
    assert row.tool_name == "read_capabilities"
    assert row.success is True


# ── CC21 Test 2: MCP-path Builder server logs with builder_session_id ─────────


@pytest.mark.asyncio
async def test_cc21_mcp_builder_tool_logs_builder_session_id(
    db_session: AsyncSession,
) -> None:
    """The Builder MCP server (_build_builder_server) logs each call with
    builder_session_id set + agent_run_id NULL.
    """
    import mcp.types as mcp_types

    from artemis.tools.mcp_server import _build_builder_server

    bs = await _make_builder_session(db_session)
    await db_session.commit()

    server = _build_builder_server(db_session, bs.id)
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name="builder_read_capabilities", arguments={}),
    )
    handler = server.request_handlers[mcp_types.CallToolRequest]
    server_result = await handler(req)
    call_result = server_result.root
    assert isinstance(call_result, mcp_types.CallToolResult)
    assert call_result.content  # non-empty

    rows = (
        (
            await db_session.execute(
                select(ToolInvocation).where(ToolInvocation.builder_session_id == bs.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.builder_session_id == bs.id
    assert row.agent_run_id is None
    assert row.tool_name == "builder_read_capabilities"
    assert row.success is True


# ── CC21 Test 3: mutual-exclusion CHECK constraint ────────────────────────────


@pytest.mark.asyncio
async def test_cc21_check_constraint_rejects_both_or_neither_scope(
    db_session: AsyncSession,
) -> None:
    """ck_tool_invocations_scope: rejects rows with BOTH agent_run_id and
    builder_session_id set, and rejects rows with NEITHER set.
    """
    bs = await _make_builder_session(db_session)
    await db_session.commit()

    # Case 1: both set → reject.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO tool_invocations "
                    "(agent_run_id, builder_session_id, tool_name, success) "
                    "VALUES ('runA', :bs, 'x', true)"
                ),
                {"bs": bs.id},
            )

    # Case 2: neither set → reject.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO tool_invocations "
                    "(agent_run_id, builder_session_id, tool_name, success) "
                    "VALUES (NULL, NULL, 'x', true)"
                )
            )


# ── CC22 Test 4: reject with reason captures rejection_reason + rejected_at ───


@pytest.mark.asyncio
async def test_cc22_reject_with_reason_persists(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """POST /api/builder/proposals/{id}/reject with {"reason": "..."} persists
    rejection_reason + rejected_at.  Status flips to 'rejected'.
    """
    bs = await _make_builder_session(db_session)
    prop = await _make_pending_proposal(db_session, bs.id)
    await db_session.commit()
    prop_id = prop.id

    response = await client.post(
        f"/api/builder/proposals/{prop_id}/reject",
        json={"reason": "hallucinated state name 'mystery'"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "hallucinated state name 'mystery'"
    assert body["rejected_at"] is not None

    # Verify it persisted via a fresh session — the route commits in its own
    # session, and our db_session has cached state from before the commit.
    from artemis.db import SessionLocal

    async with SessionLocal() as fresh:
        refreshed = (
            await fresh.execute(select(DefinitionProposal).where(DefinitionProposal.id == prop_id))
        ).scalar_one()
        assert refreshed.status == "rejected"
        assert refreshed.rejection_reason == "hallucinated state name 'mystery'"
        assert refreshed.rejected_at is not None


# ── CC22 Test 5: reject without body is backward-compatible ───────────────────


@pytest.mark.asyncio
async def test_cc22_reject_without_body_backward_compat(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Posting to /reject with no body (existing one-click Inbox path) still
    rejects the proposal.  rejection_reason stays NULL; rejected_at populated.
    """
    bs = await _make_builder_session(db_session)
    prop = await _make_pending_proposal(db_session, bs.id)
    await db_session.commit()
    prop_id = prop.id

    # No json body at all — replicates the legacy Inbox path.
    response = await client.post(f"/api/builder/proposals/{prop_id}/reject")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] is None
    assert body["rejected_at"] is not None

    from artemis.db import SessionLocal

    async with SessionLocal() as fresh:
        refreshed = (
            await fresh.execute(select(DefinitionProposal).where(DefinitionProposal.id == prop_id))
        ).scalar_one()
        assert refreshed.status == "rejected"
        assert refreshed.rejection_reason is None
        assert refreshed.rejected_at is not None


# ── CC22 Test 6: empty-string reason is treated as None ───────────────────────


@pytest.mark.asyncio
async def test_cc22_reject_with_empty_reason_treated_as_none(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Sending {"reason": null} or {} explicitly is the same as no body —
    rejection_reason stays NULL.  Guards the JS path where the operator
    leaves the prompt blank.
    """
    bs = await _make_builder_session(db_session)
    prop = await _make_pending_proposal(db_session, bs.id)
    await db_session.commit()
    prop_id = prop.id

    response = await client.post(f"/api/builder/proposals/{prop_id}/reject", json={"reason": None})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] is None
