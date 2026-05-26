"""Seed loader for Josh's canonical reason-code registry.

One-shot, idempotent: uses INSERT … ON CONFLICT (code) DO NOTHING so re-running
is a no-op. Returns counts for diagnostics.

Usage (one-liner):
    uv run python -c "
    import asyncio
    from artemis.marketing.seeds.reason_codes import run_seed
    asyncio.run(run_seed())
    "
"""

from __future__ import annotations

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import parse_spec

JOSH_SPEC_V1: list[dict[str, str]] = [
    {
        "code": row.code,
        "domain": row.domain,
        "description": row.description,
        "what_scout_looks_for": row.what_scout_looks_for,
        "default_urgency": row.default_urgency,
    }
    for row in parse_spec().reason_codes
]


async def seed_reason_codes(session: AsyncSession) -> dict[str, int]:
    """Idempotent insert of all Josh spec reason codes.

    Uses INSERT … ON CONFLICT (code) DO NOTHING — safe to re-run.
    Returns {"inserted": N, "skipped": K}.
    """
    inserted = 0
    skipped = 0
    for row in parse_spec().reason_codes:
        cursor: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
            text(
                "INSERT INTO signal_reason_codes "
                "(code, domain, description, what_scout_looks_for, default_urgency, is_active) "
                "VALUES (:code, :domain, :description, :what_scout_looks_for, :default_urgency, true) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": row.code,
                "domain": row.domain,
                "description": row.description,
                "what_scout_looks_for": row.what_scout_looks_for,
                "default_urgency": row.default_urgency,
            },
        )
        if cursor.rowcount == 1:
            inserted += 1
        else:
            skipped += 1
    await session.commit()
    return {"inserted": inserted, "skipped": skipped}


async def run_seed() -> dict[str, int]:
    """Entry point for CLI one-liner."""
    from artemis.db import SessionLocal

    async with SessionLocal() as session:
        return await seed_reason_codes(session)
