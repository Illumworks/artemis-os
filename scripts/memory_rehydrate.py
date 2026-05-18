"""Restore archived raw_inputs payloads from cold storage back into Postgres.

Usage:
    python -m scripts.memory_rehydrate --ids 123 456 789
    python -m scripts.memory_rehydrate --ids 123 --archive-dir /path/to/archive

Exit codes:
    0  success
    1  unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def _main(row_ids: list[int], archive_dir: Path) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from artemis.config import settings
    from artemis.db import attach_pgvector_codec
    from artemis.memory.archive import rehydrate

    engine = create_async_engine(settings.db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        count = await rehydrate(session, row_ids, archive_dir=archive_dir)

    await engine.dispose()
    print(f"Rehydrated {count} of {len(row_ids)} requested rows.")
    return 0


def main() -> None:
    from artemis.config import settings

    parser = argparse.ArgumentParser(description="Restore archived raw_inputs payloads.")
    parser.add_argument("--ids", type=int, nargs="+", required=True, help="Row IDs to rehydrate.")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=settings.archive_dir,
        help="Root archive directory (default: %(default)s).",
    )
    args = parser.parse_args()

    try:
        code = asyncio.run(_main(args.ids, args.archive_dir))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1

    sys.exit(code)


if __name__ == "__main__":
    main()
