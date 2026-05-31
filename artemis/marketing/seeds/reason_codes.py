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

import sqlalchemy as sa
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import parse_spec

_SPEC = parse_spec()
_FAMILIES_BY_CODE: dict[str, list[str]] = {}
for mapping in _SPEC.campaign_type_mappings:
    for code in mapping.reason_codes:
        _FAMILIES_BY_CODE.setdefault(code, []).append(mapping.campaign_type)

JOSH_SPEC_V1: list[dict[str, str]] = [
    {
        "code": row.code,
        "domain": row.domain,
        "description": row.description,
        "what_scout_looks_for": row.what_scout_looks_for,
        "default_urgency": row.default_urgency,
    }
    for row in _SPEC.reason_codes
]


async def seed_reason_codes(session: AsyncSession) -> dict[str, int]:
    """Idempotent insert of all Josh spec reason codes.

    Uses INSERT … ON CONFLICT (code) DO NOTHING — safe to re-run.
    Returns {"inserted": N, "skipped": K}.
    """
    inserted = 0
    skipped = 0
    for row in _SPEC.reason_codes:
        cursor: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
            text(
                "INSERT INTO signal_reason_codes "
                "(code, domain, description, what_scout_looks_for, default_urgency, "
                "primary_scouts, campaign_families, is_active) "
                "VALUES (:code, :domain, :description, :what_scout_looks_for, :default_urgency, "
                ":primary_scouts, :campaign_families, true) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(
                sa.bindparam("primary_scouts", type_=sa.ARRAY(sa.Text())),
                sa.bindparam("campaign_families", type_=sa.ARRAY(sa.Text())),
            ),
            {
                "code": row.code,
                "domain": row.domain,
                "description": row.description,
                "what_scout_looks_for": row.what_scout_looks_for,
                "default_urgency": row.default_urgency,
                "primary_scouts": list(row.primary_scouts),
                "campaign_families": _FAMILIES_BY_CODE.get(row.code, []),
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
