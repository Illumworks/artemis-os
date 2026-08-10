"""Tests for agent-builder full blueprint field authoring (agent-builder-blueprint-fields brief).

Verifies:
1. _commit_agent CREATE persists all blueprint fields with correct shapes.
2. _commit_agent UPDATE partial (goal-only) preserves existing blueprint fields (lossless).
3. urgency_tiers is stored as an OBJECT (dict), not an array.
4. The propose tool's definition schema includes blueprint field properties.
5. DB round-trip: read back from DB and verify shapes match what was written.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builder import engine
from artemis.builder.repository import create_definition_proposal
from artemis.builders.models import Agent

# ── Full blueprint fixture data ───────────────────────────────────────────────

FULL_BLUEPRINT_DEFN: dict[str, object] = {
    "name": "test-blueprint-agent",
    "agent_id": "test-blueprint-agent",
    "description": "An agent with a full blueprint for testing.",
    "goal": "Verify that all blueprint fields persist correctly.",
    "system_prompt": "You are a test agent. Do nothing.",
    "tools": ["signal_queue.read", "memory.search"],
    "model": "claude-sonnet-4-6",
    "provider": "anthropic",
    "max_iterations": 5,
    # Blueprint fields
    "persona": {
        "name": "Blueprint Bot",
        "purpose": "Ensure blueprint completeness.",
        "voice_notes": "Terse, factual.",
        "ghostwrite": False,
        "profile_image_path": None,
    },
    "output_contract": {
        "format": "json",
        "fields": ["status", "agent_id", "blueprint_complete"],
    },
    "reason_codes_emitted": ["BLUEPRINT_OK", "BLUEPRINT_PARTIAL"],
    "cadence_seconds": 3600,
    "lifecycle_status": "active",
    "urgency_tiers": {
        "hot": "Score >80 — run within 5 min",
        "standard": "Score 40-80 — run within 1 hr",
        "low": "Score <40 — run in next daily batch",
    },
    "failure_modes": [
        {"name": "stale_data", "description": "Source data not refreshed in >24 hrs."},
        {"name": "no_signals", "description": "Empty signal queue causes no-op run."},
    ],
    "inputs_required": [
        {"key": "district_id", "kind": "string", "description": "NCES district ID."},
        {"key": "campaign_id", "kind": "integer", "description": "Target campaign PK."},
    ],
    "db_tables_touched": ["signal_queue", "agents", "campaign_candidates"],
    "implementation_notes": "Depends on district_data_meta freshness.",
}


@pytest.mark.asyncio
async def test_commit_agent_create_full_blueprint(db_session: AsyncSession) -> None:
    """CREATE: all blueprint fields are persisted with correct shapes."""
    # Stage a proposal
    proposal = await create_definition_proposal(
        db_session,
        builder_session_id=None,
        kind="agent",
        target_id=None,
        proposed_by="builder",
        proposed_definition=FULL_BLUEPRINT_DEFN,
        citations=None,
    )
    await db_session.commit()

    # Commit via the engine (approve + write Agent row)
    from artemis.builder.repository import approve_proposal

    await approve_proposal(db_session, proposal.id)
    result = await engine._commit_agent(FULL_BLUEPRINT_DEFN, None, db_session, proposal.id)
    await db_session.commit()

    assert result["status"] == "created"
    agent_db_id = result["id"]

    # Read the row back from DB
    row_result = await db_session.execute(sa_select(Agent).where(Agent.id == agent_db_id).limit(1))
    agent = row_result.scalar_one()

    # Core fields
    assert agent.name == "test-blueprint-agent"
    assert agent.goal == "Verify that all blueprint fields persist correctly."
    assert agent.max_iterations == 5
    assert agent.model == "claude-sonnet-4-6"
    assert agent.provider == "anthropic"

    # persona — object shape
    assert isinstance(agent.persona, dict)
    assert agent.persona["name"] == "Blueprint Bot"
    assert agent.persona["ghostwrite"] is False

    # output_contract — object shape
    assert isinstance(agent.output_contract, dict)
    assert agent.output_contract["format"] == "json"
    assert "blueprint_complete" in agent.output_contract["fields"]

    # reason_codes_emitted — array of strings
    assert isinstance(agent.reason_codes_emitted, list)
    assert "BLUEPRINT_OK" in agent.reason_codes_emitted
    assert "BLUEPRINT_PARTIAL" in agent.reason_codes_emitted

    # cadence_seconds — integer
    assert agent.cadence_seconds == 3600

    # lifecycle_status — string
    assert agent.lifecycle_status == "active"

    # urgency_tiers — MUST be a dict (object), not a list
    assert isinstance(agent.urgency_tiers, dict), (
        f"urgency_tiers must be an object/dict, got {type(agent.urgency_tiers)}"
    )
    assert agent.urgency_tiers["hot"] == "Score >80 — run within 5 min"
    assert agent.urgency_tiers["standard"] == "Score 40-80 — run within 1 hr"
    assert agent.urgency_tiers["low"] == "Score <40 — run in next daily batch"

    # failure_modes — array of {name, description}
    assert isinstance(agent.failure_modes, list)
    assert len(agent.failure_modes) == 2
    fm_names = {fm["name"] for fm in agent.failure_modes}
    assert "stale_data" in fm_names
    assert "no_signals" in fm_names

    # inputs_required — array of {key, kind, description}
    assert isinstance(agent.inputs_required, list)
    assert len(agent.inputs_required) == 2
    ir_keys = {ir["key"] for ir in agent.inputs_required}
    assert "district_id" in ir_keys
    assert "campaign_id" in ir_keys

    # db_tables_touched — array of strings
    assert isinstance(agent.db_tables_touched, list)
    assert "signal_queue" in agent.db_tables_touched
    assert "agents" in agent.db_tables_touched

    # implementation_notes — string
    assert agent.implementation_notes == "Depends on district_data_meta freshness."


@pytest.mark.asyncio
async def test_commit_agent_update_partial_is_lossless(db_session: AsyncSession) -> None:
    """UPDATE with only goal present must NOT wipe existing blueprint fields."""
    # First create the agent with full blueprint
    proposal = await create_definition_proposal(
        db_session,
        builder_session_id=None,
        kind="agent",
        target_id=None,
        proposed_by="builder",
        proposed_definition=FULL_BLUEPRINT_DEFN,
        citations=None,
    )
    await db_session.commit()

    from artemis.builder.repository import approve_proposal

    await approve_proposal(db_session, proposal.id)
    create_result = await engine._commit_agent(FULL_BLUEPRINT_DEFN, None, db_session, proposal.id)
    await db_session.commit()
    agent_db_id = create_result["id"]

    # Now do a PARTIAL update — only goal changes, NO blueprint keys in the proposal
    partial_defn = {"goal": "Updated goal — only this changes."}
    update_proposal = await create_definition_proposal(
        db_session,
        builder_session_id=None,
        kind="agent",
        target_id=agent_db_id,
        proposed_by="builder",
        proposed_definition=partial_defn,
        citations=None,
    )
    await db_session.commit()

    await approve_proposal(db_session, update_proposal.id)
    update_result = await engine._commit_agent(
        partial_defn, agent_db_id, db_session, update_proposal.id
    )
    await db_session.commit()

    assert update_result["status"] == "updated"

    # Expire the session cache so we get a fresh DB read
    db_session.expire_all()
    row_result = await db_session.execute(sa_select(Agent).where(Agent.id == agent_db_id).limit(1))
    agent = row_result.scalar_one()

    # Goal was updated
    assert agent.goal == "Updated goal — only this changes."

    # All blueprint fields are PRESERVED (not wiped)
    assert isinstance(agent.urgency_tiers, dict), (
        "urgency_tiers was wiped on partial update — lossless invariant violated"
    )
    assert agent.urgency_tiers["hot"] == "Score >80 — run within 5 min"

    assert isinstance(agent.failure_modes, list)
    assert len(agent.failure_modes) == 2

    assert isinstance(agent.inputs_required, list)
    assert len(agent.inputs_required) == 2

    assert isinstance(agent.db_tables_touched, list)
    assert "signal_queue" in agent.db_tables_touched

    assert agent.implementation_notes == "Depends on district_data_meta freshness."
    assert agent.cadence_seconds == 3600
    assert agent.lifecycle_status == "active"

    # persona still intact
    assert isinstance(agent.persona, dict)
    assert agent.persona["name"] == "Blueprint Bot"

    # output_contract still intact
    assert isinstance(agent.output_contract, dict)
    assert agent.output_contract["format"] == "json"

    # reason_codes_emitted still intact
    assert isinstance(agent.reason_codes_emitted, list)
    assert "BLUEPRINT_OK" in agent.reason_codes_emitted

    # Other core fields not present in partial_defn are preserved too
    assert agent.name == "test-blueprint-agent"
    assert agent.max_iterations == 5


def test_propose_tool_schema_includes_blueprint_fields() -> None:
    """The propose tool's definition schema must document all blueprint fields."""
    from artemis.builder.agent_builder import AGENT_BUILDER_TOOL_SPECS

    propose_spec = next(s for s in AGENT_BUILDER_TOOL_SPECS if s["name"] == "propose")
    defn_schema = propose_spec["input_schema"]["properties"]["definition"]

    # Must have a properties dict with blueprint fields
    props = defn_schema.get("properties", {})
    for expected_key in (
        "persona",
        "output_contract",
        "reason_codes_emitted",
        "cadence_seconds",
        "lifecycle_status",
        "urgency_tiers",
        "failure_modes",
        "inputs_required",
        "db_tables_touched",
        "implementation_notes",
        "max_iterations",
    ):
        assert expected_key in props, (
            f"propose tool schema missing blueprint field: {expected_key!r}"
        )

    # urgency_tiers must be declared as an object (not array)
    ut = props["urgency_tiers"]
    assert ut.get("type") == "object", (
        f"urgency_tiers must be schema type 'object', got {ut.get('type')!r}"
    )

    # reason_codes_emitted must be array
    rce = props["reason_codes_emitted"]
    assert rce.get("type") == "array"

    # failure_modes must be array
    fm = props["failure_modes"]
    assert fm.get("type") == "array"

    # inputs_required must be array
    ir = props["inputs_required"]
    assert ir.get("type") == "array"


def test_system_prompt_includes_urgency_tiers_object_example() -> None:
    """The system prompt must document urgency_tiers as an OBJECT with a concrete example."""
    from artemis.builder.agent_builder import AGENT_BUILDER_SYSTEM_PROMPT

    prompt = AGENT_BUILDER_SYSTEM_PROMPT

    # Must mention urgency_tiers
    assert "urgency_tiers" in prompt

    # Must document it as an OBJECT (not an array)
    assert "OBJECT" in prompt or "object" in prompt

    # Must include a concrete example with tier names
    assert '"hot"' in prompt or "'hot'" in prompt
    assert '"standard"' in prompt or "'standard'" in prompt

    # Must document all major blueprint fields
    for field in (
        "persona",
        "output_contract",
        "reason_codes_emitted",
        "cadence_seconds",
        "lifecycle_status",
        "failure_modes",
        "inputs_required",
        "db_tables_touched",
        "implementation_notes",
    ):
        assert field in prompt, f"System prompt missing documentation for blueprint field: {field}"
