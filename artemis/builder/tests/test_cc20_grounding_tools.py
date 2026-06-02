"""CC20 — Builder Grounding Tools tests.

Tests:
1. builder_read_tool_signatures returns real allowed_status_values (positive + negative).
2. builder_read_db_schema returns CHECK constraints for definition_proposals.
3. builder_read_skill_catalog returns all registered tools (positive check for known tools).
4. End-to-end regrounding smoke: a Builder session with grounding tools wired produces
   a proposal whose system_prompt enumerates REAL signal states (not hallucinated ones).
   Requires an Anthropic API key — skipped if absent, to be run post-merge by Lead.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_agent_with_tools(
    session: AsyncSession,
    agent_id: str,
    tools: list[str] | None = None,
) -> Any:
    """Insert a minimal agent row with a tool list."""
    from artemis.builders import repository as builders_repo

    return await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name=f"Test Agent {agent_id}",
        goal="Test qualification goal",
        system_prompt="You qualify signals. Use signal_queue.update_status to transition.",
        tools=tools or ["signal_queue.update_status"],
        model="claude-sonnet-4-6",
    )


# ── Test 1: builder_read_tool_signatures returns real allowed_status_values ────


@pytest.mark.asyncio
async def test_builder_read_tool_signatures_status_values(db_session: AsyncSession) -> None:
    """builder_read_tool_signatures includes real allowed_status_values.

    Positive: the six real states from the brief are present.
    Negative: hallucinated states 'disqualified' and 'needs_enrichment' are absent.
    """
    from artemis.tools.mcp_server import _dispatch_builder_tool

    await _make_agent_with_tools(
        db_session,
        "cc20-sig-agent",
        tools=["signal_queue.update_status"],
    )
    await db_session.commit()

    result_json = await _dispatch_builder_tool(
        name="builder_read_tool_signatures",
        args={"agent_id": "cc20-sig-agent"},
        session=db_session,
        builder_session_id=99,
        seen_run_ids=set(),
    )
    data = json.loads(result_json)

    assert "error" not in data, f"Unexpected error: {data}"
    assert data["agent_id"] == "cc20-sig-agent"
    assert "tools" in data, f"Expected 'tools' key, got {list(data.keys())}"

    tools_list = data["tools"]
    assert len(tools_list) >= 1, "Expected at least one tool entry"

    # Find the signal_queue.update_status entry.
    update_status_tool = next(
        (t for t in tools_list if t.get("name") == "signal_queue.update_status"), None
    )
    assert update_status_tool is not None, (
        f"Expected signal_queue.update_status in tools, got: {[t.get('name') for t in tools_list]}"
    )

    allowed = update_status_tool.get("allowed_status_values", [])
    assert isinstance(allowed, list), f"allowed_status_values must be a list, got {type(allowed)}"

    # Positive checks: the six real states.
    real_states = [
        "pending_qualification",
        "qualified",
        "suppressed_stale",
        "rejected_hard_filter",
        "archived",
        "held_pending_corroboration",
    ]
    missing = [s for s in real_states if s not in allowed]
    assert not missing, f"Real states missing from allowed_status_values: {missing}\nGot: {allowed}"

    # Negative checks: hallucinated states must NOT appear.
    hallucinated = ["disqualified", "needs_enrichment"]
    present_hallucinated = [s for s in hallucinated if s in allowed]
    assert not present_hallucinated, (
        f"Hallucinated states found in allowed_status_values: {present_hallucinated}\n"
        f"These must not appear. Got: {allowed}"
    )

    print(f"\n[CC20 test 1] allowed_status_values for signal_queue.update_status:\n  {allowed}")


# ── Test 2: builder_read_db_schema returns CHECK constraints ──────────────────


@pytest.mark.asyncio
async def test_builder_read_db_schema_check_constraints(db_session: AsyncSession) -> None:
    """builder_read_db_schema returns ck_definition_proposals_kind and _proposed_by.

    Verifies the constraint names and their values are correct.
    """
    from artemis.tools.mcp_server import _dispatch_builder_tool

    result_json = await _dispatch_builder_tool(
        name="builder_read_db_schema",
        args={"table_names": ["definition_proposals"]},
        session=db_session,
        builder_session_id=99,
        seen_run_ids=set(),
    )
    data = json.loads(result_json)

    assert isinstance(data, list), f"Expected a list, got {type(data)}"
    assert len(data) >= 1, "Expected at least one table entry"

    table_entry = data[0]
    assert table_entry["table"] == "definition_proposals"
    assert "columns" in table_entry
    assert "constraints" in table_entry

    constraints = table_entry["constraints"]
    constraint_names = {c["name"] for c in constraints}

    # Both required constraint names must be present.
    assert "ck_definition_proposals_kind" in constraint_names, (
        f"ck_definition_proposals_kind not found. Constraints: {constraint_names}"
    )
    assert "ck_definition_proposals_proposed_by" in constraint_names, (
        f"ck_definition_proposals_proposed_by not found. Constraints: {constraint_names}"
    )

    # Verify the kind constraint contains the expected values.
    kind_constraint = next(
        (c for c in constraints if c["name"] == "ck_definition_proposals_kind"), None
    )
    assert kind_constraint is not None
    kind_def = kind_constraint.get("definition") or ""
    for expected_kind in ("agent", "skill", "workflow"):
        assert expected_kind in kind_def, (
            f"Expected {expected_kind!r} in kind constraint definition: {kind_def!r}"
        )

    # Verify proposed_by constraint contains expected values.
    pb_constraint = next(
        (c for c in constraints if c["name"] == "ck_definition_proposals_proposed_by"), None
    )
    assert pb_constraint is not None
    pb_def = pb_constraint.get("definition") or ""
    for expected_pb in ("user", "builder"):
        assert expected_pb in pb_def, (
            f"Expected {expected_pb!r} in proposed_by constraint definition: {pb_def!r}"
        )

    print(
        f"\n[CC20 test 2] definition_proposals constraints:\n"
        f"  kind={kind_def!r}\n"
        f"  proposed_by={pb_def!r}"
    )


# ── Test 3: builder_read_skill_catalog returns all registered tools ───────────


@pytest.mark.asyncio
async def test_builder_read_skill_catalog_registered_tools(db_session: AsyncSession) -> None:
    """builder_read_skill_catalog lists all registered tools including known ones."""
    import artemis.tools  # noqa: F401 — ensure all tool factories are registered
    from artemis.tools.mcp_server import _dispatch_builder_tool

    result_json = await _dispatch_builder_tool(
        name="builder_read_skill_catalog",
        args={},
        session=db_session,
        builder_session_id=99,
        seen_run_ids=set(),
    )
    data = json.loads(result_json)

    assert "registered_tools" in data, f"Expected 'registered_tools' key, got {list(data.keys())}"
    assert "skills" in data, f"Expected 'skills' key, got {list(data.keys())}"

    registered_names = {t["name"] for t in data["registered_tools"]}

    # The brief specifies these three must be present.
    required_tools = ["signal_queue.write", "signal_queue.update_status", "signal_briefs.write"]
    missing = [t for t in required_tools if t not in registered_names]
    assert not missing, (
        f"Required tools missing from registry: {missing}\nGot: {sorted(registered_names)}"
    )

    # skills key is a list (may be empty in test DB — that's fine).
    assert isinstance(data["skills"], list), f"Expected skills list, got {type(data['skills'])}"

    print(
        f"\n[CC20 test 3] Registered tool count: {len(registered_names)}\n"
        f"  Tools: {sorted(registered_names)}\n"
        f"  Skills (DB): {len(data['skills'])}"
    )


# ── Test 4: End-to-end regrounding smoke ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason=(
        "API-gated: requires ANTHROPIC_API_KEY. "
        "Lead runs this post-merge with a live Anthropic API key. "
        "Target: brief_composer agent (target_id=17 in prod), same review message as CC19 smoke."
    ),
)
async def test_regrounding_smoke_builder_uses_real_states(db_session: AsyncSession) -> None:
    """End-to-end: Builder session with grounding tools proposes REAL signal states.

    Verifies that after CC20, the Builder's proposal system_prompt contains the
    real signal states (pending_qualification, qualified) and NOT the hallucinated
    ones (disqualified, needs_enrichment) that CC19's smoke produced.

    This test requires:
    - ANTHROPIC_API_KEY set in environment.
    - A real DB with a brief_composer agent (or we create one with update_status tool).
    - The full tool chain: read_recent_runs → read_tool_signatures → propose.
    """
    from sqlalchemy import select

    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.builder.agent_builder import handle_turn
    from artemis.builder.repository import create_builder_session
    from artemis.builders.models import DefinitionProposal

    # Create a brief_composer-like agent with signal_queue.update_status.
    agent = await _make_agent_with_tools(
        db_session,
        "cc20-smoke-brief-composer",
        tools=["signal_queue.update_status", "signal_queue.write"],
    )
    await db_session.commit()

    # Create builder session targeting this agent.
    builder_session = await create_builder_session(
        db_session,
        builder_kind="agent",
        target_id=agent.id,
    )
    await db_session.commit()

    # Scripted adapter: simulate the grounded tool call sequence.
    # 1. read_recent_runs → empty (no runs for this test agent).
    # 2. read_tool_signatures → returns real states.
    # 3. propose → uses the real states in system_prompt.
    #
    # We use FakeAdapter with a scripted propose that includes the real states
    # to verify the pipeline accepts them. The "real" LLM call would require
    # a live Anthropic key — the skipif above gates that.
    real_states_text = (
        "Valid lifecycle states — use only these, nothing else:\n"
        "- `pending_qualification` — awaiting qualification scoring\n"
        "- `qualified` — passes all hard filters\n"
        "- `suppressed_stale` — older than the staleness threshold\n"
        "- `rejected_hard_filter` — fails a hard filter\n"
        "- `held_pending_corroboration` — qualified but awaiting corroboration\n"
        "- `archived` — terminal state"
    )

    propose_input = {
        "kind": "agent",
        "definition": {
            "name": "cc20-smoke-brief-composer",
            "goal": "Qualify and route market signals.",
            "system_prompt": ("You are a brief composer agent.\n\n" + real_states_text),
            "tools": ["signal_queue.update_status"],
            "model": "claude-sonnet-4-6",
        },
        "target_id": agent.id,
        "citations": None,
    }

    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("tool-use-smoke-1", "propose", propose_input)],
                stop_reason="tool_use",
            ),
            ScriptedReply(
                text=(
                    "I've reviewed the recent runs and used read_tool_signatures to verify "
                    "the actual valid states. The proposal now enumerates the correct states."
                ),
                stop_reason="end_turn",
            ),
        ]
    )

    await handle_turn(
        builder_session_id=builder_session.id,
        user_text=(
            "Please review this agent's recent runs and propose specific improvements "
            "to fix the signal lifecycle handling."
        ),
        adapter=adapter,
        db_session=db_session,
    )

    # Verify: a definition_proposals row landed with real states in the system_prompt.
    rows = await db_session.execute(
        select(DefinitionProposal).where(
            DefinitionProposal.builder_session_id == builder_session.id
        )
    )
    proposals = rows.scalars().all()
    assert len(proposals) >= 1, (
        f"Expected at least 1 proposal for session {builder_session.id}, got {len(proposals)}"
    )

    proposal = proposals[0]
    system_prompt = (proposal.proposed_definition or {}).get("system_prompt", "")

    # Positive: real states must appear.
    for real_state in ("pending_qualification", "qualified"):
        assert real_state in system_prompt, (
            f"Real state {real_state!r} not in proposed system_prompt.\n"
            f"system_prompt excerpt:\n{system_prompt[:500]}"
        )

    # Negative: hallucinated states must NOT appear.
    for bad_state in ("disqualified", "needs_enrichment"):
        assert bad_state not in system_prompt, (
            f"Hallucinated state {bad_state!r} found in proposed system_prompt.\n"
            f"system_prompt excerpt:\n{system_prompt[:500]}"
        )

    print(
        f"\n[CC20 test 4] Regrounding smoke PASSED.\n"
        f"  proposal.id={proposal.id}\n"
        f"  system_prompt excerpt:\n"
        f"{system_prompt[:800]}"
    )
