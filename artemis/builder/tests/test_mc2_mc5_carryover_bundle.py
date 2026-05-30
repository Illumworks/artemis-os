"""MC2-MC5 carryover bundle tests.

Tests:
  MC2: signal Gate-1 approval → memory (workspace:marketing + workspace:platform)
  MC3: skill promotion → memory (skill:<slug> + workspace:platform)
  MC4: pipeline human-gate decision → memory (workspace:pipeline-<id> + workspace:platform)
  MC5: FA marketing approval → memory (agent:floating-artemis + workspace:marketing + workspace:platform)

For each surface: multi-scope-via-join-table, failure isolation, content shape.

Requires ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_os_test.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401
import artemis.builders.models  # noqa: F401
import artemis.db as _db
import artemis.memory.models  # noqa: F401
import artemis.tools.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryEvidence, MemoryObservation, MemoryObservationScope

# ── DB URL guard ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

# ── Truncation SQL ────────────────────────────────────────────────────────────

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observation_scopes, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session; patches artemis.db.SessionLocal so carryover opens its session
    on the same test engine."""
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


# ── Shared assertion helper ───────────────────────────────────────────────────


async def _get_obs_and_scopes(
    session: AsyncSession,
) -> tuple[list[MemoryObservation], list[MemoryObservationScope]]:
    obs = (await session.execute(select(MemoryObservation))).scalars().all()
    scopes = (await session.execute(select(MemoryObservationScope))).scalars().all()
    return list(obs), list(scopes)


# ═══════════════════════════════════════════════════════════════════════════
# MC2: Signal Gate-1 approval
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc2_gate1_approval_multi_scope_join_table(db_session: AsyncSession) -> None:
    """MC2: approval writes 1 observation + 2 scope-join rows (marketing + platform)."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=42,
        new_status="approved",
        decided_by="operator",
        decision_payload={"headline": "New legislation passed in Ohio"},
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1, f"Expected 1 observation, got {len(obs_list)}"

    obs = obs_list[0]
    assert obs.scope_kind == "workspace"
    assert obs.scope_id == "marketing"
    assert obs.category == "signal_gate1_decision"
    assert obs.wing == "durable"
    assert obs.confidence_origin == "mc_signal_gate1"

    assert len(scope_rows) == 2, f"Expected 2 scope-join rows, got {len(scope_rows)}"
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("workspace", "marketing", True) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(ev_rows) == 1
    assert ev_rows[0].source_kind == "signal_queue"
    assert ev_rows[0].source_id == 42


@pytest.mark.asyncio
async def test_mc2_rejection_also_writes_observation(db_session: AsyncSession) -> None:
    """MC2: rejection (rejected_at_gate_1) also produces a memory observation."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=99,
        new_status="rejected_at_gate_1",
        decided_by="operator",
        decision_payload=None,
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    assert "rejected" in obs_list[0].content


@pytest.mark.asyncio
async def test_mc2_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MC2: memory write failure does not propagate; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated MC2 failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        # Must not raise
        await write_signal_gate1_approval_observation(
            signal_id=7,
            new_status="approved",
            decided_by="operator",
            decision_payload=None,
        )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 0

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC2 memory observation write failed" in m for m in warnings), (
        f"Expected MC2 warning. Got: {warnings}"
    )


@pytest.mark.asyncio
async def test_mc2_content_shape(db_session: AsyncSession) -> None:
    """MC2: observation content contains signal id, decision, date, headline."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=182,
        new_status="approved",
        decided_by="operator",
        decision_payload={
            "headline": "Edtech legislation update",
            "reason_codes": ["POLICY_EDTECH_TIME_LIMIT"],
        },
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    content = obs_list[0].content
    assert "#182" in content
    assert "approved" in content
    assert "operator" in content
    assert "Edtech legislation update" in content
    assert "POLICY_EDTECH_TIME_LIMIT" in content


# ═══════════════════════════════════════════════════════════════════════════
# MC3: Skill promotion
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc3_skill_promotion_multi_scope_join_table(db_session: AsyncSession) -> None:
    """MC3: promotion writes 1 observation + 2 scope-join rows (skill + platform)."""
    from artemis.builder.memory_carryover import write_skill_promotion_observation

    await write_skill_promotion_observation(
        skill_slug="brief-composer",
        skill_name="Brief Composer",
        description="Composes campaign briefs",
        promoted_by="operator",
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1, f"Expected 1 observation, got {len(obs_list)}"

    obs = obs_list[0]
    assert obs.scope_kind == "skill"
    assert obs.scope_id == "brief-composer"
    assert obs.category == "skill_promotion"
    assert obs.wing == "durable"
    assert obs.confidence_origin == "mc_skill_promotion"

    assert len(scope_rows) == 2, f"Expected 2 scope-join rows, got {len(scope_rows)}"
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("skill", "brief-composer", True) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs

    from artemis.builder.memory_carryover import _source_id_to_int

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(ev_rows) == 1
    assert ev_rows[0].source_kind == "skill"
    # source_id is hashed (slug → stable int) since MemoryEvidence.source_id is BigInteger
    assert ev_rows[0].source_id == _source_id_to_int("brief-composer")


@pytest.mark.asyncio
async def test_mc3_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MC3: memory write failure does not propagate; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.memory_carryover import write_skill_promotion_observation

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated MC3 failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        await write_skill_promotion_observation(
            skill_slug="my-skill",
            skill_name="My Skill",
            description=None,
            promoted_by="operator",
        )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 0

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC3 memory observation write failed" in m for m in warnings)


@pytest.mark.asyncio
async def test_mc3_content_shape(db_session: AsyncSession) -> None:
    """MC3: observation content contains slug, name, description, date, actor."""
    from artemis.builder.memory_carryover import write_skill_promotion_observation

    await write_skill_promotion_observation(
        skill_slug="qualifier-skill",
        skill_name="Qualifier Skill",
        description="Qualifies campaign signals",
        promoted_by="operator",
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    content = obs_list[0].content
    assert "qualifier-skill" in content
    assert "Qualifier Skill" in content
    assert "approved status" in content
    assert "operator" in content
    assert "Qualifies campaign signals" in content


# ═══════════════════════════════════════════════════════════════════════════
# MC4: Pipeline human-gate decision
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc4_pipeline_gate_multi_scope_join_table(db_session: AsyncSession) -> None:
    """MC4: gate decision writes 1 observation + 2 scope-join rows."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-abc-123",
        pipeline_id="pipeline-marketing-v1",
        node_id="gate_node_1",
        decision="approved",
        decided_by="jon@amiralearning.com",
        decision_payload={"pipeline_name": "Marketing Pipeline v1"},
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1, f"Expected 1 observation, got {len(obs_list)}"

    obs = obs_list[0]
    assert obs.scope_kind == "workspace"
    assert obs.scope_id == "pipeline-pipeline-marketing-v1"
    assert obs.category == "pipeline_gate_decision"
    assert obs.wing == "durable"
    assert obs.confidence_origin == "mc_pipeline_gate"

    # 2 scope-join rows: primary (workspace:pipeline-...) + secondary (workspace:platform)
    assert len(scope_rows) == 2, f"Expected 2 scope-join rows, got {len(scope_rows)}"
    primary_rows = [r for r in scope_rows if r.is_primary]
    assert len(primary_rows) == 1

    from artemis.builder.memory_carryover import _source_id_to_int

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(ev_rows) == 1
    assert ev_rows[0].source_kind == "pipeline_run"
    assert ev_rows[0].source_id == _source_id_to_int("run-abc-123")


@pytest.mark.asyncio
async def test_mc4_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MC4: memory write failure does not propagate; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated MC4 failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        await write_pipeline_gate_decision_observation(
            pipeline_run_id="run-xyz",
            pipeline_id="pipe-1",
            node_id="gate_1",
            decision="rejected",
            decided_by="operator",
            decision_payload=None,
        )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 0

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC4 memory observation write failed" in m for m in warnings)


@pytest.mark.asyncio
async def test_mc4_content_shape(db_session: AsyncSession) -> None:
    """MC4: observation content contains pipeline_id, node_id, decision, decided_by, date."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-42",
        pipeline_id="pipeline-edtech",
        node_id="gate_review",
        decision="approved",
        decided_by="jon@amiralearning.com",
        decision_payload={"pipeline_name": "Edtech Pipeline"},
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    content = obs_list[0].content
    assert "pipeline-edtech" in content
    assert "gate_review" in content
    assert "approved" in content
    assert "jon@amiralearning.com" in content


# ═══════════════════════════════════════════════════════════════════════════
# MC5: FA marketing approval
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc5_fa_approval_three_scope_join_rows(db_session: AsyncSession) -> None:
    """MC5: FA approval writes 1 observation + 3 scope-join rows (FA + marketing + platform)."""
    from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

    await write_fa_marketing_approval_observation(
        signal_id=77,
        new_status="approved",
        fa_session_id="fa-session-abc123",
        user_directive="Approve the Ohio legislation signal",
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1, f"Expected 1 observation, got {len(obs_list)}"

    obs = obs_list[0]
    assert obs.scope_kind == "agent"
    assert obs.scope_id == "floating-artemis"
    assert obs.category == "fa_marketing_approval"
    assert obs.wing == "durable"
    assert obs.confidence_origin == "mc_fa_marketing"

    # 3 scope-join rows: FA (primary) + marketing + platform
    assert len(scope_rows) == 3, f"Expected 3 scope-join rows (MC5 3-scope), got {len(scope_rows)}"
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("agent", "floating-artemis", True) in scope_pairs
    assert ("workspace", "marketing", False) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(ev_rows) == 2, (
        f"Expected 2 evidence rows (signal_queue + fa_messages), got {len(ev_rows)}"
    )
    source_kinds = {e.source_kind for e in ev_rows}
    assert "signal_queue" in source_kinds, f"Missing signal_queue, got {source_kinds}"
    assert "floating_artemis_messages" in source_kinds, f"Missing fa_messages, got {source_kinds}"
    # signal_queue source_id is the integer signal_id
    sq_ev = next(e for e in ev_rows if e.source_kind == "signal_queue")
    assert sq_ev.source_id == 77


@pytest.mark.asyncio
async def test_mc5_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MC5: memory write failure does not propagate; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated MC5 failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        await write_fa_marketing_approval_observation(
            signal_id=5,
            new_status="approved",
            fa_session_id="session-1",
            user_directive=None,
        )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 0

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC5 memory observation write failed" in m for m in warnings)


@pytest.mark.asyncio
async def test_mc5_content_shape(db_session: AsyncSession) -> None:
    """MC5: observation content contains signal_id, session_id, user_directive, date."""
    from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

    await write_fa_marketing_approval_observation(
        signal_id=55,
        new_status="approved",
        fa_session_id="session-xyz",
        user_directive="Approve the signal for Q3 campaign",
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    content = obs_list[0].content
    assert "#55" in content
    assert "session-xyz" in content
    assert "Approve the signal for Q3 campaign" in content
    assert "approved" in content


@pytest.mark.asyncio
async def test_mc5_no_directive_uses_fallback(db_session: AsyncSession) -> None:
    """MC5: None user_directive substitutes 'inferred from context'."""
    from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

    await write_fa_marketing_approval_observation(
        signal_id=10,
        new_status="approved",
        fa_session_id="session-no-directive",
        user_directive=None,
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert "inferred from context" in obs_list[0].content


# ═══════════════════════════════════════════════════════════════════════════
# Shared helper usage confirmation
# ═══════════════════════════════════════════════════════════════════════════


def test_all_helpers_use_multi_scope_write() -> None:
    """Confirm _multi_scope_observation_write is referenced in all 5 helpers (MC1-MC5)."""
    import inspect

    import artemis.builder.memory_carryover as _carryover

    helper_names = [
        "write_proposal_approval_observation",
        "write_signal_gate1_approval_observation",
        "write_skill_promotion_observation",
        "write_pipeline_gate_decision_observation",
        "write_fa_marketing_approval_observation",
    ]
    for name in helper_names:
        fn = getattr(_carryover, name)
        src = inspect.getsource(fn)
        assert "_multi_scope_observation_write" in src, (
            f"{name} does not call _multi_scope_observation_write"
        )
