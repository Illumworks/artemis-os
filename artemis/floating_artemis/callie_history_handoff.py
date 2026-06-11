"""C3c: Retire the Artemis-DM marketing history into Callie's memory scope.

This module ingests the ~246-message retired Artemis DM session
(``slack-T4MNZ8CCV-D0AN8CCJC4C-_``, tagged ``callie_handoff_pending=true``)
into ``agent:callie`` as memory observations, then marks the handoff complete.

Design:
- Lossless: the original Artemis session and its messages are NEVER modified.
  The messages are read, their text is extracted, and new observations are
  written into ``agent:callie`` scope.
- Idempotent: ``write_observation`` deduplicates by content hash within scope,
  so re-running the handoff is safe and produces no duplicates.
- Step 3 updates ``callie_handoff_pending=false`` + sets
  ``handed_to_callie_at`` on the session metadata so the flag is cleared once
  and won't re-trigger.

Usage (one-off — called at startup or via management command)::

    from artemis.floating_artemis.callie_history_handoff import run_handoff
    await run_handoff()
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.floating_artemis.models import FloatingArtemisSession
from artemis.memory.schemas import Scope, SourceQualityHint
from artemis.memory.store import write_observation

logger = logging.getLogger(__name__)

# The retired Artemis DM session that carries the marketing history
RETIRED_SESSION_ID = "slack-T4MNZ8CCV-D0AN8CCJC4C-_"

# Callie's memory scope — the target for all ingested observations
_CALLIE_SCOPE = Scope(scope_kind="agent", scope_id="callie")

# Category applied to every ingested observation
_HANDOFF_CATEGORY = "discovery"

# Source label for provenance
_HANDOFF_SOURCE_KIND = "floating_artemis_messages"


def _extract_text_from_content(content: Any) -> str | None:
    """Return the plain-text representation of a message content block list.

    Handles both raw-text strings and the standard ``[{"type": "text", "text":
    "…"}]`` block list format used by FloatingArtemisMessage.  Returns ``None``
    when no usable text can be extracted (e.g. pure tool calls).
    """
    if isinstance(content, str):
        stripped = content.strip()
        return stripped if stripped else None

    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "text")
        if btype == "text":
            text = (block.get("text") or "").strip()
            if text:
                parts.append(text)

    return "\n".join(parts) if parts else None


def _build_observation_content(
    role: str,
    text: str,
    msg_id: int | None = None,
    created_at: datetime | None = None,
) -> str:
    """Format a single message as an observation string.

    Format: ``[ROLE] <text>`` (optionally prefixed with an ISO timestamp).
    """
    role_tag = role.upper()
    lines: list[str] = []
    if created_at is not None:
        ts = created_at.isoformat()
        lines.append(f"[{ts}] [{role_tag}] {text}")
    else:
        lines.append(f"[{role_tag}] {text}")
    return "\n".join(lines)


async def ingest_session_messages(
    session: AsyncSession,
    *,
    fa_session_id: str = RETIRED_SESSION_ID,
    batch_size: int = 50,
) -> int:
    """Read all messages from the retired session and write them to Callie's scope.

    Returns the number of new observations written (0 if already done / empty).

    Args:
        session: Active async DB session (caller owns transaction).
        fa_session_id: The floating_artemis_sessions.session_id to ingest.
        batch_size: Messages are processed in batches to avoid large IN clauses.
    """
    from sqlalchemy import select

    from artemis.floating_artemis.models import FloatingArtemisMessage

    # Load all messages for this session, ordered chronologically
    result = await session.execute(
        select(FloatingArtemisMessage)
        .where(FloatingArtemisMessage.session_id == fa_session_id)
        .order_by(FloatingArtemisMessage.id.asc())
    )
    messages = list(result.scalars().all())

    if not messages:
        logger.info(
            "callie_history_handoff: no messages found for session %s",
            fa_session_id,
        )
        return 0

    logger.info(
        "callie_history_handoff: ingesting %d messages from %s into agent:callie",
        len(messages),
        fa_session_id,
    )

    written = 0
    for msg in messages:
        text = _extract_text_from_content(msg.content)
        if not text:
            # Skip tool-only messages (no usable text)
            continue

        observation_content = _build_observation_content(
            role=msg.role,
            text=text,
            msg_id=msg.id,
            created_at=getattr(msg, "created_at", None),
        )

        await write_observation(
            session,
            scope=_CALLIE_SCOPE,
            content=observation_content,
            category=_HANDOFF_CATEGORY,
            source_quality=SourceQualityHint.agent,
            raw_payload={
                "session_id": fa_session_id,
                "msg_id": msg.id,
                "role": msg.role,
                "text": text,
            },
            raw_source_kind=_HANDOFF_SOURCE_KIND,
            raw_source_id=str(msg.id),
            raw_actor="callie_history_handoff",
        )
        written += 1

    logger.info(
        "callie_history_handoff: wrote %d observations into agent:callie",
        written,
    )
    return written


async def mark_handoff_complete(
    session: AsyncSession,
    *,
    fa_session_id: str = RETIRED_SESSION_ID,
    timestamp: datetime | None = None,
) -> bool:
    """Update the session metadata: clear callie_handoff_pending, set handed_to_callie_at.

    Idempotent: if the session doesn't exist or handoff is already marked, logs
    a warning and returns False instead of raising.

    Returns True when the update succeeds.
    """
    from sqlalchemy import select

    now = timestamp or datetime.now(UTC)

    result = await session.execute(
        select(FloatingArtemisSession).where(
            FloatingArtemisSession.session_id == fa_session_id
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        logger.warning(
            "callie_history_handoff: session %s not found — cannot mark handoff complete",
            fa_session_id,
        )
        return False

    existing_meta: dict[str, Any] = {}
    if isinstance(row.metadata_, dict):
        existing_meta = dict(row.metadata_)

    new_meta = {
        **existing_meta,
        "callie_handoff_pending": False,
        "handed_to_callie_at": now.isoformat(),
    }

    await session.execute(
        update(FloatingArtemisSession)
        .where(FloatingArtemisSession.session_id == fa_session_id)
        .values(metadata_=new_meta)
    )
    logger.info(
        "callie_history_handoff: session %s marked complete (handed_to_callie_at=%s)",
        fa_session_id,
        now.isoformat(),
    )
    return True


async def run_handoff(
    *,
    fa_session_id: str = RETIRED_SESSION_ID,
    force: bool = False,
) -> dict[str, Any]:
    """One-shot handoff runner: ingest messages then mark complete.

    Checks ``callie_handoff_pending`` in session metadata before running.
    If the flag is already false (handoff already done), returns early unless
    ``force=True``.

    Returns a result dict with keys:
    - ``skipped`` (bool): True when already done and force=False.
    - ``observations_written`` (int): number of new observations written.
    - ``handoff_marked`` (bool): True when metadata was updated.
    - ``error`` (str | None): error message if something failed.
    """
    import artemis.db as _db

    try:
        # Use two separate sessions: a read-only check first, then a write session.
        # This avoids "transaction already begun" errors that arise when mixing
        # an autobegin SELECT with an explicit session.begin() call on the same session.
        async with _db.SessionLocal() as read_session:
            from sqlalchemy import select

            result = await read_session.execute(
                select(FloatingArtemisSession).where(
                    FloatingArtemisSession.session_id == fa_session_id
                )
            )
            row = result.scalar_one_or_none()

            if row is not None and not force:
                meta = row.metadata_ if isinstance(row.metadata_, dict) else {}
                if not meta.get("callie_handoff_pending", True):
                    logger.info(
                        "callie_history_handoff: already complete for %s — skipping",
                        fa_session_id,
                    )
                    return {
                        "skipped": True,
                        "observations_written": 0,
                        "handoff_marked": False,
                        "error": None,
                    }

        # Session not found or handoff pending — run the write in a fresh session.
        async with _db.SessionLocal() as write_session, write_session.begin():
            observations_written = await ingest_session_messages(
                write_session,
                fa_session_id=fa_session_id,
            )
            handoff_marked = await mark_handoff_complete(
                write_session,
                fa_session_id=fa_session_id,
            )

    except Exception as exc:
        logger.exception("callie_history_handoff: run_handoff failed")
        return {
            "skipped": False,
            "observations_written": 0,
            "handoff_marked": False,
            "error": str(exc),
        }

    return {
        "skipped": False,
        "observations_written": observations_written,
        "handoff_marked": handoff_marked,
        "error": None,
    }
