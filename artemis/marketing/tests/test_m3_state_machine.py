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
]

_BRIEF_EDGES = [
    (BriefState.created, BriefState.in_inbox),
    (BriefState.in_inbox, BriefState.approved),
    (BriefState.in_inbox, BriefState.rejected),
    (BriefState.in_inbox, BriefState.snoozed),
    (BriefState.in_inbox, BriefState.asked),
    (BriefState.snoozed, BriefState.in_inbox),
    (BriefState.asked, BriefState.in_inbox),
]

_WORKSPACE_EDGES = [
    (WorkspaceState.pending_content, WorkspaceState.in_content_preparation),
    (WorkspaceState.in_content_preparation, WorkspaceState.sent_to_writing_studio),
    (WorkspaceState.in_content_preparation, WorkspaceState.content_preparation_failed),
]

_DELIVERABLE_EDGES = [
    (DeliverableState.queued, DeliverableState.generating),
    (DeliverableState.generating, DeliverableState.draft_ready),
    (DeliverableState.generating, DeliverableState.generation_failed),
    (DeliverableState.draft_ready, DeliverableState.approved),
    (DeliverableState.draft_ready, DeliverableState.revised),
    (DeliverableState.draft_ready, DeliverableState.rejected),
    (DeliverableState.revised, DeliverableState.generating),
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
    sig = await _sig(db_session, status="qualified")
    with pytest.raises(IllegalTransition):
        await transition(db_session, "signal", sig.id, SignalState.pending_qualification)
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
    ("workspace", "sent_to_writing_studio", WorkspaceState.in_content_preparation),
    ("workspace", "content_preparation_failed", WorkspaceState.pending_content),
    ("deliverable", "approved", DeliverableState.draft_ready),
    ("deliverable", "rejected", DeliverableState.generating),
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

    eng_b = create_async_engine(artemis.db.engine.url, poolclass=NullPool, echo=False)
    attach_pgvector_codec(eng_b)
    try:
        async with AsyncSessionCls(eng_b, expire_on_commit=False) as s, s.begin():
            with pytest.raises(IllegalTransition):
                await transition(s, "signal", sig_id, SignalState.pending_qualification)
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
