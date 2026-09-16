"""Run a Champions ingest by hand.

    uv run python -m artemis.champions              # incremental, classify
    uv run python -m artemis.champions --full       # re-read the whole community
    uv run python -m artemis.champions --no-classify  # store only, no model calls
    uv run python -m artemis.champions --classify-only  # label what is already stored

Phase 1 sends nothing. There is no flag here that would email or post.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from artemis.champions.ingest import classify_pending, ingest
from artemis.db import SessionLocal, engine


async def _run(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        if args.classify_only:
            classified, flagged, escalated, errors = await classify_pending(
                session, limit=args.limit
            )
            await session.commit()
            print(
                f"classified {classified} | product issues {flagged} | "
                f"escalations {escalated} | errors {len(errors)}"
            )
        else:
            result = await ingest(
                session,
                full=args.full,
                classify=not args.no_classify,
                limit=args.limit,
            )
            await session.commit()
            print(
                f"run {result.run_id}: seen {result.items_seen} | new {result.items_new} | "
                f"classified {result.items_classified} | product issues {result.items_flagged} | "
                f"escalations {result.escalations} | errors {len(result.errors)}"
            )
            for err in result.errors[:5]:
                print(f"  ! {err}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Champions community ingest (Phase 1)")
    parser.add_argument("--full", action="store_true", help="re-read the whole community")
    parser.add_argument("--no-classify", action="store_true", help="store only, no model calls")
    parser.add_argument("--classify-only", action="store_true", help="label rows already stored")
    parser.add_argument("--limit", type=int, help="cap items processed (for a smoke run)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
