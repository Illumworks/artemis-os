"""Backfill qualification for existing pending_qualification signals.

Idempotent: only updates rows where signal_status = 'pending_qualification'.
Lossless: never deletes rows; only updates qualification_json + signal_status.

Usage:
    uv run python scripts/backfill_qualify_pending.py [--dry-run] [--limit N]

Exit codes:
    0 — success (including zero rows to process)
    1 — fatal error
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make the project root importable when run as `python scripts/backfill_qualify_pending.py`
# (sys.path[0] is the scripts/ dir, not the repo root, so `import artemis` would fail).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import artemis.marketing.models  # noqa: F401 — register models on Base.metadata
import artemis.pipelines.models  # noqa: F401
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.marketing.models import SignalQueue
from artemis.marketing.qualification import run_and_store_qualification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("backfill_qualify")


async def _run(dry_run: bool, limit: int | None) -> int:
    """Return number of rows processed."""
    db_url = str(settings.db_url)
    engine = create_async_engine(db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)

    processed = 0
    qualified = 0
    skipped_no_ruleset = 0
    errors = 0

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            stmt = select(SignalQueue).where(SignalQueue.signal_status == "pending_qualification")
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            log.info("Found %d pending_qualification signals to backfill", len(rows))

            if dry_run:
                log.info("DRY-RUN mode — no changes written")
                return len(rows)

            for row in rows:
                processed += 1
                try:
                    qual_dict = await run_and_store_qualification(session, row)
                    if qual_dict is None:
                        log.info(
                            "signal id=%s — no active rulesets, skipped",
                            row.id,
                        )
                        skipped_no_ruleset += 1
                    else:
                        await session.commit()
                        await session.refresh(row)
                        qualified += 1
                        log.info(
                            "signal id=%s → %s (qualified)",
                            row.id,
                            row.signal_status,
                        )
                except Exception:
                    log.exception("signal id=%s — qualification error (non-fatal)", row.id)
                    errors += 1
                    await session.rollback()
    finally:
        await engine.dispose()

    log.info(
        "Backfill complete: processed=%d qualified=%d skipped_no_ruleset=%d errors=%d",
        processed,
        qualified,
        skipped_no_ruleset,
        errors,
    )
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count pending rows without writing changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap number of rows to process (default: all)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(dry_run=args.dry_run, limit=args.limit))
    except Exception:
        log.exception("Fatal error during backfill")
        sys.exit(1)


if __name__ == "__main__":
    main()
