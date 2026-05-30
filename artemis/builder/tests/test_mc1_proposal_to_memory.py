"""MC1 — Tests: definition-proposal approval → memory observation write path.

Seven tests:
1. Approval of kind='agent' proposal writes 2 observations (multi-scope).
2. Approval of kind='skill' proposal writes 2 observations.
3. Failure isolation — monkeypatch write path to raise; approval still flips
   status, response succeeds, warning is logged, no partial memory state.
4. Idempotency — approve same proposal twice (2nd is no-op); memory not doubled.
5. Empty citations — proposal with no run_ids; observation lands with proposal
   evidence only (no agent_run links).
6. Content shape — observation content matches Part C format.
7. Source kinds — evidence rows use 'definition_proposal' and 'agent_run' correctly.

Requires ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_os_test.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401 — register O1 models
import artemis.builders.models  # noqa: F401 — register builder models
import artemis.db as _db
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.tools.models  # noqa: F401
from artemis.builders.models import Agent, DefinitionProposal, Skill
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryEvidence, MemoryObservation

# ── DB URL guard ─────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

# ── Truncation SQL ─────────────────────────────────────────────────────────────

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs, "
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
    """Per-test session; patches artemis.db.SessionLocal so carryover opens its session on the
    same test engine.

    The explicit rollback at the end of the yield (per M5 pattern) releases any pending locks
    from the embedding SAVEPOINT before the next test's TRUNCATE runs, preventing deadlocks.
    """
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    original_engine = _db.engine
    original_session_local = _db.SessionLocal
    _db.engine = engine
    _db.SessionLocal = __import__(
        "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
    ).async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
            # Release any pending autobegin locks before the next test's TRUNCATE.
            if session.in_transaction():
                await session.rollback()
    finally:
        _db.engine = original_engine
        _db.SessionLocal = original_session_local
        await engine.dispose()


# ── Seed helpers ─────────────────────────────────────────────────────────────


async def _seed_agent(
    session: AsyncSession,
    agent_id: str = "marketing.qualifier.brief_composer",
) -> int:
    agent = Agent(
        agent_id=agent_id,
        name="Brief Composer",
        goal="Compose campaign briefs.",
        system_prompt="You are a brief composer.",
        tools=[],
        model="claude-sonnet-4-6",
        provider="anthropic",
        max_iterations=10,
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    await session.commit()
    return agent.id


async def _seed_skill(
    session: AsyncSession,
    slug: str = "brief-composer-skill",
) -> int:
    skill = Skill(
        slug=slug,
        name="Brief Composer Skill",
        description="Compose briefs.",
        status="proposed",
        kind="user",
        tools=[],
    )
    session.add(skill)
    await session.flush()
    await session.refresh(skill)
    await session.commit()
    return skill.id


async def _seed_proposal(
    session: AsyncSession,
    *,
    kind: str = "agent",
    target_id: int | None = None,
    proposed_by: str = "builder",
    proposed_definition: dict[str, Any] | None = None,
    citations: dict[str, Any] | None = None,
) -> int:
    proposal = DefinitionProposal(
        kind=kind,
        target_id=target_id,
        proposed_by=proposed_by,
        proposed_definition=proposed_definition
        or {"goal": "Compose campaign briefs.", "system_prompt": "You are a brief composer."},
        citations=citations,
        status="pending",
    )
    session.add(proposal)
    await session.flush()
    await session.refresh(proposal)
    await session.commit()
    return proposal.id


# ── Test 1: Multi-scope write (agent) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_approval_writes_two_observations(db_session: AsyncSession) -> None:
    """Approving kind='agent' proposal writes 2 observations: agent scope + workspace:platform."""
    from artemis.builder.memory_carryover import write_proposal_approval_observation

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
        citations={"run_ids": [10, 20]},
    )

    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind="agent",
        target_id=agent_db_id,
        target_slug="marketing.qualifier.brief_composer",
        proposed_definition={"goal": "Compose campaign briefs."},
        proposed_by="builder",
        citations={"run_ids": [10, 20]},
    )

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 2, f"Expected 2 observations, got {len(obs_rows)}"

    scopes = {(o.scope_kind, o.scope_id) for o in obs_rows}
    assert ("agent", "marketing.qualifier.brief_composer") in scopes, (
        f"Missing agent scope: {scopes}"
    )
    assert ("workspace", "platform") in scopes, f"Missing workspace:platform: {scopes}"

    # (d) confidence=1.0 on both
    for obs in obs_rows:
        assert obs.confidence == 1.0, f"Expected confidence=1.0, got {obs.confidence}"

    # (c) evidence rows: 1 proposal + 2 runs = 3 per observation, 6 total
    all_evidence = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(all_evidence) == 6, (
        f"Expected 6 evidence rows (3 per obs × 2), got {len(all_evidence)}"
    )


# ── Test 2: Multi-scope write (skill) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_approval_writes_two_observations(db_session: AsyncSession) -> None:
    """Approving kind='skill' proposal writes 2 observations: skill scope + workspace:platform."""
    from artemis.builder.memory_carryover import write_proposal_approval_observation

    skill_db_id = await _seed_skill(db_session, "my-skill")
    proposal_id = await _seed_proposal(
        db_session,
        kind="skill",
        target_id=skill_db_id,
        citations={"run_ids": [5]},
    )

    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind="skill",
        target_id=skill_db_id,
        target_slug="my-skill",
        proposed_definition={"goal": "Some skill goal."},
        proposed_by="builder",
        citations={"run_ids": [5]},
    )

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 2, f"Expected 2 observations, got {len(obs_rows)}"

    scopes = {(o.scope_kind, o.scope_id) for o in obs_rows}
    assert ("skill", "my-skill") in scopes
    assert ("workspace", "platform") in scopes


# ── Test 3: Failure isolation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Memory write failure must not propagate; proposal status flips; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.engine import commit
    from artemis.builder.repository import get_definition_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
    )

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated memory failure")

    monkeypatch.setattr(_carryover, "_write_one_observation", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        # commit() flips the status; carryover is called after
        await commit(proposal_id, db_session=db_session)
        await db_session.commit()

        # Now call carryover explicitly (as the route does) — must not raise
        from artemis.builder.memory_carryover import write_proposal_approval_observation

        await write_proposal_approval_observation(
            proposal_id=proposal_id,
            kind="agent",
            target_id=agent_db_id,
            target_slug="marketing.qualifier.brief_composer",
            proposed_definition={"goal": "Test."},
            proposed_by="builder",
            citations=None,
        )

    # (a) status is approved (commit flipped it)
    proposal = await get_definition_proposal(db_session, proposal_id)
    assert proposal.status == "approved"

    # (b) no partial memory state
    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 0, f"Expected 0 obs after failure, got {len(obs_rows)}"

    # (c) warning logged
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC1 memory observation write failed" in m for m in warnings), (
        f"Expected MC1 warning. Got: {warnings}"
    )


# ── Test 4: Idempotency ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency_no_duplicate_observations(db_session: AsyncSession) -> None:
    """Approving the same proposal twice (2nd is already_approved no-op) yields 2 obs, not 4."""
    from artemis.builder.engine import commit
    from artemis.builder.memory_carryover import write_proposal_approval_observation

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
    )

    # First approval
    await commit(proposal_id, db_session=db_session)
    await db_session.commit()
    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind="agent",
        target_id=agent_db_id,
        target_slug="marketing.qualifier.brief_composer",
        proposed_definition={"goal": "Compose campaign briefs."},
        proposed_by="builder",
        citations=None,
    )

    obs_after_first = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_after_first) == 2

    # Second approval attempt — engine returns already_approved; route skips memory write
    result2 = await commit(proposal_id, db_session=db_session)
    assert result2.get("status") == "already_approved"
    # Simulate route: skip write on already_approved
    if result2.get("status") != "already_approved":
        await write_proposal_approval_observation(
            proposal_id=proposal_id,
            kind="agent",
            target_id=agent_db_id,
            target_slug="marketing.qualifier.brief_composer",
            proposed_definition={"goal": "Compose campaign briefs."},
            proposed_by="builder",
            citations=None,
        )

    obs_after_second = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_after_second) == 2, (
        f"Expected 2 observations (idempotent), got {len(obs_after_second)}"
    )


# ── Test 5: Empty citations ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_citations_observation_with_proposal_evidence_only(
    db_session: AsyncSession,
) -> None:
    """Proposal with no run_ids: each observation gets exactly 1 evidence row (proposal only)."""
    from artemis.builder.memory_carryover import write_proposal_approval_observation

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
        citations=None,
    )

    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind="agent",
        target_id=agent_db_id,
        target_slug="marketing.qualifier.brief_composer",
        proposed_definition={"goal": "Compose campaign briefs."},
        proposed_by="builder",
        citations=None,
    )

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 2

    all_evidence = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    # 1 proposal evidence × 2 observations = 2 total
    assert len(all_evidence) == 2, (
        f"Expected 2 evidence rows (no run links), got {len(all_evidence)}"
    )
    for ev in all_evidence:
        assert ev.source_kind == "definition_proposal", (
            f"Expected only definition_proposal evidence, got {ev.source_kind}"
        )


# ── Test 6: Content shape ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_content_shape_matches_part_c_format(db_session: AsyncSession) -> None:
    """Observation content matches the Part C template."""
    from artemis.builder.memory_carryover import write_proposal_approval_observation

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
        citations={"run_ids": [42, 99]},
    )

    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind="agent",
        target_id=agent_db_id,
        target_slug="marketing.qualifier.brief_composer",
        proposed_definition={"goal": "Compose targeted campaign briefs."},
        proposed_by="builder",
        citations={"run_ids": [42, 99]},
    )

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 2
    content = obs_rows[0].content

    assert f"#{proposal_id}" in content, f"Missing proposal id in: {content}"
    assert "agent" in content, f"Missing 'agent' kind in: {content}"
    assert "marketing.qualifier.brief_composer" in content, f"Missing target_slug in: {content}"
    assert "42, 99" in content or "42" in content, f"Missing run ids in: {content}"
    assert "builder" in content, f"Missing proposed_by in: {content}"
    assert "Operator approved" in content, f"Missing 'Operator approved' in: {content}"
    assert "Compose targeted campaign briefs." in content, f"Missing summary in: {content}"


# ── Test 7: Source kinds ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_kinds_are_correct(db_session: AsyncSession) -> None:
    """Evidence rows use 'definition_proposal' and 'agent_run' source_kinds correctly."""
    from artemis.builder.memory_carryover import write_proposal_approval_observation

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
        citations={"run_ids": [7]},
    )

    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind="agent",
        target_id=agent_db_id,
        target_slug="marketing.qualifier.brief_composer",
        proposed_definition={"goal": "Test goal."},
        proposed_by="builder",
        citations={"run_ids": [7]},
    )

    all_evidence = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    # 2 scopes × (1 proposal + 1 run) = 4 total evidence rows
    assert len(all_evidence) == 4, f"Expected 4 evidence rows, got {len(all_evidence)}"

    source_kinds = {e.source_kind for e in all_evidence}
    assert "definition_proposal" in source_kinds, (
        f"Missing 'definition_proposal' source_kind, got {source_kinds}"
    )
    assert "agent_run" in source_kinds, f"Missing 'agent_run' source_kind, got {source_kinds}"

    proposal_links = [e for e in all_evidence if e.source_kind == "definition_proposal"]
    run_links = [e for e in all_evidence if e.source_kind == "agent_run"]
    assert len(proposal_links) == 2, (
        f"Expected 2 proposal evidence (1 per scope), got {len(proposal_links)}"
    )
    assert len(run_links) == 2, f"Expected 2 run evidence (1 per scope), got {len(run_links)}"

    for link in proposal_links:
        assert link.source_id == proposal_id, f"Wrong proposal source_id: {link.source_id}"
    for link in run_links:
        assert link.source_id == 7, f"Wrong run source_id: {link.source_id}"
