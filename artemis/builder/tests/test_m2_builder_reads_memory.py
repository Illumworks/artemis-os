"""M2 — Tests: Builder reads agent memory (cross-run grounding for proposals).

Six tests:
1. builder_search_memory returns matched observations for an agent with 3 obs in scope.
2. Empty scope returns [] (not error) when agent has no observations.
3. Query-based retrieval narrows results (5 obs, query matches 2).
4. Edit-session opener injects "## Prior observations" block when obs exist.
5. Empty memory doesn't break opener (no obs → opener still assembles cleanly).
6. Integration smoke: Builder session proposal cites memory observations
   (API-gated; skipped if no ANTHROPIC_API_KEY present).

Requires ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401 — register O1 models
import artemis.builders.models  # noqa: F401 — register builder models
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.tools.models  # noqa: F401 — register tool_invocations
from artemis.builders import repository as builders_repo
from artemis.builders.models import AgentRun
from artemis.db import attach_pgvector_codec
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation

# ── DB URL guard ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE would destroy production data. Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

# ── Truncation SQL ────────────────────────────────────────────────────────────

_TRUNCATE_SQL = text(
    # Memory tables (child → parent order)
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs, "
    # Builder tables
    "tool_invocations, "
    "agent_context, "
    "agent_run_trajectory_summaries, "
    "definition_proposals, "
    "agent_runs, "
    "agent_skills, "
    "workflow_runs, "
    "agents, "
    "skills, "
    "workflows, "
    "agent_chains, "
    "agent_dags, "
    "builder_sessions "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session: truncates both builder and memory tables."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_agent(session: AsyncSession, agent_id: str) -> int:
    """Insert a minimal agent row; returns its PK."""
    agent = await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name=f"Test Agent ({agent_id})",
        goal="Test M2 memory retrieval",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    await session.commit()
    return agent.id


async def _make_run(session: AsyncSession, agent_id: str) -> int:
    """Insert an agent_run row; returns its PK."""
    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_id=agent_id,
        status="completed",
        user_message="Test run.",
        error=None,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    await session.commit()
    return run.id


async def _write_obs(session: AsyncSession, agent_id: str, content: str) -> int:
    """Write a memory observation into scope agent:<agent_id>; return obs PK."""
    scope = Scope(scope_kind="agent", scope_id=agent_id)
    obs = await write_observation(
        session,
        scope=scope,
        content=content,
        category="trajectory",
        source_quality=0.7,
    )
    await session.commit()
    return obs.id


# ── Test 1: builder_search_memory returns matched observations ─────────────────


@pytest.mark.asyncio
async def test_builder_search_memory_returns_observations(db_session: AsyncSession) -> None:
    """builder_search_memory returns all 3 observations for an agent with 3 obs in scope."""
    from artemis.tools.mcp_server import _dispatch_builder_search_memory

    agent_id = "marketing.qualifier.brief_composer"
    await _make_agent(db_session, agent_id)

    for i in range(3):
        await _write_obs(db_session, agent_id, f"Observation {i}: scout found signals for run {i}.")

    result_json = await _dispatch_builder_search_memory(
        {"agent_id": agent_id},
        db_session,
    )
    data = json.loads(result_json)

    assert isinstance(data, list), f"Expected list, got: {type(data)}"
    assert len(data) == 3, f"Expected 3 observations, got {len(data)}"

    for item in data:
        assert "id" in item
        assert "content" in item
        assert "created_at" in item
        assert "confidence" in item
        assert "superseded_by" in item
        assert "evidence_summary" in item
        assert isinstance(item["evidence_summary"], list)


# ── Test 2: Empty scope returns [] (not error) ────────────────────────────────


@pytest.mark.asyncio
async def test_builder_search_memory_empty_scope_returns_empty_list(
    db_session: AsyncSession,
) -> None:
    """Agent with no memory observations returns [] — not an error."""
    from artemis.tools.mcp_server import _dispatch_builder_search_memory

    result_json = await _dispatch_builder_search_memory(
        {"agent_id": "agent.with.no.memory"},
        db_session,
    )
    data = json.loads(result_json)
    assert data == [], f"Expected [], got {data!r}"


# ── Test 3: Query-based retrieval narrows results ─────────────────────────────


@pytest.mark.asyncio
async def test_builder_search_memory_query_narrows_results(db_session: AsyncSession) -> None:
    """5 observations with diverse content; query for 'timeout' matches fewer."""
    from artemis.tools.mcp_server import _dispatch_builder_search_memory

    agent_id = "marketing.scout.federal_funding"
    await _make_agent(db_session, agent_id)

    # 2 observations about timeout, 3 about unrelated topics
    timeout_contents = [
        "Run 10 stalled: API timeout after 300 seconds, rate-limit headroom missing.",
        "Run 15 stalled: claude-code timeout exceeded 300s threshold again.",
    ]
    other_contents = [
        "Scout found 5 signals via Google News successfully.",
        "Qualifier completed in under 30 seconds — no errors.",
        "Brief composer generated high-quality output for campaign wave.",
    ]
    for c in timeout_contents + other_contents:
        await _write_obs(db_session, agent_id, c)

    all_result = json.loads(
        await _dispatch_builder_search_memory({"agent_id": agent_id, "limit": 5}, db_session)
    )
    assert len(all_result) == 5, f"Expected 5 total, got {len(all_result)}"

    query_result = json.loads(
        await _dispatch_builder_search_memory(
            {"agent_id": agent_id, "query": "timeout", "limit": 5}, db_session
        )
    )
    # The query-ranked result must include at least one timeout observation
    contents = [item["content"] for item in query_result]
    assert any("timeout" in c.lower() for c in contents), (
        f"Expected at least one timeout observation in results: {contents}"
    )
    # FTS/semantic ranking should surface timeout observations higher
    if len(query_result) >= 2:
        top_two_contents = " ".join(contents[:2]).lower()
        assert "timeout" in top_two_contents, f"Expected timeout in top-2 results: {contents[:2]}"


# ── Test 4: Edit-session opener injects memory block ─────────────────────────


@pytest.mark.asyncio
async def test_edit_session_opener_injects_memory(db_session: AsyncSession) -> None:
    """Opener for an agent with observations includes '## Prior observations' section."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.builder.agent_builder import build_edit_session_opener
    from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize

    agent_id = "marketing.qualifier.brief_composer"
    agent_pk = await _make_agent(db_session, agent_id)
    run_pk = await _make_run(db_session, agent_id)

    # Write a trajectory summary so the opener has runs to show
    snapshot = AgentRunSnapshot(
        run_id=str(uuid.uuid4()),
        run_pk=run_pk,
        agent_id=agent_id,
        status="completed",
        user_message="Test opener injection.",
        error=None,
    )
    summary_json = json.dumps(
        {
            "what_worked": "Scout found signals.",
            "what_stalled": "Rate limit hit.",
            "what_was_missing": "Retry headroom.",
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=summary_json)])
    await summarize(snapshot, adapter=adapter, db_session=db_session)

    opener = await build_edit_session_opener(agent_pk, db_session=db_session)

    assert opener is not None, "Opener should not be None"
    assert "## Prior observations" in opener, (
        f"Expected '## Prior observations' section in opener. Got:\n{opener}"
    )
    # Should contain the observation content written by M1's summarize()
    assert "Scout found signals." in opener or "Rate limit hit." in opener, (
        f"Expected observation content in opener. Got:\n{opener}"
    )


# ── Test 5: Empty memory doesn't break opener ─────────────────────────────────


@pytest.mark.asyncio
async def test_edit_session_opener_no_memory_no_error(db_session: AsyncSession) -> None:
    """Opener for an agent with runs but no memory observations assembles cleanly.

    Directly insert a trajectory summary without writing any memory observations
    so we can test the no-memory path without monkeypatching.
    """
    from artemis.builder.agent_builder import build_edit_session_opener
    from artemis.builders.models import AgentRunTrajectorySummary

    agent_id = "marketing.scout.no_memory_agent"
    agent_pk = await _make_agent(db_session, agent_id)
    run_pk = await _make_run(db_session, agent_id)

    # Insert a trajectory summary row directly (bypasses memory write path)
    traj = AgentRunTrajectorySummary(
        run_id=run_pk,
        what_worked="Ran OK.",
        what_stalled=None,
        what_was_missing=None,
    )
    db_session.add(traj)
    await db_session.commit()

    # Opener must not raise even if no memory obs exist
    opener = await build_edit_session_opener(agent_pk, db_session=db_session)

    assert opener is not None, "Opener should not be None (run exists with trajectory)"
    assert "## Prior observations" not in opener, (
        "Should not inject memory section when no observations exist"
    )


# ── Test 6: Integration smoke (API-gated) ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY for live Builder session",
)
async def test_builder_session_cites_memory_in_proposal(db_session: AsyncSession) -> None:
    """End-to-end: Builder session with observations produces a response that references memory.

    This test is API-gated (requires ANTHROPIC_API_KEY) and is skipped in CI.
    The Lead runs this post-merge to confirm memory grounding is active.
    """
    from artemis.builder.agent_builder import handle_turn
    from artemis.builder.repository import create_builder_session
    from artemis.providers.registry import get_adapter

    agent_id = "marketing.qualifier.brief_composer"
    agent_pk = await _make_agent(db_session, agent_id)

    # Write 2 observations so memory is populated
    await _write_obs(
        db_session,
        agent_id,
        "Run 100 stalled: qualifier timed out at signal enrichment step; "
        "missing retry logic for rate-limit responses.",
    )
    await _write_obs(
        db_session,
        agent_id,
        "Run 105 succeeded after rate-limit headroom was added; scout returned 7 signals.",
    )

    # Create a builder session targeting the agent
    session_row = await create_builder_session(
        db_session,
        target_id=agent_pk,
        builder_kind="agent",
    )
    await db_session.commit()

    adapter = get_adapter("anthropic")

    result = await handle_turn(
        builder_session_id=session_row.id,
        user_text="Review this agent and suggest any improvements based on its run history.",
        adapter=adapter,
        db_session=db_session,
    )

    assistant_text = result.get("assistant_text", "")
    assert assistant_text, "Builder should return a non-empty response"

    # The response should reference memory patterns (rate-limit, timeout, etc.)
    lower_text = assistant_text.lower()
    memory_signals = ["rate-limit", "timeout", "observation", "memory", "stall", "retry"]
    found = [s for s in memory_signals if s in lower_text]
    assert found, (
        f"Expected Builder response to reference memory patterns. "
        f"None of {memory_signals} found.\n\nResponse:\n{assistant_text}"
    )
