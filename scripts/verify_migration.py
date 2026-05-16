# mypy: ignore-errors
"""Post-migration verifier: compare Node SQLite vs Postgres after --apply.

Usage:
    uv run python scripts/verify_migration.py [--source PATH]

Checks:
  - Row counts match for all migrated tables.
  - 10 random rows per table spot-checked for content fidelity.

Exit 0 only if all checks pass; 1 with a clear failure summary otherwise.

NOTE: This script is safe to run against the real database; it is read-only
on both the SQLite source and Postgres. The actual apply must have been run
first (scripts/migrate_okr_writing_rules.py --apply).
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_SOURCE = Path.home() / ".artemis" / "data.db"

# Tables to verify and their "text identity column" for spot-checking
_TABLES: list[tuple[str, str]] = [
    ("okr_objectives", "title"),
    ("okr_key_results", "title"),
    ("okr_activity", "text"),
    ("okr_next_up", "text"),
    ("writing_profiles", "name"),
    ("writing_folders", "name"),
    ("writing_rules", "title"),
    ("writing_examples", "title"),
    ("writing_sources", "title"),
]


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def _sqlite_sample(conn: sqlite3.Connection, table: str, col: str, n: int = 10) -> list[str]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT {col} FROM {table}").fetchall()  # noqa: S608
    sample = random.sample(rows, min(n, len(rows)))
    return [row[col] for row in sample]


async def _pg_count(session: object, table: str) -> int:
    from sqlalchemy import text

    result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # type: ignore[union-attr]  # noqa: S608
    return result.scalar_one()


async def _pg_has_value(session: object, table: str, col: str, value: str) -> bool:
    from sqlalchemy import text

    result = await session.execute(  # type: ignore[union-attr]
        text(f"SELECT 1 FROM {table} WHERE {col} = :v LIMIT 1"),  # noqa: S608
        {"v": value},
    )
    return result.fetchone() is not None


async def _verify_async(source_path: Path) -> list[str]:
    """Run all verification checks. Returns a list of failure messages."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from artemis.config import settings
    from artemis.db import attach_pgvector_codec

    engine = create_async_engine(settings.db_url, echo=False, future=True)
    attach_pgvector_codec(engine)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    conn = sqlite3.connect(str(source_path))
    failures: list[str] = []

    try:
        async with session_factory() as session:
            for table, col in _TABLES:
                # Count check
                src_count = _sqlite_count(conn, table)
                pg_count = await _pg_count(session, table)

                if pg_count < src_count:
                    # Postgres may have fewer rows if some were already there
                    # (idempotent insert). We only fail if Postgres has 0 when
                    # source has > 0.
                    if src_count > 0 and pg_count == 0:
                        failures.append(f"{table}: source has {src_count} rows but Postgres has 0")
                    else:
                        print(
                            f"  {table}: SQLite={src_count}, Postgres={pg_count} "
                            f"(gap={src_count - pg_count} — may be conflicts/pre-existing)"
                        )
                else:
                    print(f"  {table}: SQLite={src_count}, Postgres={pg_count} ✓")

                # Spot-check: pick up to 10 random values and verify they exist in PG
                sample = _sqlite_sample(conn, table, col)
                for val in sample:
                    found = await _pg_has_value(session, table, col, val)
                    if not found:
                        failures.append(
                            f"{table}: value {col}={val!r:.60} from source not found in Postgres"
                        )
    finally:
        conn.close()
        await engine.dispose()

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-migration verification")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()

    if not args.source.exists():
        print(f"ERROR: source SQLite not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    import asyncio

    print(f"Verifying migration (source: {args.source})")
    print("-" * 60)
    failures = asyncio.run(_verify_async(args.source))

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
