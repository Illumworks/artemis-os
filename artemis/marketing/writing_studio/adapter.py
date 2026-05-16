"""Writing Studio Adapter — Component 5.3 (Python port).

Port of writing-studio-adapter.js.

Subscribes to Writing Studio draft lifecycle events and maintains
campaign_deliverables state. This is the ONLY Marketing OS component
that touches the Writing Studio internal event interface.

Guardrails:
  - Never modifies draft content.
  - Tracks deliverable state only.
  - No external HTTP webhooks.
  - All errors swallowed/logged; subscriber failures never interrupt draft ops.

Call init_adapter() once at app startup (lifespan).
Call reset_adapter() in test cleanup.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── State transitions per event type ──────────────────────────────────────────

# Maps event type → target deliverable status.
# None means the event is informational (no state change).
_EVENT_TRANSITIONS: dict[str, str | None] = {
    "draft.generated": "ready_for_review",
    "draft.approved": "approved",
    "draft.rejected": "rejected_at_gate_2",
    "draft.revised": None,  # handled conditionally (same as regenerated)
    "draft.regenerated": None,  # handled conditionally
    "draft.edited": None,  # informational only
}

# ── Init guard ─────────────────────────────────────────────────────────────────

_initialized: bool = False
_unsubscribe_fn: Any = None


def init_adapter() -> None:
    """Subscribe the adapter to draft events. Safe to call multiple times (idempotent)."""
    global _initialized, _unsubscribe_fn
    if _initialized:
        return
    from artemis.marketing.writing_studio.events import subscribe

    _unsubscribe_fn = subscribe(_handle_draft_event)
    _initialized = True


def reset_adapter() -> None:
    """Tear down subscription — for test cleanup only."""
    global _initialized, _unsubscribe_fn
    if _unsubscribe_fn is not None:
        _unsubscribe_fn()
        _unsubscribe_fn = None
    _initialized = False


# ── Workspace state computation ────────────────────────────────────────────────


async def _recompute_workspace_state(
    session: Any,
    candidate_id: int,
) -> None:
    """Recompute and persist the campaign candidate's workspace_state.

    Mirrors Node's computeWorkspaceStateFromDeliverables + updateCampaignWorkspaceState.
    Rules:
      - Any deliverable rejected_at_gate_2 → 'revision_needed'
      - All deliverables approved → 'all_content_approved'
      - Any ready_for_review → 'content_in_review'
      - Otherwise → 'content_in_progress'
    """
    from sqlalchemy import select

    from artemis.marketing.models import CampaignCandidate, CampaignDeliverable

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.candidate_id == candidate_id)
    )
    deliverables = list(result.scalars().all())

    if not deliverables:
        return

    statuses = [d.status for d in deliverables]

    if any(s == "rejected_at_gate_2" for s in statuses):
        workspace_state = "revision_needed"
    elif all(s == "approved" for s in statuses):
        workspace_state = "all_content_approved"
    elif any(s == "ready_for_review" for s in statuses):
        workspace_state = "content_in_review"
    else:
        workspace_state = "content_in_progress"

    candidate = await session.get(CampaignCandidate, candidate_id)
    if candidate is not None:
        candidate.workspace_state = workspace_state
        from datetime import UTC, datetime

        candidate.updated_at = datetime.now(tz=UTC)
        await session.flush()


# ── Deliverable state update ───────────────────────────────────────────────────


async def _apply_deliverable_transition(
    session: Any,
    deliverable_id_str: str,
    event_type: str,
) -> tuple[int | None, int | None]:
    """Apply the state transition to a deliverable.

    Returns (deliverable.id, deliverable.candidate_id) or (None, None).
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from artemis.marketing.models import CampaignDeliverable

    try:
        deliverable_id = int(deliverable_id_str)
    except (TypeError, ValueError):
        return None, None

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.id == deliverable_id)
    )
    deliverable = result.scalar_one_or_none()
    if deliverable is None:
        return None, None

    target_state = _resolve_target_state(event_type, deliverable.status)
    if target_state is None:
        return deliverable.id, deliverable.candidate_id

    deliverable.status = target_state
    deliverable.updated_at = datetime.now(tz=UTC)
    await session.flush()
    return deliverable.id, deliverable.candidate_id


def _resolve_target_state(event_type: str, current_state: str) -> str | None:
    """Return the target deliverable status for the given event, or None for no-op."""
    if event_type == "draft.edited":
        return None

    if event_type in ("draft.revised", "draft.regenerated"):
        # Only meaningful if currently rejected or still generating
        if current_state in ("rejected_at_gate_2", "generating"):
            return "ready_for_review"
        return None

    return _EVENT_TRANSITIONS.get(event_type)


# ── Handler ────────────────────────────────────────────────────────────────────


async def _handle_draft_event(event: Any) -> None:
    """Process a draft lifecycle event and update campaign_deliverables state.

    Errors are always swallowed — subscriber failures must never interrupt draft ops.
    """
    try:
        await _process_event(event)
    except Exception as exc:
        logger.warning("[writing-studio-adapter] unhandled error: %s", exc)


async def _process_event(event: Any) -> None:
    """Core event processing — requires a DB session from app context.

    In production: uses the async session factory from artemis.db.
    Falls back gracefully if no DB is available (e.g., unit tests without DB).
    """
    if event.type not in _EVENT_TRANSITIONS:
        return

    deliverable_id_str = event.deliverable_id
    if not deliverable_id_str:
        return

    try:
        from artemis.db import SessionLocal

        async with SessionLocal() as session:
            _, candidate_id = await _apply_deliverable_transition(
                session, deliverable_id_str, event.type
            )
            if candidate_id is not None:
                await _recompute_workspace_state(session, candidate_id)
            await session.commit()
    except ImportError:
        # DB not available in lightweight test contexts
        pass
    except Exception as exc:
        logger.warning("[writing-studio-adapter] DB update failed: %s", exc)


# ── Helper for tests that inject a session directly ───────────────────────────


async def process_event_with_session(session: Any, event: Any) -> None:
    """Process a draft event using a caller-supplied session.

    Used by tests that control the DB session directly.
    Does NOT commit — caller owns the transaction.
    """
    if event.type not in _EVENT_TRANSITIONS:
        return

    deliverable_id_str = event.deliverable_id
    if not deliverable_id_str:
        return

    _, candidate_id = await _apply_deliverable_transition(session, deliverable_id_str, event.type)
    if candidate_id is not None:
        await _recompute_workspace_state(session, candidate_id)
