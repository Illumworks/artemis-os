"""Shared mutation site for ``deliverable_metadata.live_content``.

There are exactly two writers of the autosave/live buffer:
  1. The HTTP ``PUT /drafts/{id}`` handler (Phase 2 soft-lock autosave).
  2. The collab room flush (Phase 3) — the single writer while clients are
     connected over the WebSocket.

Both go through :func:`apply_live_content` so the JSONB mutation (set text,
stamp updated-at, bump the ``live_content_version`` compare-and-set counter)
lives in ONE place.  :func:`set_live_content` wraps it with the session
fetch+commit the collab room needs (the room has no request session).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignDeliverable


def apply_live_content(meta: dict[str, Any], text: str) -> int:
    """Mutate *meta* in place to persist *text* as the live buffer.

    Sets ``live_content`` + ``live_content_updated_at`` and bumps
    ``live_content_version`` (the Phase 2 compare-and-set counter). Returns the
    new version. The caller is responsible for re-assigning the JSONB column so
    SQLAlchemy detects the change (``deliverable.deliverable_metadata = meta``).
    """
    current = int(meta.get("live_content_version", 0))
    meta["live_content"] = text
    meta["live_content_updated_at"] = datetime.now(UTC).isoformat()
    meta["live_content_version"] = current + 1
    return current + 1


async def get_live_text(session: AsyncSession, draft_id: int) -> str:
    """Read a draft's current body using the canonical precedence rule.

    ``live_content`` (autosave/live buffer) wins over ``versions[0].content``;
    empty string if neither exists. Mirrors
    ``compose_engine._latest_draft_content`` so the collab room hydrates its
    baseline to exactly what every other reader sees.
    """
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        return ""
    meta: dict[str, Any] = dict(deliverable.deliverable_metadata or {})
    live = meta.get("live_content")
    if isinstance(live, str) and live:
        return live
    versions = meta.get("versions", [])
    if versions and isinstance(versions[0], dict):
        return str(versions[0].get("content", ""))
    return ""


async def set_live_content(session: AsyncSession, draft_id: int, text: str) -> int | None:
    """Persist *text* as the draft's live buffer; return the new version (or None).

    Used by the collab room to flush the converged materialized text. Returns
    ``None`` if the draft no longer exists. Commits its own transaction —
    callers pass a fresh session (the room opens one per flush).
    """
    deliverable = await session.get(CampaignDeliverable, draft_id, with_for_update=True)
    if deliverable is None:
        return None
    # Re-assign the whole dict (not in-place mutate) so SQLAlchemy's change
    # detection fires on the JSONB column — same pattern as the PUT handler.
    meta: dict[str, Any] = dict(deliverable.deliverable_metadata or {})
    new_version = apply_live_content(meta, text)
    deliverable.deliverable_metadata = meta
    deliverable.updated_at = datetime.now(UTC)
    await session.commit()
    return new_version
