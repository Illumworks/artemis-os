"""One-time historical backfill from agent_runs, floating_artemis_messages, workflow_runs.

Usage:
    uv run python -m artemis.costs.backfill --dry-run   # preview counts, no writes
    uv run python -m artemis.costs.backfill             # write rows to cost_events

Idempotent: rows with the same (source_kind, source_id) are skipped.
DO NOT run this against the dev DB manually — Lead runs it post-merge as part of
the post-merge smoke procedure. Run against artemis_test_cost_p1 for acceptance testing.

Source mapping:
  agent_runs     → feature_tag='agent_run',         source_kind='agent_run'
  fa_messages    → feature_tag='floating_artemis',   source_kind='fa_message'
  workflow_runs  → feature_tag='workflow',           source_kind='workflow_run'
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from artemis.costs.events import record_cost_event

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# Default anthropic model when agents table join is unavailable.
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"


async def _backfill_agent_runs(session: AsyncSession, dry_run: bool) -> int:
    """Backfill cost_events from agent_runs.

    Joins agents to get the model. provider_path='api' (conservative default —
    we can't distinguish CLI vs API historically).
    """
    rows = await session.execute(
        text("""
            SELECT
                ar.id,
                ar.started_at,
                ar.cost_input_tokens,
                ar.cost_output_tokens,
                ar.error,
                a.model,
                a.id AS agent_db_id
            FROM agent_runs ar
            LEFT JOIN agents a ON a.agent_id = ar.agent_id
            WHERE ar.cost_input_tokens > 0 OR ar.cost_output_tokens > 0
            ORDER BY ar.id
        """)
    )
    records = rows.fetchall()
    count = 0
    for row in records:
        source_id = str(row[0])
        # Check idempotency
        existing = await session.execute(
            text(
                "SELECT 1 FROM cost_events "
                "WHERE source_kind = 'agent_run' AND source_id = :sid LIMIT 1"
            ),
            {"sid": source_id},
        )
        if existing.fetchone():
            continue
        if dry_run:
            count += 1
            continue

        model = row[5] or _DEFAULT_ANTHROPIC_MODEL
        await record_cost_event(
            session,
            provider="anthropic",
            model=model,
            provider_path="api",
            feature_tag="agent_run",
            input_tokens=int(row[2] or 0),
            output_tokens=int(row[3] or 0),
            source_kind="agent_run",
            source_id=source_id,
            agent_id=int(row[6]) if row[6] is not None else None,
            is_error=bool(row[4]),
            error_kind="backfill_error" if row[4] else None,
        )
        count += 1

    return count


async def _backfill_fa_messages(session: AsyncSession, dry_run: bool) -> int:
    """Backfill cost_events from floating_artemis_messages (assistant role only)."""
    rows = await session.execute(
        text("""
            SELECT
                fam.id,
                fam.created_at,
                fam.cost_input_tokens,
                fam.cost_output_tokens,
                fam.cache_creation_input_tokens,
                fam.cache_read_input_tokens,
                fam.session_id,
                fas.provider,
                fas.model
            FROM floating_artemis_messages fam
            JOIN floating_artemis_sessions fas ON fas.session_id = fam.session_id
            WHERE fam.role = 'assistant'
              AND (fam.cost_input_tokens > 0 OR fam.cost_output_tokens > 0)
            ORDER BY fam.id
        """)
    )
    records = rows.fetchall()
    count = 0
    for row in records:
        source_id = str(row[0])
        existing = await session.execute(
            text(
                "SELECT 1 FROM cost_events "
                "WHERE source_kind = 'fa_message' AND source_id = :sid LIMIT 1"
            ),
            {"sid": source_id},
        )
        if existing.fetchone():
            continue
        if dry_run:
            count += 1
            continue

        provider = row[7] or "anthropic"
        model = row[8] or _DEFAULT_ANTHROPIC_MODEL
        await record_cost_event(
            session,
            provider=provider,
            model=model,
            provider_path="api",
            feature_tag="floating_artemis",
            input_tokens=int(row[2] or 0),
            output_tokens=int(row[3] or 0),
            cache_creation_input_tokens=int(row[4] or 0),
            cache_read_input_tokens=int(row[5] or 0),
            source_kind="fa_message",
            source_id=source_id,
            session_id=str(row[6]),
        )
        count += 1

    return count


async def _backfill_workflow_runs(session: AsyncSession, dry_run: bool) -> int:
    """Backfill cost_events from workflow_runs.

    We only have total_cost_usd historically — no token breakdown. Write a
    pre-aggregated row with token counts = 0 and cost_usd = total_cost_usd.
    error_kind='backfill_lossy' flags these rows as lacking token detail.
    """
    rows = await session.execute(
        text("""
            SELECT id, started_at, total_cost_usd
            FROM workflow_runs
            WHERE total_cost_usd > 0
            ORDER BY id
        """)
    )
    records = rows.fetchall()
    count = 0
    for row in records:
        source_id = str(row[0])
        existing = await session.execute(
            text(
                "SELECT 1 FROM cost_events "
                "WHERE source_kind = 'workflow_run' AND source_id = :sid LIMIT 1"
            ),
            {"sid": source_id},
        )
        if existing.fetchone():
            continue
        if dry_run:
            count += 1
            continue

        # Write a pre-aggregated lossy row. Rates are 0 since we only have the
        # total; we inject the cost_usd directly via a post-flush UPDATE.
        # We use a sentinel rates trick: write 0 tokens + override cost_usd.
        # Simpler: insert with 0 tokens and rates, then set cost_usd directly.
        from artemis.costs.models import CostEvent

        event = CostEvent(
            provider="anthropic",
            model=_DEFAULT_ANTHROPIC_MODEL,
            provider_path="api",
            feature_tag="workflow",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            input_rate_per_million=0.0,
            output_rate_per_million=0.0,
            cache_write_rate_per_million=0.0,
            cache_read_rate_per_million=0.0,
            cost_usd=float(row[2]),
            source_kind="workflow_run",
            source_id=source_id,
            workflow_run_id=int(row[0]),
            error_kind="backfill_lossy",
        )
        session.add(event)
        await session.flush()
        count += 1

    return count


async def run_backfill(*, dry_run: bool = False) -> None:
    """Main backfill entry point."""
    db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL")
    if not db_url:
        logger.error("ARTEMIS_TEST_DB_URL or ARTEMIS_DB_URL must be set")
        sys.exit(1)

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info("=== Backfill starting (%s) ===", mode)

    async with session_factory() as session:
        ar_count = await _backfill_agent_runs(session, dry_run)
        fa_count = await _backfill_fa_messages(session, dry_run)
        wf_count = await _backfill_workflow_runs(session, dry_run)

        if not dry_run:
            await session.commit()

    logger.info("--- Source counts (%s) ---", mode)
    logger.info("  agent_runs        → %d new rows", ar_count)
    logger.info("  floating_artemis  → %d new rows", fa_count)
    logger.info("  workflow_runs     → %d new rows", wf_count)
    logger.info("  TOTAL             → %d new rows", ar_count + fa_count + wf_count)

    if not dry_run:
        # Report final total in the table.
        async with session_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM cost_events"))
            total = result.scalar_one()
        logger.info("cost_events table now has %d total rows", total)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill cost_events from historical tables.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing any rows.",
    )
    args = parser.parse_args()
    asyncio.run(run_backfill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
