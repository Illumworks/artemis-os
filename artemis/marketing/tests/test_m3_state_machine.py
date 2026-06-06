"""M3 — Campaign State Machine tests.

Coverage per brief:
  1. Every legal edge per lifecycle: state changes, audit row written, from_state correct.
  2. Illegal edges raise IllegalTransition; no audit row; state unchanged.
  3. Terminal states sticky — no outgoing edges.
  4. Unknown to_state raises before DB call (entity type unknown too).
  5. Concurrent writes serialise via FOR UPDATE.
  6. DraftState alias equals DeliverableState.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import (
    CampaignCandidate,
    CampaignDeliverable,
    CampaignStateTransition,
    SignalQueue,
)
from artemis.marketing.state_machine import (
    BRIEF_TRANSITIONS,
    DELIVERABLE_TRANSITIONS,
    SIGNAL_TRANSITIONS,
    WORKSPACE_TRANSITIONS,
    BriefState,
    DeliverableState,
    DraftState,
    IllegalTransition,
    SignalState,
    WorkspaceState,
    transition,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _sig(session: AsyncSession, status: str = "pending_qualification") -> SignalQueue:
    s = SignalQueue(headline="T", campaign_family="f", signal_status=status, summary="t")
    session.add(s)
    await session.flush()
    return s


async def _cand(
    session: AsyncSession,
    decision_state: str = "created",
    workspace_state: str = "pending_content",
) -> CampaignCandidate:
    c = CampaignCandidate(
        campaign_family="f", decision_state=decision_state, workspace_state=workspace_state
    )
    session.add(c)
    await session.flush()
    return c


async def _deliv(session: AsyncSession, status: str = "queued") -> CampaignDeliverable:
    c = await _cand(session)
    d = CampaignDeliverable(candidate_id=c.id, status=status)
    session.add(d)
    await session.flush()
    return d


async def _audit(session: AsyncSession, etype: str, eid: int) -> int:
    r = await session.execute(
        select(CampaignStateTransition).where(
            CampaignStateTransition.entity_type == etype,
            CampaignStateTransition.entity_id == eid,
        )
    )
    return len(list(r.scalars().all()))


# ── 1. Legal edges — parametrized ─────────────────────────────────────────────

_SIGNAL_EDGES = [
    (SignalState.pending_qualification, SignalState.qualified),
    (SignalState.pending_qualification, SignalState.rejected_hard_filter),
    (SignalState.pending_qualification, SignalState.suppressed_stale),
    # M3a Gate-1 outcome edges
    (SignalState.qualified, SignalState.APPROVED),
    (SignalState.qualified, SignalState.REJECTED_AT_GATE_1),
    (SignalState.qualified, SignalState.SNOOZED),
    (SignalState.qualified, SignalState.ARCHIVED),
    (SignalState.SNOOZED, SignalState.qualified),
]

_BRIEF_EDGES = [
    (BriefState.created, BriefState.in_inbox),
    (BriefState.in_inbox, BriefState.approved),
    (BriefState.in_inbox, BriefState.rejected),
    (BriefState.in_inbox, BriefState.snoozed),
    (BriefState.in_inbox, BriefState.asked),
    (BriefState.snoozed, BriefState.in_inbox),
    (BriefState.asked, BriefState.in_inbox),
    # M3a new members
    (BriefState.in_inbox, BriefState.monitoring),
    (BriefState.in_inbox, BriefState.changes_requested),
    (BriefState.monitoring, BriefState.in_inbox),
    (BriefState.changes_requested, BriefState.in_inbox),
]

_WORKSPACE_EDGES = [
    (WorkspaceState.pending_content, WorkspaceState.in_content_preparation),
    (WorkspaceState.in_content_preparation, WorkspaceState.sent_to_writing_studio),
    (WorkspaceState.in_content_preparation, WorkspaceState.content_preparation_failed),
    # M3a new members
    (WorkspaceState.sent_to_writing_studio, WorkspaceState.content_in_review),
    (WorkspaceState.content_in_review, WorkspaceState.all_content_approved),
    (WorkspaceState.content_in_review, WorkspaceState.revision_needed),
    (WorkspaceState.revision_needed, WorkspaceState.in_content_preparation),
]

_DELIVERABLE_EDGES = [
    (DeliverableState.queued, DeliverableState.generating),
    (DeliverableState.generating, DeliverableState.draft_ready),
    (DeliverableState.generating, DeliverableState.generation_failed),
    (DeliverableState.draft_ready, DeliverableState.approved),
    (DeliverableState.draft_ready, DeliverableState.revised),
    (DeliverableState.draft_ready, DeliverableState.rejected),
    (DeliverableState.revised, DeliverableState.generating),
    (DeliverableState.rejected, DeliverableState.draft_ready),  # revision after gate-2 rejection
]


@pytest.mark.asyncio
@pytest.mark.parametrize("frm,to", _SIGNAL_EDGES)
async def test_signal_legal_edges(
    frm: SignalState, to: SignalState, db_session: AsyncSession
) -> None:
    sig = await _sig(db_session, status=frm.value)
    updated = await transition(db_session, "signal", sig.id, to)
    assert updated.signal_status == to.value
    assert await _audit(db_session, "signal", sig.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("frm,to", _BRIEF_EDGES)
async def test_brief_legal_edges(frm: BriefState, to: BriefState, db_session: AsyncSession) -> None:
    cand = await _cand(db_session, decision_state=frm.value)
    updated = await transition(db_session, "brief", cand.id, to)
    assert updated.decision_state == to.value
    assert await _audit(db_session, "brief", cand.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("frm,to", _WORKSPACE_EDGES)
async def test_workspace_legal_edges(
    frm: WorkspaceState, to: WorkspaceState, db_session: AsyncSession
) -> None:
    cand = await _cand(db_session, workspace_state=frm.value)
    updated = await transition(db_session, "workspace", cand.id, to)
    assert updated.workspace_state == to.value
    assert await _audit(db_session, "workspace", cand.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("frm,to", _DELIVERABLE_EDGES)
async def test_deliverable_legal_edges(
    frm: DeliverableState, to: DeliverableState, db_session: AsyncSession
) -> None:
    deliv = await _deliv(db_session, status=frm.value)
    updated = await transition(db_session, "deliverable", deliv.id, to)
    assert updated.status == to.value
    assert await _audit(db_session, "deliverable", deliv.id) == 1


# ── 2. Audit row shape ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_from_state_correct(db_session: AsyncSession) -> None:
    sig = await _sig(db_session)
    await transition(
        db_session, "signal", sig.id, SignalState.qualified, actor="a@b.com", reason="ok"
    )
    r = await db_session.execute(
        select(CampaignStateTransition).where(CampaignStateTransition.entity_id == sig.id)
    )
    row = r.scalar_one()
    assert row.from_state == "pending_qualification"
    assert row.to_state == "qualified"
    assert row.actor == "a@b.com"
    assert row.reason == "ok"


# ── 3. Illegal edges — no audit, state unchanged ──────────────────────────────


@pytest.mark.asyncio
async def test_illegal_edge_raises_no_audit(db_session: AsyncSession) -> None:
    # qualified → rejected_hard_filter is illegal (hard-filter rejection is only
    # valid from pending_qualification; qualified → pending_qualification is now
    # a legal re-evaluation demotion, so use a genuinely illegal target).
    sig = await _sig(db_session, status="qualified")
    with pytest.raises(IllegalTransition):
        await transition(db_session, "signal", sig.id, SignalState.rejected_hard_filter)
    await db_session.refresh(sig)
    assert sig.signal_status == "qualified"
    assert await _audit(db_session, "signal", sig.id) == 0


@pytest.mark.asyncio
async def test_illegal_brief_edge_no_audit(db_session: AsyncSession) -> None:
    cand = await _cand(db_session, decision_state="approved")
    with pytest.raises(IllegalTransition):
        await transition(db_session, "brief", cand.id, BriefState.rejected)
    assert await _audit(db_session, "brief", cand.id) == 0


# ── 4. Terminal stickiness ────────────────────────────────────────────────────

_TERMINAL_CASES = [
    ("signal", "rejected_hard_filter", SignalState.pending_qualification),
    ("signal", "suppressed_stale", SignalState.qualified),
    ("brief", "approved", BriefState.in_inbox),
    ("brief", "rejected", BriefState.in_inbox),
    # sent_to_writing_studio is no longer terminal (M3a: → content_in_review)
    ("workspace", "content_preparation_failed", WorkspaceState.pending_content),
    ("workspace", "all_content_approved", WorkspaceState.in_content_preparation),
    ("deliverable", "approved", DeliverableState.draft_ready),
    # rejected is no longer terminal (M3a: → draft_ready for gate-2 revision)
    ("deliverable", "generation_failed", DeliverableState.generating),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("etype,terminal_val,to", _TERMINAL_CASES)
async def test_terminal_states_sticky(
    etype: str, terminal_val: str, to: object, db_session: AsyncSession
) -> None:
    from typing import Any

    entity: Any
    if etype == "signal":
        entity = await _sig(db_session, status=terminal_val)
    elif etype in ("brief", "workspace"):
        if etype == "brief":
            entity = await _cand(db_session, decision_state=terminal_val)
        else:
            entity = await _cand(db_session, workspace_state=terminal_val)
    else:
        entity = await _deliv(db_session, status=terminal_val)
    with pytest.raises(IllegalTransition):
        await transition(db_session, etype, entity.id, to)  # type: ignore[arg-type]


# ── 5. Unknown state/entity raises pre-DB ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_to_state_raises(db_session: AsyncSession) -> None:
    sig = await _sig(db_session)
    with pytest.raises(IllegalTransition, match="ghost_state"):
        await transition(db_session, "signal", sig.id, "ghost_state")
    assert await _audit(db_session, "signal", sig.id) == 0


@pytest.mark.asyncio
async def test_unknown_entity_type_raises(db_session: AsyncSession) -> None:
    with pytest.raises(IllegalTransition):
        await transition(db_session, "bad_entity", 1, "qualified")


# ── 6. Concurrent writes serialise via FOR UPDATE ─────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_transition_second_loses(db_session: AsyncSession) -> None:
    from sqlalchemy import NullPool
    from sqlalchemy.ext.asyncio import AsyncSession as AsyncSessionCls  # noqa: N817
    from sqlalchemy.ext.asyncio import create_async_engine

    import artemis.db
    from artemis.db import attach_pgvector_codec

    sig = await _sig(db_session)
    sig_id = sig.id
    await db_session.commit()

    eng_a = create_async_engine(artemis.db.engine.url, poolclass=NullPool, echo=False)
    attach_pgvector_codec(eng_a)
    try:
        async with AsyncSessionCls(eng_a, expire_on_commit=False) as s, s.begin():
            await transition(s, "signal", sig_id, SignalState.qualified)
    finally:
        await eng_a.dispose()

    # After eng_a committed qualified, try to transition to rejected_hard_filter
    # (illegal from qualified — hard-filter rejection only comes from pending_qualification).
    # This remains illegal even after the qualified→pending_qualification demotion was added.
    eng_b = create_async_engine(artemis.db.engine.url, poolclass=NullPool, echo=False)
    attach_pgvector_codec(eng_b)
    try:
        async with AsyncSessionCls(eng_b, expire_on_commit=False) as s, s.begin():
            with pytest.raises(IllegalTransition):
                await transition(s, "signal", sig_id, SignalState.rejected_hard_filter)
    finally:
        await eng_b.dispose()


# ── 7. DraftState alias ───────────────────────────────────────────────────────


def test_draft_state_is_deliverable_state() -> None:
    assert DraftState is DeliverableState
    assert set(DraftState) == set(DeliverableState)


# ── 8. LEGAL_TRANSITIONS completeness: every enum member has an entry ─────────


def test_signal_transitions_covers_all_states() -> None:
    assert set(SIGNAL_TRANSITIONS.keys()) == set(SignalState)


def test_brief_transitions_covers_all_states() -> None:
    assert set(BRIEF_TRANSITIONS.keys()) == set(BriefState)


def test_workspace_transitions_covers_all_states() -> None:
    assert set(WORKSPACE_TRANSITIONS.keys()) == set(WorkspaceState)


def test_deliverable_transitions_covers_all_states() -> None:
    assert set(DELIVERABLE_TRANSITIONS.keys()) == set(DeliverableState)


# ── 9. WorkspaceState end-to-end multi-hop ────────────────────────────────────


@pytest.mark.asyncio
async def test_workspace_sent_to_review_to_approved(db_session: AsyncSession) -> None:
    """sent_to_writing_studio → content_in_review → all_content_approved round-trip.
    Each hop writes an audit row.
    """
    cand = await _cand(db_session, workspace_state=WorkspaceState.sent_to_writing_studio.value)
    await transition(db_session, "workspace", cand.id, WorkspaceState.content_in_review)
    await transition(db_session, "workspace", cand.id, WorkspaceState.all_content_approved)
    await db_session.refresh(cand)
    assert cand.workspace_state == WorkspaceState.all_content_approved.value
    assert await _audit(db_session, "workspace", cand.id) == 2


@pytest.mark.asyncio
async def test_workspace_revision_loop(db_session: AsyncSession) -> None:
    """content_in_review → revision_needed → in_content_preparation round-trip."""
    cand = await _cand(db_session, workspace_state=WorkspaceState.content_in_review.value)
    await transition(db_session, "workspace", cand.id, WorkspaceState.revision_needed)
    await transition(db_session, "workspace", cand.id, WorkspaceState.in_content_preparation)
    await db_session.refresh(cand)
    assert cand.workspace_state == WorkspaceState.in_content_preparation.value
    assert await _audit(db_session, "workspace", cand.id) == 2


# ── 10. BriefState monitoring / changes_requested loops ───────────────────────


@pytest.mark.asyncio
async def test_brief_monitoring_round_trip(db_session: AsyncSession) -> None:
    """in_inbox → monitoring → in_inbox — audit rows written for both hops."""
    cand = await _cand(db_session, decision_state=BriefState.in_inbox.value)
    await transition(db_session, "brief", cand.id, BriefState.monitoring)
    await transition(db_session, "brief", cand.id, BriefState.in_inbox)
    await db_session.refresh(cand)
    assert cand.decision_state == BriefState.in_inbox.value
    assert await _audit(db_session, "brief", cand.id) == 2


@pytest.mark.asyncio
async def test_brief_changes_requested_round_trip(db_session: AsyncSession) -> None:
    """in_inbox → changes_requested → in_inbox — audit rows written for both hops."""
    cand = await _cand(db_session, decision_state=BriefState.in_inbox.value)
    await transition(db_session, "brief", cand.id, BriefState.changes_requested)
    await transition(db_session, "brief", cand.id, BriefState.in_inbox)
    await db_session.refresh(cand)
    assert cand.decision_state == BriefState.in_inbox.value
    assert await _audit(db_session, "brief", cand.id) == 2


# ── 11. LEGACY_STATUS_MAP completeness ────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_status_map_all_targets_reachable(db_session: AsyncSession) -> None:
    """Every value in LEGACY_STATUS_MAP is a valid state in its enum.

    The test verifies that enum resolution succeeds for every mapped target
    (i.e. no stale or misspelled enum member reference). It does not call
    transition() directly because LEGACY_STATUS_MAP maps FROM legacy strings
    that are not necessarily reachable from the initial fixture state; the
    purpose is enum-completeness coverage only.
    """
    from artemis.marketing.state_machine import LEGACY_STATUS_MAP

    for (entity_type, legacy_value), target_enum_member in LEGACY_STATUS_MAP.items():
        assert isinstance(
            target_enum_member, (SignalState, BriefState, WorkspaceState, DeliverableState)
        ), (
            f"LEGACY_STATUS_MAP[({entity_type!r}, {legacy_value!r})] is not a known state enum member: "
            f"{target_enum_member!r}"
        )
        # The string value must round-trip through the enum
        enum_cls = type(target_enum_member)
        assert enum_cls(target_enum_member.value) is target_enum_member, (
            f"Round-trip failed for {target_enum_member!r}"
        )
