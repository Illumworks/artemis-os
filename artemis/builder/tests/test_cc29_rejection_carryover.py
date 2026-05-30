"""CC29 — Tests: definition-proposal rejection → memory observation write path.

Mirrors MC1 (approval carryover), but for the reject path:
1. Reject with reason — writes ONE observation + 2 scope-join rows (agent:<slug>
   primary + workspace:platform audit) + evidence rows for proposal + cited runs.
2. Reject WITHOUT reason — content reads 'Reason: (none captured)'.
3. Reject of kind='skill' — primary scope is skill:<slug>.
4. Reject of proposal with no resolvable target_slug — falls back to
   workspace:platform as the only scope (no agent/skill row).
5. Idempotency — re-rejecting raises ValueError ('not pending') from the
   repository; exactly one observation lands in memory.
6. Failure isolation — monkeypatch _multi_scope_observation_write to raise;
   reject_proposal still flips status and returns; warning is logged.
7. Empty citations handled — observation lands with 'Citations: runs (none)'.
8. Content shape matches the CC29 format spec.

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
from artemis.memory.models import MemoryEvidence, MemoryObservation, MemoryObservationScope

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
    "memory_embeddings, memory_evidence, memory_observation_scopes, memory_observations, "
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
    same test engine.  Same shape as test_mc1_proposal_to_memory.py.
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
            if session.in_transaction():
                await session.rollback()
    finally:
        _db.engine = original_engine
        _db.SessionLocal = original_session_local
        await engine.dispose()


# ── Seed helpers ─────────────────────────────────────────────────────────────


async def _seed_agent(
    session: AsyncSession, agent_id: str = "marketing.qualifier.brief_composer"
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


async def _seed_skill(session: AsyncSession, slug: str = "brief-composer-skill") -> int:
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


async def _seed_pending_proposal(
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


# ── Test 1: Reject with reason writes observation + evidence ─────────────────


@pytest.mark.asyncio
async def test_reject_with_reason_writes_observation(db_session: AsyncSession) -> None:
    """Rejecting kind='agent' with a reason: status flips, rejection_reason set,
    observation lands in agent:<slug> + workspace:platform, evidence links to
    proposal + each cited run.
    """
    from artemis.builder.repository import reject_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_pending_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
        citations={"run_ids": [10, 20]},
    )

    row = await reject_proposal(db_session, proposal_id, rejection_reason="hallucinated state name")
    await db_session.commit()

    # (a) status flip + rejection_reason persisted
    assert row.status == "rejected"
    assert row.rejection_reason == "hallucinated state name"
    assert row.rejected_at is not None

    # (b) ONE observation row in agent:<slug> primary
    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1, f"Expected 1 observation, got {len(obs_rows)}"
    obs = obs_rows[0]
    assert obs.scope_kind == "agent"
    assert obs.scope_id == "marketing.qualifier.brief_composer"
    assert obs.category == "definition_rejection"
    assert obs.confidence_origin == "mc_definition_rejection"
    assert obs.wing == "durable"

    # Two scope-join rows: agent:<slug> primary + workspace:platform audit
    scope_rows = (await db_session.execute(select(MemoryObservationScope))).scalars().all()
    assert len(scope_rows) == 2, f"Expected 2 scope rows, got {len(scope_rows)}"
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("agent", "marketing.qualifier.brief_composer", True) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs

    # (c) evidence: 1 proposal + 2 runs = 3 rows
    evidence = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(evidence) == 3, f"Expected 3 evidence rows, got {len(evidence)}"
    kinds = {(e.source_kind, e.source_id) for e in evidence}
    assert ("definition_proposal", str(proposal_id)) in kinds
    assert ("agent_run", "10") in kinds
    assert ("agent_run", "20") in kinds


# ── Test 2: Reject without reason — '(none captured)' ────────────────────────


@pytest.mark.asyncio
async def test_reject_without_reason_still_writes_observation(
    db_session: AsyncSession,
) -> None:
    """Reject with no reason: observation content reads 'Reason: (none captured)'."""
    from artemis.builder.repository import reject_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_pending_proposal(
        db_session, kind="agent", target_id=agent_db_id, citations=None
    )

    await reject_proposal(db_session, proposal_id, rejection_reason=None)
    await db_session.commit()

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1
    assert "Reason: (none captured)" in obs_rows[0].content


# ── Test 3: kind=skill primary scope ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reject_skill_primary_scope_is_skill_slug(db_session: AsyncSession) -> None:
    """Rejecting a kind='skill' proposal lands in skill:<slug> primary scope."""
    from artemis.builder.repository import reject_proposal

    skill_db_id = await _seed_skill(db_session, "my-skill")
    proposal_id = await _seed_pending_proposal(
        db_session, kind="skill", target_id=skill_db_id, citations={"run_ids": [5]}
    )

    await reject_proposal(db_session, proposal_id, rejection_reason="duplicates approved skill")
    await db_session.commit()

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1
    assert obs_rows[0].scope_kind == "skill"
    assert obs_rows[0].scope_id == "my-skill"

    scope_pairs = {
        (r.scope_kind, r.scope_id)
        for r in (await db_session.execute(select(MemoryObservationScope))).scalars().all()
    }
    assert ("skill", "my-skill") in scope_pairs
    assert ("workspace", "platform") in scope_pairs


# ── Test 4: No target_slug → workspace:platform only ─────────────────────────


@pytest.mark.asyncio
async def test_reject_with_no_target_slug_falls_back_to_platform(
    db_session: AsyncSession,
) -> None:
    """Proposal with target_id=None (new agent draft, not a revision) falls back
    to workspace:platform as the only scope.
    """
    from artemis.builder.repository import reject_proposal

    proposal_id = await _seed_pending_proposal(
        db_session, kind="agent", target_id=None, citations=None
    )

    await reject_proposal(db_session, proposal_id, rejection_reason="orphaned draft")
    await db_session.commit()

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1
    obs = obs_rows[0]
    assert obs.scope_kind == "workspace"
    assert obs.scope_id == "platform"

    # Only one scope row (no secondary) because primary IS workspace:platform.
    scope_rows = (await db_session.execute(select(MemoryObservationScope))).scalars().all()
    assert len(scope_rows) == 1, f"Expected 1 scope row, got {len(scope_rows)}"
    assert scope_rows[0].scope_kind == "workspace"
    assert scope_rows[0].scope_id == "platform"
    assert scope_rows[0].is_primary is True


# ── Test 5: Idempotency ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency_second_reject_raises_no_duplicate_observation(
    db_session: AsyncSession,
) -> None:
    """Re-rejecting the same proposal raises ValueError ('not pending') in the
    repository BEFORE the memory hook fires.  Exactly one observation lands.
    """
    from artemis.builder.repository import reject_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_pending_proposal(
        db_session, kind="agent", target_id=agent_db_id, citations=None
    )

    await reject_proposal(db_session, proposal_id, rejection_reason="first time")
    await db_session.commit()

    obs_after_first = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_after_first) == 1

    # Second reject must raise — proposal is no longer pending.
    with pytest.raises(ValueError, match="not pending"):
        await reject_proposal(db_session, proposal_id, rejection_reason="second time")

    obs_after_second = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_after_second) == 1, (
        f"Expected 1 observation (idempotent), got {len(obs_after_second)}"
    )


# ── Test 6: Failure isolation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_isolation_memory_write_does_not_break_reject(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the memory helper raises, the reject still succeeds and a WARNING is
    logged.  No partial memory state.
    """
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.repository import get_definition_proposal, reject_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_pending_proposal(
        db_session, kind="agent", target_id=agent_db_id, citations=None
    )

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated memory failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        row = await reject_proposal(db_session, proposal_id, rejection_reason="boom")
        await db_session.commit()

    # (a) status flip still succeeded
    assert row.status == "rejected"
    assert row.rejection_reason == "boom"

    refreshed = await get_definition_proposal(db_session, proposal_id)
    assert refreshed.status == "rejected"

    # (b) no partial memory state
    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 0, f"Expected 0 obs after failure, got {len(obs_rows)}"

    # (c) WARNING logged
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("CC29 memory observation write failed" in m for m in warnings), (
        f"Expected CC29 warning. Got: {warnings}"
    )


# ── Test 7: Empty citations ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_citations_observation_records_no_runs(
    db_session: AsyncSession,
) -> None:
    """Proposal with no cited run_ids: content reads 'Citations: runs (none)'
    and only the proposal evidence row is linked.
    """
    from artemis.builder.repository import reject_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_pending_proposal(
        db_session, kind="agent", target_id=agent_db_id, citations=None
    )

    await reject_proposal(db_session, proposal_id, rejection_reason="off-topic")
    await db_session.commit()

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1
    assert "Citations: runs (none)" in obs_rows[0].content

    evidence = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(evidence) == 1
    assert evidence[0].source_kind == "definition_proposal"
    assert evidence[0].source_id == str(proposal_id)


# ── Test 8: Content shape ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_content_shape_matches_cc29_format(db_session: AsyncSession) -> None:
    """Observation content matches the CC29 spec:
    'Operator rejected definition proposal #<id> for <kind> <slug> on <date>.
     Reason: <reason>. Citations: runs <ids>. Proposed by: <who>. Summary: <s>.'
    """
    from artemis.builder.repository import reject_proposal

    agent_db_id = await _seed_agent(db_session, "marketing.qualifier.brief_composer")
    proposal_id = await _seed_pending_proposal(
        db_session,
        kind="agent",
        target_id=agent_db_id,
        proposed_definition={"goal": "Compose targeted campaign briefs."},
        citations={"run_ids": [42, 99]},
    )

    await reject_proposal(db_session, proposal_id, rejection_reason="not aligned with persona")
    await db_session.commit()

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1
    content = obs_rows[0].content

    assert "Operator rejected definition proposal" in content
    assert f"#{proposal_id}" in content
    assert "agent" in content
    assert "marketing.qualifier.brief_composer" in content
    assert "Reason: not aligned with persona" in content
    assert "42, 99" in content or "42" in content
    assert "Proposed by: builder" in content
    assert "Compose targeted campaign briefs." in content
