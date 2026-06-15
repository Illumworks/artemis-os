"""One-shot backfill for the 2pm Marketing Team Role / RACI Check In meeting.

granola_id: f956ecc0-edcf-4827-a5fa-896fe825d778

Bypasses the GCal-event matching step (calendar token was expired during the
summarizer window) and calls the lower-level summarize+persist path directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make the project root importable when run as `python scripts/backfill_meeting.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

GRANOLA_ID = "f956ecc0-edcf-4827-a5fa-896fe825d778"
TITLE = "Marketing Team Role / RACI Check In"


async def main() -> None:
    import artemis.db as _db
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.granola.client import GranolaClient
    from artemis.meetings.models import MeetingSummary
    from artemis.meetings.summarizer import _llm_summarize
    from artemis.memory.raw_inputs import insert_raw_input
    from artemis.proactivity.commitments import ingest_meeting_commitments
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with _db.SessionLocal() as session:
        # -- Idempotency check --------------------------------------------------
        existing = await session.execute(
            select(MeetingSummary).where(MeetingSummary.granola_id == GRANOLA_ID)
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            logger.info("Already summarized — granola_id=%s, id=%d", GRANOLA_ID, row.id)
            logger.info("Action items: %s", json.dumps(row.action_items, indent=2))
            return

        # -- Build Granola client -----------------------------------------------
        rows = await repo.list_active(session, provider="granola")
        if not rows:
            logger.error("Granola integration not connected")
            return
        creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
        granola = GranolaClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
            expires_at=float(str(creds.get("expires_at") or 0)),
        )

        # -- Fetch transcript ---------------------------------------------------
        logger.info("Fetching transcript for granola_id=%s …", GRANOLA_ID)
        transcript_data = await granola.get_meeting(GRANOLA_ID)
        if not transcript_data:
            logger.error("Granola returned empty transcript for %s", GRANOLA_ID)
            return

        transcript_len = len(str(transcript_data))
        logger.info("Transcript fetched — %d chars", transcript_len)

        # Extract plain-text transcript for storage.
        transcript_text: str | None = None
        if isinstance(transcript_data, dict):
            if "transcript" in transcript_data:
                transcript_text = str(transcript_data["transcript"])
            elif "notes" in transcript_data:
                transcript_text = str(transcript_data["notes"])
        if not transcript_text:
            transcript_text = None

        # -- LLM summarize -----------------------------------------------------
        logger.info("Calling LLM summarize …")
        summary_text, action_items = await _llm_summarize(TITLE, transcript_data)
        logger.info("Summary: %s", summary_text[:200])
        logger.info("Action items (%d):", len(action_items))
        for i, item in enumerate(action_items, 1):
            logger.info("  %d. %s", i, item)

        # -- Persist -----------------------------------------------------------
        payload: dict[str, Any] = {
            "granola_id": GRANOLA_ID,
            "gcal_event_id": None,
            "title": TITLE,
            "summary": summary_text,
            "action_items": action_items,
            "transcript_length": transcript_len,
        }

        async with session.begin_nested():
            raw = await insert_raw_input(
                session,
                source_kind="meeting_summary",
                source_id=GRANOLA_ID,
                actor="artemis-backfill",
                scope_kind="user",
                scope_id="jon",
                payload=payload,
            )

            stmt = (
                pg_insert(MeetingSummary.__table__)  # type: ignore[arg-type]
                .values(
                    granola_id=GRANOLA_ID,
                    gcal_event_id=None,
                    title=TITLE,
                    summary=summary_text,
                    action_items=action_items,
                    transcript=transcript_text,
                    raw_input_id=raw.id,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["granola_id"])
            )
            await session.execute(stmt)

            await ingest_meeting_commitments(
                session,
                granola_id=GRANOLA_ID,
                title=TITLE,
                action_items=action_items,
            )

        await session.commit()
        logger.info("Done — raw_input_id=%d", raw.id)

        # -- Verification query ------------------------------------------------
        result = await session.execute(
            select(MeetingSummary).where(MeetingSummary.granola_id == GRANOLA_ID)
        )
        saved = result.scalar_one_or_none()
        if saved:
            logger.info("Verified — meeting_summaries.id=%d", saved.id)
            logger.info("Stored action items:\n%s", json.dumps(saved.action_items, indent=2))
        else:
            logger.error("Row not found after commit — something went wrong")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=180))
