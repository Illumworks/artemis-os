"""Campaign State Machine — M3 / M3a.

Single source of truth for all 5 marketing lifecycle states and transitions.

  SignalState      → signal_queue.signal_status
  BriefState       → campaign_candidates.decision_state
  WorkspaceState   → campaign_candidates.workspace_state
  DeliverableState → campaign_deliverables.status
  DraftState       → alias for DeliverableState (fully overlapping, deduplicated)

Call transition(session, entity_type, entity_id, to_state) to mutate state.
All other state writes are illegal after M3a.

LEGACY_STATUS_MAP maps (entity_type, legacy_value) → new enum member.
Use it to convert stored legacy values before calling transition().
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any


class SignalState(enum.StrEnum):
    """signal_queue.signal_status lifecycle.

    Canonical source of truth (H2 alignment). Every value written to
    signal_queue.signal_status must be a member here.

    suppressed_deprioritized — emitted by qualifier_rule_layer suppress rules
        (e.g. suppress_tx_biliteracy_v1). It is a legitimate terminal state,
        not a legacy alias. Added here because live code already writes it but
        the enum was missing the member (CC20 drift report).
        Live DB scan 2026-05-29: 0 rows (no data migration needed).
    """

    pending_qualification = "pending_qualification"
    qualified = "qualified"
    rejected_hard_filter = "rejected_hard_filter"  # terminal
    suppressed_stale = "suppressed_stale"  # terminal
    suppressed_deprioritized = "suppressed_deprioritized"  # terminal — H2 drift fix

    # These four members reflect Gate 1 outcomes currently written to
    # signal_status. Long-term these belong on signal_briefs.status, not
    # on signal lifecycle — see follow-up brief m3b-attribution-cleanup.
    # For now, extending unblocks the sweep without lossy collapse.
    APPROVED = "approved"
    REJECTED_AT_GATE_1 = "rejected_at_gate_1"
    SNOOZED = "snoozed"
    ARCHIVED = "archived"


class BriefState(enum.StrEnum):
    """campaign_candidates.decision_state lifecycle (Gate 1)."""

    created = "created"
    in_inbox = "in_inbox"
    approved = "approved"  # terminal
    rejected = "rejected"  # terminal
    snoozed = "snoozed"
    asked = "asked"
    monitoring = "monitoring"
    changes_requested = "changes_requested"


class WorkspaceState(enum.StrEnum):
    """campaign_candidates.workspace_state lifecycle."""

    pending_content = "pending_content"
    in_content_preparation = "in_content_preparation"
    sent_to_writing_studio = "sent_to_writing_studio"
    content_preparation_failed = "content_preparation_failed"  # terminal
    content_in_review = "content_in_review"
    all_content_approved = "all_content_approved"  # terminal
    revision_needed = "revision_needed"


class DeliverableState(enum.StrEnum):
    """campaign_deliverables.status lifecycle.

    SEND2-B adds two send-pipeline states:
      queued_for_send — deliverable has been enqueued for outbound send;
                        one campaign_sends row exists with status='queued'.
      sent            — terminal; transport stub has recorded the send.

    Full happy-path: queued → generating → draft_ready → approved
                     → queued_for_send → sent.
    """

    queued = "queued"
    generating = "generating"
    draft_ready = "draft_ready"
    approved = "approved"
    queued_for_send = "queued_for_send"
    sent = "sent"  # terminal
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
        SignalState.suppressed_deprioritized,
    },
    SignalState.qualified: {
        SignalState.APPROVED,
        SignalState.REJECTED_AT_GATE_1,
        SignalState.SNOOZED,
        SignalState.ARCHIVED,
        # Re-evaluation demotion: a re-score that fails the fit bar returns the
        # signal to pending_qualification so it can re-qualify when rulesets improve.
        # This is lossless — qualification_json is preserved; only status changes.
        SignalState.pending_qualification,
    },
    SignalState.SNOOZED: {SignalState.qualified},
    SignalState.rejected_hard_filter: set(),
    SignalState.suppressed_stale: set(),
    SignalState.suppressed_deprioritized: set(),  # terminal — H2 drift fix
    SignalState.APPROVED: set(),
    SignalState.REJECTED_AT_GATE_1: set(),
    SignalState.ARCHIVED: set(),
}

BRIEF_TRANSITIONS: dict[BriefState, set[BriefState]] = {
    BriefState.created: {BriefState.in_inbox},
    BriefState.in_inbox: {
        BriefState.approved,
        BriefState.rejected,
        BriefState.snoozed,
        BriefState.asked,
        BriefState.monitoring,
        BriefState.changes_requested,
    },
    BriefState.approved: set(),
    BriefState.rejected: set(),
    BriefState.snoozed: {BriefState.in_inbox},
    BriefState.asked: {BriefState.in_inbox},
    BriefState.monitoring: {BriefState.in_inbox},
    BriefState.changes_requested: {BriefState.in_inbox},
}

WORKSPACE_TRANSITIONS: dict[WorkspaceState, set[WorkspaceState]] = {
    WorkspaceState.pending_content: {WorkspaceState.in_content_preparation},
    WorkspaceState.in_content_preparation: {
        WorkspaceState.sent_to_writing_studio,
        WorkspaceState.content_preparation_failed,
    },
    WorkspaceState.sent_to_writing_studio: {WorkspaceState.content_in_review},
    WorkspaceState.content_preparation_failed: set(),
    WorkspaceState.content_in_review: {
        WorkspaceState.all_content_approved,
        WorkspaceState.revision_needed,
    },
    WorkspaceState.all_content_approved: set(),
    WorkspaceState.revision_needed: {WorkspaceState.in_content_preparation},
}

DELIVERABLE_TRANSITIONS: dict[DeliverableState, set[DeliverableState]] = {
    DeliverableState.queued: {DeliverableState.generating},
    DeliverableState.generating: {DeliverableState.draft_ready, DeliverableState.generation_failed},
    DeliverableState.draft_ready: {
        DeliverableState.approved,
        DeliverableState.revised,
        DeliverableState.rejected,
    },
    # approved is no longer terminal — enqueue_send_for_deliverable transitions to
    # queued_for_send when contacts are available (or leaves at approved when skipped).
    DeliverableState.approved: {DeliverableState.queued_for_send},
    DeliverableState.queued_for_send: {DeliverableState.sent},
    DeliverableState.sent: set(),  # terminal
    DeliverableState.revised: {DeliverableState.generating},
    DeliverableState.rejected: {DeliverableState.draft_ready},  # revision after gate-2 rejection
    DeliverableState.generation_failed: set(),
}

# ── Legacy-value mapping table (M3a) ─────────────────────────────────────────
# Single source of truth for converting pre-M3 status strings to enum members.
# (entity_type, legacy_value) → new enum member.
LEGACY_STATUS_MAP: dict[tuple[str, str], enum.Enum] = {
    ("deliverable", "ready_for_review"): DeliverableState.draft_ready,
    ("deliverable", "rejected_at_gate_2"): DeliverableState.rejected,
    ("deliverable", "review_pending"): DeliverableState.draft_ready,
    ("workspace", "content_in_progress"): WorkspaceState.in_content_preparation,
    ("workspace", "content_in_review"): WorkspaceState.content_in_review,
    ("workspace", "all_content_approved"): WorkspaceState.all_content_approved,
    ("workspace", "revision_needed"): WorkspaceState.revision_needed,
    ("workspace", "created"): WorkspaceState.pending_content,
    ("brief", "monitoring"): BriefState.monitoring,
    ("brief", "changes_requested"): BriefState.changes_requested,
    ("signal", "approved"): SignalState.APPROVED,
    ("signal", "rejected"): SignalState.REJECTED_AT_GATE_1,
    ("signal", "snoozed"): SignalState.SNOOZED,
    ("signal", "archived"): SignalState.ARCHIVED,
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
