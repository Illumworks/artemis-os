"""Move raw_inputs rows older than the retention window to cold archive.

Usage:
    python -m scripts.memory_archive_cold [--age-days N] [--archive-dir PATH]

Defaults from ARTEMIS_ARCHIVE_AGE_DAYS and ARTEMIS_ARCHIVE_DIR.

Exit codes:
    0  success (may have archived 0 rows)
    1  unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def _main(age_days: int, archive_dir: Path) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from artemis.config import settings
    from artemis.db import attach_pgvector_codec
    from artemis.memory.archive import archive_cold

    engine = create_async_engine(settings.db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)

    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        count = await archive_cold(session, archive_age_days=age_days, archive_dir=archive_dir)

    await engine.dispose()
    print(f"Archived {count} rows to {archive_dir}")
    return 0


def main() -> None:
    from artemis.config import settings

    parser = argparse.ArgumentParser(description="Cold-archive old raw_inputs rows.")
    parser.add_argument(
        "--age-days",
        type=int,
        default=settings.archive_age_days,
        help="Archive rows older than N days (default: %(default)s).",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=settings.archive_dir,
        help="Root archive directory (default: %(default)s).",
    )
    args = parser.parse_args()

    try:
        code = asyncio.run(_main(args.age_days, args.archive_dir))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1

    sys.exit(code)


if __name__ == "__main__":
    main()
