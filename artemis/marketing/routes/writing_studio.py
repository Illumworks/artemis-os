"""Writing Studio router — /api/writing-studio.

Endpoints:
  POST /drafts                          — create draft from candidate
  POST /drafts/{draft_id}/submit-review — submit draft for Gate-2 review
  POST /drafts/{draft_id}/events/{event_kind} — webhook from Writing Studio
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found
from artemis.marketing.writing_studio import events as ws_events
from artemis.marketing.writing_studio import invoke as ws_invoke

router = APIRouter(
    prefix="/api/writing-studio",
    tags=["writing-studio"],
    dependencies=[Depends(require_token)],
)

# Valid event kinds that the external Writing Studio may post back.
_VALID_EVENT_KINDS = frozenset(["approved", "rejected", "revised"])

# Map short event kind → full DRAFT_EVENT_TYPES key
_EVENT_KIND_MAP: dict[str, str] = {
    "approved": "draft.approved",
    "rejected": "draft.rejected",
    "revised": "draft.revised",
}


@router.post("/drafts", status_code=201)
async def create_draft(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a Writing Studio draft from a campaign candidate.

    Body:
      candidate_id: int          (required)
      asset_context: dict | None (optional — pre-assembled asset bundle)
    """
    candidate_id = body.get("candidate_id")
    if candidate_id is None:
        raise bad_request("candidate_id is required", "missing_candidate_id")
    try:
        candidate_id = int(candidate_id)
    except (TypeError, ValueError):
        raise bad_request("candidate_id must be an integer", "invalid_candidate_id")  # noqa: B904

    asset_context = body.get("asset_context")
    asset_context_bundle: list[dict[str, Any]] | None = None
    if asset_context is not None:
        if isinstance(asset_context, list):
            asset_context_bundle = asset_context
        elif isinstance(asset_context, dict):
            # Accept a single asset dict wrapped in a list
            asset_context_bundle = [asset_context]

    try:
        draft = await ws_invoke.create_draft_from_candidate(
            session,
            candidate_id=candidate_id,
            asset_context_bundle=asset_context_bundle,
        )
    except ValueError as exc:
        raise not_found(str(exc), "candidate_not_found")  # noqa: B904

    return _serialize_draft(draft)


@router.post("/drafts/{draft_id}/submit-review", status_code=200)
async def submit_review(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Submit a draft for Gate-2 review.

    draft_id is the local campaign_deliverables.id (integer PK).
    """
    try:
        record = await ws_invoke.submit_draft_for_review(session, deliverable_id=draft_id)
    except ValueError as exc:
        raise not_found(str(exc), "draft_not_found")  # noqa: B904

    return _serialize_approval(record)


@router.post("/drafts/{draft_id}/events/{event_kind}", status_code=200)
async def post_draft_event(
    draft_id: str = Path(...),
    event_kind: str = Path(...),
) -> dict[str, Any]:
    """Receive a lifecycle event webhook from the external Writing Studio.

    Valid event_kind values: approved, rejected, revised.
    draft_id is the external draft id (string — e.g. 'stub-draft-1').
    """
    if event_kind not in _VALID_EVENT_KINDS:
        raise bad_request(
            f"Invalid event_kind {event_kind!r}. Valid: {sorted(_VALID_EVENT_KINDS)}",
            "invalid_event_kind",
        )

    event_type = _EVENT_KIND_MAP[event_kind]
    event = await ws_events.publish(event_type, draft_id=draft_id)

    return {
        "ok": True,
        "eventKind": event_kind,
        "draftId": draft_id,
        "eventId": event.event_id if event else None,
    }


# ── Serializers ───────────────────────────────────────────────────────────────


def _serialize_draft(draft: ws_invoke.Draft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "externalId": draft.external_id,
        "candidateId": draft.candidate_id,
        "title": draft.title,
        "status": draft.status,
        "briefText": draft.brief_text,
        "assetContextBundle": draft.asset_context_bundle,
        "metadata": draft.metadata,
        "createdAt": draft.created_at.isoformat() if draft.created_at else None,
    }


def _serialize_approval(record: ws_invoke.ApprovalRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "subjectId": record.subject_id,
        "status": record.status,
        "externalApprovalId": record.external_approval_id,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
    }
