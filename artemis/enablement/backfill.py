"""Batch-draft catalog summaries.

    uv run python -m artemis.enablement.backfill --limit 10          # dry run
    uv run python -m artemis.enablement.backfill --limit 10 --write

Drafts land as ``ai_draft`` and wait for Sara or Missy in the review queue
(``GET /api/enablement/review``). Nothing here can mark anything verified.

Deliberately opt-in per batch rather than a scheduled job: 416 provider calls is
real money, and a bad prompt change should be caught on ten rows, not four
hundred. Run it, read the drafts, then widen.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import or_, select

import artemis.db as _db
from artemis.enablement.enrichment import (
    STATUS_AI_DRAFT,
    AssetFacts,
    apply_enrichment,
    generate_enrichment,
    reembed,
)
from artemis.enablement.models import EnablementAsset

_logger = logging.getLogger(__name__)


async def backfill(*, limit: int, write: bool, redraft: bool = False) -> int:
    """Draft summaries for assets that have none. Returns the number drafted."""
    drafted = 0
    async with _db.SessionLocal() as session:
        stmt = select(EnablementAsset).where(
            EnablementAsset.status.is_distinct_from("archived"),
        )
        if redraft:
            stmt = stmt.where(EnablementAsset.summary_status == STATUS_AI_DRAFT)
        else:
            # Never overwrite a human-reviewed summary, and never re-draft one
            # that is already waiting in the queue.
            stmt = stmt.where(
                or_(EnablementAsset.summary.is_(None), EnablementAsset.summary == "")
            ).where(EnablementAsset.summary_status.is_(None))
        stmt = stmt.order_by(EnablementAsset.id).limit(limit)

        assets = list((await session.execute(stmt)).scalars().all())
        print(f"{len(assets)} asset(s) to draft (write={write})\n")

        for asset in assets:
            facts = AssetFacts.from_row(asset)
            enrichment = await generate_enrichment(
                facts, feedback=asset.summary_feedback, session=session
            )
            label = (asset.title or asset.asset_name or asset.drive_file_id)[:58]
            if enrichment is None:
                print(f"  SKIP  {label}\n        (no usable draft; left untouched)")
                continue

            drafted += 1
            print(f"  DRAFT {label}")
            print(f"        {enrichment.summary}")
            extras = [
                f"{name}={value}"
                for name, value in (
                    ("audience", enrichment.audience),
                    ("format", enrichment.format),
                    ("grade", enrichment.grade_range),
                )
                if value
            ]
            if extras:
                print(f"        {'  '.join(extras)}")

            if write:
                apply_enrichment(asset, enrichment)
                # Re-embed AFTER applying, so the new summary is in the vector.
                # Without this the summary only ever reaches keyword search.
                if not await reembed(asset):
                    print("        (warning: re-embed failed; vector left stale)")

        if write:
            await session.commit()
            print(f"\ncommitted {drafted} draft(s) as '{STATUS_AI_DRAFT}'")
        else:
            print(f"\ndry run: nothing written. {drafted} draft(s) would land as ai_draft")

    return drafted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="How many assets to draft.")
    parser.add_argument("--write", action="store_true", help="Persist. Default is a dry run.")
    parser.add_argument(
        "--redraft",
        action="store_true",
        help="Re-draft existing ai_draft rows instead of empty ones (after a prompt change).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(backfill(limit=args.limit, write=args.write, redraft=args.redraft))


if __name__ == "__main__":
    main()
