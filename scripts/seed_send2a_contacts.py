"""Seed script: create district contacts for SEND2-A verification.

Looks up the resolved_district_id for campaign_candidates 5, 7, 8 via their
linked signal_queue rows. For each resolved district, upserts 1-2 plausible
manual contacts (Curriculum Director, Chief Academic Officer).

Idempotent: re-running reactivates existing rows instead of creating duplicates.

Usage:
    uv run python scripts/seed_send2a_contacts.py

Environment:
    ARTEMIS_DB_URL  — defaults to the value in .env (artemis_os DB).
    Pass ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test
    to target the test database instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Candidate IDs to seed contacts for
_CANDIDATE_IDS = [5, 7, 8]

# Role templates: (title, first_name, last_name)
_CONTACT_TEMPLATES = [
    ("Curriculum Director", "alex", "johnson"),
    ("Chief Academic Officer", "morgan", "smith"),
]


def _district_slug(district_name: str) -> str:
    """Turn a district name into a short email-safe slug."""
    slug = district_name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:40]


async def main() -> None:
    from sqlalchemy import select

    import artemis.marketing.models  # noqa: F401 — registers models on metadata
    import artemis.pipelines.models  # noqa: F401 — pipeline_runs FK dep
    from artemis.db import SessionLocal
    from artemis.marketing.contacts import create_contact
    from artemis.marketing.models import CampaignCandidate, District, SignalQueue

    summary: list[dict] = []

    async with SessionLocal() as session:
        async with session.begin():
            for candidate_id in _CANDIDATE_IDS:
                candidate = await session.get(CampaignCandidate, candidate_id)
                if candidate is None:
                    logger.warning("candidate_id=%d not found — skipping", candidate_id)
                    summary.append(
                        {"candidate_id": candidate_id, "district_id": None, "contacts_seeded": 0}
                    )
                    continue

                # Resolve district via source_signal → signal_queue → resolved_district_id
                resolved_district_id: int | None = None
                if candidate.source_signal_id is not None:
                    signal = await session.get(SignalQueue, candidate.source_signal_id)
                    if signal is not None:
                        resolved_district_id = signal.resolved_district_id

                if resolved_district_id is None:
                    # Fallback: scan all signals for this candidate's district text id
                    if candidate.source_signal_id is not None:
                        signal = await session.get(SignalQueue, candidate.source_signal_id)
                        if signal is not None and signal.district_id is not None:
                            stmt = select(SignalQueue.resolved_district_id).where(
                                SignalQueue.district_id == signal.district_id,
                                SignalQueue.resolved_district_id.isnot(None),
                            )
                            resolved_district_id = (
                                await session.execute(stmt)
                            ).scalar_one_or_none()

                if resolved_district_id is None:
                    logger.warning(
                        "candidate_id=%d has no resolved_district_id — skipping",
                        candidate_id,
                    )
                    summary.append(
                        {
                            "candidate_id": candidate_id,
                            "district_id": None,
                            "contacts_seeded": 0,
                        }
                    )
                    continue

                district = await session.get(District, resolved_district_id)
                district_name = district.name if district else f"district-{resolved_district_id}"
                slug = _district_slug(district_name)

                contacts_seeded = 0
                for title, first, last in _CONTACT_TEMPLATES:
                    email = f"{first}.{last}@{slug}.example"
                    name = f"{first.capitalize()} {last.capitalize()}"
                    try:
                        contact = await create_contact(
                            session,
                            district_id=resolved_district_id,
                            name=name,
                            email=email,
                            title=title,
                            source="manual",
                        )
                        logger.info(
                            "candidate_id=%d district_id=%d upserted contact_id=%d %s <%s>",
                            candidate_id,
                            resolved_district_id,
                            contact.id,
                            name,
                            email,
                        )
                        contacts_seeded += 1
                    except ValueError as exc:
                        logger.warning(
                            "candidate_id=%d district_id=%d skipped contact: %s",
                            candidate_id,
                            resolved_district_id,
                            exc,
                        )

                summary.append(
                    {
                        "candidate_id": candidate_id,
                        "district_id": resolved_district_id,
                        "contacts_seeded": contacts_seeded,
                    }
                )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
