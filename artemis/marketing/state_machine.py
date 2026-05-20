"""Campaign State Machine — M3.

Single source of truth for all 5 marketing lifecycle states and transitions.

  SignalState      → signal_queue.signal_status
  BriefState       → campaign_candidates.decision_state
  WorkspaceState   → campaign_candidates.workspace_state
  DeliverableState → campaign_deliverables.status
  DraftState       → alias for DeliverableState (fully overlapping, deduplicated)

Call transition(session, entity_type, entity_id, to_state) to mutate state.
All other state writes are illegal after M3.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any


class SignalState(enum.StrEnum):
    """signal_queue.signal_status lifecycle."""

    pending_qualification = "pending_qualification"
    qualified = "qualified"
    rejected_hard_filter = "rejected_hard_filter"  # terminal
    suppressed_stale = "suppressed_stale"  # terminal


class BriefState(enum.StrEnum):
    """campaign_candidates.decision_state lifecycle (Gate 1)."""

    created = "created"
    in_inbox = "in_inbox"
    approved = "approved"  # terminal
    rejected = "rejected"  # terminal
    snoozed = "snoozed"
    asked = "asked"


class WorkspaceState(enum.StrEnum):
    """campaign_candidates.workspace_state lifecycle."""

    pending_content = "pending_content"
    in_content_preparation = "in_content_preparation"
    sent_to_writing_studio = "sent_to_writing_studio"  # terminal
    content_preparation_failed = "content_preparation_failed"  # terminal


class DeliverableState(enum.StrEnum):
    """campaign_deliverables.status lifecycle."""

    queued = "queued"
    generating = "generating"
    draft_ready = "draft_ready"
    approved = "approved"  # terminal
    revised = "revised"
    rejected = "rejected"  # terminal
    generation_failed = "generation_failed"  # terminal


# DraftState deduplicates with DeliverableState — values are identical.
DraftState = DeliverableState


# ── Transition maps (terminal states → empty set) ─────────────────────────────

SIGNAL_TRANSITIONS: dict[SignalState, set[SignalState]] = {
    SignalState.pending_qualification: {
        SignalState.qualified,
        SignalState.rejected_hard_filter,
        SignalState.suppressed_stale,
    },
    SignalState.qualified: set(),
    SignalState.rejected_hard_filter: set(),
    SignalState.suppressed_stale: set(),
}

BRIEF_TRANSITIONS: dict[BriefState, set[BriefState]] = {
    BriefState.created: {BriefState.in_inbox},
    BriefState.in_inbox: {
        BriefState.approved, BriefState.rejected, BriefState.snoozed, BriefState.asked
    },
    BriefState.approved: set(),
    BriefState.rejected: set(),
    BriefState.snoozed: {BriefState.in_inbox},
    BriefState.asked: {BriefState.in_inbox},
}

WORKSPACE_TRANSITIONS: dict[WorkspaceState, set[WorkspaceState]] = {
    WorkspaceState.pending_content: {WorkspaceState.in_content_preparation},
    WorkspaceState.in_content_preparation: {
        WorkspaceState.sent_to_writing_studio,
        WorkspaceState.content_preparation_failed,
    },
    WorkspaceState.sent_to_writing_studio: set(),
    WorkspaceState.content_preparation_failed: set(),
}

DELIVERABLE_TRANSITIONS: dict[DeliverableState, set[DeliverableState]] = {
    DeliverableState.queued: {DeliverableState.generating},
    DeliverableState.generating: {
        DeliverableState.draft_ready, DeliverableState.generation_failed
    },
    DeliverableState.draft_ready: {
        DeliverableState.approved, DeliverableState.revised, DeliverableState.rejected
    },
    DeliverableState.approved: set(),
    DeliverableState.revised: {DeliverableState.generating},
    DeliverableState.rejected: set(),
    DeliverableState.generation_failed: set(),
}

# Lazy entity-type registry — avoids circular imports at module load time.
_ENTITY_MAP: dict[str, Any] | None = None


def _get_entity_map() -> dict[str, Any]:
    global _ENTITY_MAP
    if _ENTITY_MAP is not None:
        return _ENTITY_MAP
    from artemis.marketing.models import CampaignCandidate, CampaignDeliverable, SignalQueue

    _ENTITY_MAP = {
        "signal": (SIGNAL_TRANSITIONS, SignalQueue, "signal_status"),
        "brief": (BRIEF_TRANSITIONS, CampaignCandidate, "decision_state"),
        "workspace": (WORKSPACE_TRANSITIONS, CampaignCandidate, "workspace_state"),
        "deliverable": (DELIVERABLE_TRANSITIONS, CampaignDeliverable, "status"),
    }
    return _ENTITY_MAP


class IllegalTransition(Exception):  # noqa: N818 — name mandated by M3 brief
    """Raised on invalid state transition (pre-DB for unknown state/edge; post-lock for race)."""

    def __init__(
        self, message: str, *, entity_type: str = "", from_state: str = "", to_state: str = ""
    ) -> None:
        super().__init__(message)
        self.entity_type = entity_type
        self.from_state = from_state
        self.to_state = to_state


async def transition(
    session: Any,
    entity_type: str,
    entity_id: int,
    to_state: str | enum.Enum,
    *,
    reason: str | None = None,
    actor: str | None = None,
) -> Any:
    """Advance an entity's lifecycle state and atomically write an audit row.

    Raises IllegalTransition (pre-DB) for unknown entity_type or to_state value.
    Raises IllegalTransition (post-lock) for illegal edge or stale from_state.
    Raises ValueError if entity_id does not exist.
    Caller owns commit/rollback.
    """
    from sqlalchemy import select

    from artemis.marketing.models import CampaignStateTransition

    entity_map = _get_entity_map()
    if entity_type not in entity_map:
        raise IllegalTransition(
            f"Unknown entity_type {entity_type!r}. Valid: {sorted(entity_map)}",
            entity_type=entity_type,
            to_state=str(to_state),
        )
    transitions_map, model_cls, status_attr = entity_map[entity_type]
    enum_cls = type(next(iter(transitions_map)))

    # Resolve to_state BEFORE any DB call — cheap fail for unknown values.
    if isinstance(to_state, enum_cls):
        resolved_to = to_state
    else:
        try:
            resolved_to = enum_cls(str(to_state))
        except ValueError:
            raise IllegalTransition(
                f"Unknown state {to_state!r} for lifecycle {entity_type!r}. "
                f"Valid: {[e.value for e in enum_cls]}",
                entity_type=entity_type,
                to_state=str(to_state),
            ) from None

    # Lock row — serialises concurrent writers.
    result = await session.execute(
        select(model_cls).where(model_cls.id == entity_id).with_for_update()
    )
    entity = result.scalar_one_or_none()
    if entity is None:
        raise ValueError(f"{model_cls.__tablename__} id={entity_id} not found")

    from_state_str: str = getattr(entity, status_attr)

    # Re-validate from_state under lock — catches concurrent transition wins.
    try:
        resolved_from = enum_cls(from_state_str)
    except ValueError:
        raise IllegalTransition(
            f"Cannot transition from legacy/unknown state {from_state_str!r}. "
            "Resolve this row manually before calling transition().",
            entity_type=entity_type,
            from_state=from_state_str,
            to_state=resolved_to.value,
        ) from None

    allowed = transitions_map.get(resolved_from, set())
    if resolved_to not in allowed:
        raise IllegalTransition(
            f"Illegal {from_state_str!r} → {resolved_to.value!r} "
            f"for {entity_type!r}. "
            f"Legal next: {sorted(s.value for s in allowed) or '(terminal)'}",
            entity_type=entity_type,
            from_state=from_state_str,
            to_state=resolved_to.value,
        )

    now = datetime.now(UTC)
    setattr(entity, status_attr, resolved_to.value)
    if hasattr(entity, "updated_at"):
        entity.updated_at = now

    session.add(
        CampaignStateTransition(
            entity_type=entity_type,
            entity_id=entity_id,
            from_state=from_state_str,
            to_state=resolved_to.value,
            actor=actor,
            reason=reason,
            transitioned_at=now,
        )
    )
    await session.flush()
    return entity
