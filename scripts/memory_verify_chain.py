"""Verify the raw_inputs hash chain.

Usage:
    python -m scripts.memory_verify_chain [--scope-kind KIND --scope-id ID]

Exit codes:
    0  chain intact (or empty)
    1  chain break detected
    2  unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _main(scope_kind: str | None, scope_id: str | None) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from artemis.config import settings
    from artemis.db import attach_pgvector_codec
    from artemis.memory.hashchain import verify_chain

    engine = create_async_engine(settings.db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await verify_chain(session, scope_kind=scope_kind, scope_id=scope_id)

    await engine.dispose()

    if result.ok:
        print(f"Chain OK — {result.row_count} rows verified. {result.message}")
        return 0
    else:
        print(
            f"CHAIN BREAK — first_break_id={result.first_break_id}. {result.message}",
            file=sys.stderr,
        )
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify raw_inputs hash chain integrity.")
    parser.add_argument("--scope-kind", default=None, help="Restrict to this scope_kind.")
    parser.add_argument("--scope-id", default=None, help="Restrict to this scope_id.")
    args = parser.parse_args()

    try:
        code = asyncio.run(_main(args.scope_kind, args.scope_id))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 2

    sys.exit(code)


if __name__ == "__main__":
    main()
