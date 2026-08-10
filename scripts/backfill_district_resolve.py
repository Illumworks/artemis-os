"""One-off backfill: resolve district names for qualified signal_queue rows.

Context
-------
The district name normaliser had two bugs that left ~103 qualified signals with
non-null district_id text but null resolved_district_id:

1. NCES-style alphanumeric district codes (e.g. "Salem-Keizer SD 24J") were not
   stripped because the trailing-number regex only matched pure digits.  A new
   regex now strips "SD <alphanum>", "USD <alphanum>", etc.

2. The " city schools" suffix was over-greedy, stripping the discriminating
   "city" component from names like "Orange City Schools", leaving "orange"
   instead of "orange city".  The suffix has been removed so " schools" strips
   last, leaving "orange city" to match DB "Orange City".

This script re-runs resolution over every signal_queue row that:
  - has non-null district_id (raw text)
  - has null resolved_district_id
  - signal_status NOT IN ('archived', 'rejected_hard_filter', 'suppressed_stale')

Where the FIXED resolver now finds a confident match, resolved_district_id is
set.  routing_status is intentionally NOT touched — Lead decides on recompute.

Behaviour
---------
- ADDITIVE ONLY: only fills NULL resolved_district_id.  No existing data changed.
- Idempotent: rows that already have resolved_district_id are skipped.
- Reports: rows recovered, samples, count of signals that would now be
  technically routable (resolved_district_id set) for Lead's reference.

Usage
-----
    uv run python scripts/backfill_district_resolve.py

Requires ARTEMIS_DB_URL (or defaults from .env).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select, update

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(["archived", "rejected_hard_filter", "suppressed_stale"])


async def run_backfill() -> None:
    # Import here so .env is loaded first
    import artemis.db as _db
    from artemis.marketing.models import District, SignalQueue
    from artemis.tools.district_resolve import resolve_district_from_list

    async with _db.SessionLocal() as session:
        # Load all districts once (same pattern as resolve_district())
        district_rows = (
            (await session.execute(select(District).order_by(District.id))).scalars().all()
        )
        districts = list(district_rows)
        logger.info("Loaded %d districts from DB", len(districts))

        # Fetch candidate signals: non-null district_id, null resolved_district_id,
        # not in terminal status.
        stmt = select(SignalQueue).where(
            SignalQueue.district_id.is_not(None),
            SignalQueue.resolved_district_id.is_(None),
            SignalQueue.signal_status.not_in(list(TERMINAL_STATUSES)),
        )
        result = await session.execute(stmt)
        candidates = result.scalars().all()

    logger.info("Candidate signals to backfill: %d", len(candidates))

    if not candidates:
        logger.info("Nothing to do.")
        return

    recovered: list[dict[str, object]] = []
    skipped_no_match = 0

    for signal in candidates:
        raw_name: str = signal.district_id or ""  # WHERE clause guarantees non-null
        if not raw_name:
            skipped_no_match += 1
            continue
        state = signal.state  # optional 2-char state hint

        res = resolve_district_from_list(raw_name, state, districts)
        if not res.matched:
            skipped_no_match += 1
            continue

        recovered.append(
            {
                "signal_id": signal.id,
                "headline": signal.headline,
                "district_raw": raw_name,
                "resolved_to": res.district_name,
                "resolved_id": res.district_id,
                "confidence": res.confidence,
                "method": res.match_method,
            }
        )

    logger.info(
        "Resolved %d / %d candidates (skipped no-match: %d)",
        len(recovered),
        len(candidates),
        skipped_no_match,
    )

    if not recovered:
        logger.info("No new resolutions — nothing to write.")
        return

    # --- Write phase (ADDITIVE only) ---
    async with _db.SessionLocal() as session, session.begin():
        for row in recovered:
            await session.execute(
                update(SignalQueue)
                .where(
                    SignalQueue.id == row["signal_id"],
                    # Safety guard: only fill actual NULLs — idempotent
                    SignalQueue.resolved_district_id.is_(None),
                )
                .values(resolved_district_id=row["resolved_id"])
            )

    logger.info("Wrote %d resolved_district_id values (routing_status untouched).", len(recovered))

    # --- Report ---
    print()
    print("=== Backfill complete ===")
    print(f"Candidates examined : {len(candidates)}")
    print(f"Newly resolved      : {len(recovered)}")
    print(f"No match (skipped)  : {skipped_no_match}")
    print()
    print("Samples (first 10):")
    for row in recovered[:10]:
        print(
            f"  signal {row['signal_id']:>6} | {row['district_raw']!r:35s} "
            f"-> district_id={row['resolved_id']} ({row['resolved_to']!r}) "
            f"conf={row['confidence']:.2f} via {row['method']}"
        )
    print()
    # Count how many of the recovered signals have signal_status that could
    # now be routable (i.e., resolved_district_id is now set — actual routing
    # also requires a district_contacts row, which this script does not check).
    would_be_routable_count = len(recovered)
    print(
        f"Signals newly having resolved_district_id (potentially routable, "
        f"subject to district_contacts check): {would_be_routable_count}"
    )
    print(
        "NOTE: routing_status was NOT changed. "
        "Lead should trigger a routing recompute to update routing_status."
    )


if __name__ == "__main__":
    try:
        asyncio.run(run_backfill())
    except KeyboardInterrupt:
        sys.exit(0)
