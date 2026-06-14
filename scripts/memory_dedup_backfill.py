"""M1b backfill — collapse existing near-duplicate (clone) observations losslessly.

Runs ``consolidate_near_duplicates`` over an existing corpus. The faucet that
created the clones (the Callie history handoff embedding a timestamp in content)
is fixed at the source in ``callie_history_handoff.py``; this script cleans up the
clutter already written.

LOSSLESS: never deletes. Each duplicate is superseded (``superseded_by`` →
canonical) and linked back as evidence; superseded rows stay fully recoverable.

Dry-run by default — prints the plan without writing. Pass --apply to commit.

    uv run python scripts/memory_dedup_backfill.py                 # dry-run, all scopes
    uv run python scripts/memory_dedup_backfill.py --scope-kind agent --scope-id callie
    uv run python scripts/memory_dedup_backfill.py --apply
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.memory.hashchain import verify_chain
from artemis.memory.models import MemoryObservation
from artemis.memory.near_duplicate import (
    apply_clone_consolidation,
    plan_clone_clusters,
)
from artemis.memory.schemas import Observation


async def _run(db_url: str, scope_kind: str | None, scope_id: str | None, apply: bool) -> None:
    engine = create_async_engine(db_url, pool_pre_ping=True)
    attach_pgvector_codec(engine)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            stmt = select(MemoryObservation).where(MemoryObservation.superseded_by.is_(None))
            if scope_kind is not None:
                stmt = stmt.where(MemoryObservation.scope_kind == scope_kind)
            if scope_id is not None:
                stmt = stmt.where(MemoryObservation.scope_id == scope_id)
            rows = list((await session.execute(stmt)).scalars())
            observations = [Observation.model_validate(r) for r in rows]
            clusters = plan_clone_clusters(observations)
            dups = sum(len(c.duplicate_ids) for c in clusters)

            print(f"active observations scanned: {len(observations)}")
            print(f"clone clusters: {len(clusters)} | observations to supersede: {dups}")
            for c in sorted(clusters, key=lambda c: -c.size)[:15]:
                print(
                    f"  size={c.size:3d} {c.scope_kind}:{c.scope_id} canon={c.canonical_id} "
                    f":: {c.normalized[:60]!r}"
                )

            if not apply:
                print("\nDRY-RUN — no changes written. Pass --apply to commit.")
                return

            before_total = (
                await session.execute(text("SELECT count(*) FROM memory_observations"))
            ).scalar_one()
            # The session already auto-began a transaction from the SELECTs above, so
            # do the writes in it directly (no session.begin()), VERIFY the lossless
            # invariant while still uncommitted, and roll back if it's violated —
            # only commit a clean result.
            stats = await apply_clone_consolidation(session, clusters)
            after_total = (
                await session.execute(text("SELECT count(*) FROM memory_observations"))
            ).scalar_one()
            chain = await verify_chain(session)
            if after_total != before_total or not chain.ok:
                await session.rollback()
                raise SystemExit("LOSSLESS INVARIANT VIOLATED — rolled back, nothing committed.")
            await session.commit()

            print(f"\nAPPLIED: {stats}")
            print(
                f"total observation rows: {before_total} -> {after_total} "
                f"(delta {after_total - before_total}; must be 0 — lossless)"
            )
            print(f"hashchain ok: {chain.ok} ({chain.row_count} rows)")
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="Lossless near-duplicate consolidation backfill.")
    p.add_argument("--db-url", default=settings.db_url, help="Override ARTEMIS_DB_URL.")
    p.add_argument("--scope-kind", default=None)
    p.add_argument("--scope-id", default=None)
    p.add_argument("--apply", action="store_true", help="Commit changes (default: dry-run).")
    args = p.parse_args()
    asyncio.run(_run(args.db_url, args.scope_kind, args.scope_id, args.apply))


if __name__ == "__main__":
    main()
