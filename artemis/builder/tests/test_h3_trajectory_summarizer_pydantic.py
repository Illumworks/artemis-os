"""H3 — Tests: Pydantic validation + Builder provenance for trajectory summaries.

Seven tests:
1. Valid summary passes Pydantic (using fixture data from the DB).
2. Oversized field (>2000 chars) is rejected by Pydantic → null fallback row.
3. Extra fields are rejected by extra="forbid" → null fallback row.
4. Validation failure triggers one retry; valid second reply lands.
5. Persistent validation failure produces null row + warning log.
6. read_recent_runs returns a provenance block on each trajectory.
7. build_edit_session_opener contains the "LLM-generated inferences" framing.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.trajectory_schemas import TrajectorySummary
from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize
from artemis.builders import repository as builders_repo
from artemis.builders.models import AgentRun, AgentRunTrajectorySummary

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_run(session: AsyncSession, agent_id: str = "h3-test-agent") -> tuple[str, int]:
    """Insert minimal agent + agent_run rows. Returns (run_id str, run.id int)."""
    await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name=f"H3 Test Agent ({agent_id})",
        goal="Test H3 Pydantic validation",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    run_uuid = str(uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id=agent_id,
        status="completed",
        user_message="Run the marketing pipeline.",
        error=None,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    await session.commit()
    return run_uuid, run.id


def _valid_json() -> str:
    return json.dumps(
        {
            "what_worked": "Search tool returned results.",
            "what_stalled": "No stalls observed.",
            "what_was_missing": "Nothing missing.",
        }
    )


# ── Test 1: Valid summary passes Pydantic ─────────────────────────────────────


def test_valid_summary_parses() -> None:
    """A well-formed JSON string from the LLM parses cleanly under TrajectorySummary."""
    raw = json.dumps(
        {
            "what_worked": "Scout found 12 signals.",
            "what_stalled": None,
            "what_was_missing": "Timeout config.",
        }
    )
    summary = TrajectorySummary.model_validate_json(raw)
    assert summary.what_worked == "Scout found 12 signals."
    assert summary.what_stalled is None
    assert summary.what_was_missing == "Timeout config."
    assert summary.confidence == "medium"  # default


# ── Test 2: Oversized field rejected → null fallback ─────────────────────────


@pytest.mark.asyncio
async def test_oversized_field_produces_null_row(db_session: AsyncSession) -> None:
    """A 5000-char what_worked field fails Pydantic; the summary row is still written
    (with all-null fields for audit trail) and a warning is logged.
    """
    run_uuid, run_pk = await _make_run(db_session, "h3-oversized")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="h3-oversized",
        status="completed",
        user_message="test",
        error=None,
    )

    oversized = json.dumps(
        {"what_worked": "x" * 5000, "what_stalled": None, "what_was_missing": None}
    )
    # Retry reply is also invalid (same oversized string) → persistent failure path.
    adapter = FakeAdapter([ScriptedReply(text=oversized), ScriptedReply(text=oversized)])

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    row = result.scalar_one_or_none()
    assert row is not None, "No row written after oversized-field failure"
    assert row.what_worked is None, "what_worked should be null on oversized failure"
    assert row.what_stalled is None
    assert row.what_was_missing is None


# ── Test 3: Extra fields rejected by extra="forbid" ──────────────────────────


def test_extra_fields_rejected() -> None:
    """TrajectorySummary rejects JSON with unknown keys."""
    from pydantic import ValidationError as PydanticValidationError

    raw = json.dumps(
        {
            "what_worked": "Agent ran successfully.",
            "what_stalled": None,
            "what_was_missing": None,
            "hallucinated_field": "invented value",
        }
    )
    with pytest.raises(PydanticValidationError, match="Extra inputs are not permitted"):
        TrajectorySummary.model_validate_json(raw)


# ── Test 4: Validation failure triggers retry ─────────────────────────────────


@pytest.mark.asyncio
async def test_retry_on_validation_failure(db_session: AsyncSession) -> None:
    """First LLM call returns invalid JSON; second returns valid JSON.
    The written row must contain the valid second-call content.
    """
    run_uuid, run_pk = await _make_run(db_session, "h3-retry")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="h3-retry",
        status="completed",
        user_message="test retry path",
        error=None,
    )

    invalid = json.dumps(
        {"what_worked": "x" * 5000, "what_stalled": None, "what_was_missing": None}
    )
    valid = json.dumps(
        {
            "what_worked": "Tool executed successfully on retry.",
            "what_stalled": None,
            "what_was_missing": None,
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=invalid), ScriptedReply(text=valid)])

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.what_worked == "Tool executed successfully on retry."
    # Two calls were consumed — first invalid, second valid.
    assert len(adapter.requests) == 2, (
        f"Expected 2 LLM calls (1 initial + 1 retry), got {len(adapter.requests)}"
    )


# ── Test 5: Persistent failure → null row + warning ──────────────────────────


@pytest.mark.asyncio
async def test_persistent_failure_null_row(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """When both initial and retry calls return invalid JSON, a null row is written
    and a warning is logged. The agent run itself is NOT broken.
    """
    run_uuid, run_pk = await _make_run(db_session, "h3-persistent-fail")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="h3-persistent-fail",
        status="completed",
        user_message="test persistent failure",
        error=None,
    )

    bad_json = json.dumps(
        {"what_worked": "x" * 5000, "what_stalled": None, "what_was_missing": None}
    )
    adapter = FakeAdapter([ScriptedReply(text=bad_json), ScriptedReply(text=bad_json)])

    with caplog.at_level(logging.WARNING, logger="artemis.builder.trajectory_summarizer"):
        await summarize(snapshot, adapter=adapter, db_session=db_session)

    result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    row = result.scalar_one_or_none()
    assert row is not None, "No audit row written after persistent failure"
    assert row.what_worked is None
    assert row.what_stalled is None
    assert row.what_was_missing is None

    warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("persistent validation failure" in m for m in warning_msgs), (
        f"Expected 'persistent validation failure' warning. Got: {warning_msgs}"
    )


# ── Test 6: read_recent_runs returns provenance block ─────────────────────────


@pytest.mark.asyncio
async def test_read_recent_runs_provenance(db_session: AsyncSession) -> None:
    """Each trajectory in read_recent_runs output contains a provenance block."""
    from artemis.builder import engine

    run_uuid, run_pk = await _make_run(db_session, "h3-provenance-agent")

    # Write a trajectory summary directly.
    from artemis.builder.repository import create_trajectory_summary

    await create_trajectory_summary(
        db_session,
        run_id=run_pk,
        what_worked="Something worked.",
        what_stalled=None,
        what_was_missing=None,
    )
    await db_session.commit()

    runs = await engine.read_recent_runs("h3-provenance-agent", db_session=db_session)
    runs_with_traj = [r for r in runs if "trajectory" in r]
    assert runs_with_traj, "No run with trajectory returned"

    traj = runs_with_traj[0]["trajectory"]
    assert "provenance" in traj, f"No provenance key in trajectory: {traj}"

    prov = traj["provenance"]
    assert prov["source"] == "llm_trajectory_summarizer"
    assert "confidence" in prov
    assert "generated_at" in prov
    assert "model" in prov


# ── Test 7: build_edit_session_opener contains "LLM-generated inferences" ────


@pytest.mark.asyncio
async def test_edit_session_opener_llm_framing(db_session: AsyncSession) -> None:
    """build_edit_session_opener must contain the LLM-generated-inferences framing
    when summaries are present.
    """
    from artemis.builder.agent_builder import build_edit_session_opener
    from artemis.builder.repository import create_trajectory_summary
    from artemis.builders.models import Agent as AgentModel

    # Create an agent and get its PK.
    await builders_repo.create_agent(
        db_session,
        agent_id="h3-builder-framing-agent",
        name="H3 Builder Framing Agent",
        goal="Test opener framing",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    await db_session.flush()

    result = await db_session.execute(
        select(AgentModel).where(AgentModel.agent_id == "h3-builder-framing-agent").limit(1)
    )
    agent = result.scalar_one()

    run_uuid = str(uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id="h3-builder-framing-agent",
        status="completed",
        user_message="test",
        error=None,
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.refresh(run)

    await create_trajectory_summary(
        db_session,
        run_id=run.id,
        what_worked="The grounding tool returned schema data.",
        what_stalled="Qualifier loop stalled on missing status.",
        what_was_missing=None,
    )
    await db_session.commit()

    opener = await build_edit_session_opener(agent.id, db_session=db_session)

    assert opener is not None, "build_edit_session_opener returned None with summaries present"
    assert "LLM-generated trajectory summaries" in opener, (
        f"Missing LLM-generated framing in opener:\n{opener}"
    )
    assert "treat as inferences, not facts" in opener, (
        f"Missing 'treat as inferences, not facts' in opener:\n{opener}"
    )
    assert "verify" in opener.lower(), f"Missing verification directive in opener:\n{opener}"
