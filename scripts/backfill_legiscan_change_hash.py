"""One-off backfill: add change_hash to existing legiscan signal_queue rows.

Context
-------
The activity-aware dedup in signal_queue.write (a5050b6) only re-surfaces a bill
when BOTH the incoming payload AND the stored row carry a change_hash in provenance.
Rows written before that commit have no change_hash, so they can never trigger a
re-emit even when the bill advances.

This script sets each existing row's baseline change_hash to "as of now" so that
FUTURE scout runs can detect bill advancement via a differing hash. It deliberately
does NOT replay past changes — those bills are already captured.

Behaviour
---------
- Queries signal_queue for rows where source_url LIKE '%legiscan%' AND
  provenance->>'change_hash' IS NULL AND status NOT IN ('archived', 'rejected_hard_filter').
- For each row: parses (state, bill_number, year) from source_url, calls the
  LegiScan getSearch API (bill= parameter) to fetch the current change_hash.
- Merges change_hash into provenance ADDITIVELY — no other field is touched.
- Idempotent: rows that already have a change_hash are skipped.
- Skips and reports rows where the bill can't be resolved from the API.
- Respects the LegiScan free-tier rate limit (1 req/sec via ScoutHttpClient).

Usage
-----
    uv run python scripts/backfill_legiscan_change_hash.py

Requires LEGISCAN_API_KEY and ARTEMIS_DB_URL (or defaults from .env).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure the package is importable from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from artemis.db import SessionLocal  # noqa: E402  — needs sys.path first
from artemis.marketing.models import SignalQueue  # noqa: E402
from artemis.scouts._http import ScoutHttpClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_LEGISCAN_BASE = "https://api.legiscan.com/"
_EXCLUDE_STATUSES = ("archived", "rejected_hard_filter")


def _parse_url(source_url: str) -> tuple[str, str, int] | None:
    """Return (state, bill_number, year) parsed from a LegiScan URL.

    E.g. https://legiscan.com/TX/bill/HB123/2025 → ('TX', 'HB123', 2025)
    Returns None if the URL doesn't match the expected pattern.
    """
    m = re.search(r"legiscan\.com/([A-Z]{2})/bill/([^/]+)/(\d{4})", source_url, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper(), int(m.group(3))


async def _fetch_change_hash(
    http: ScoutHttpClient,
    api_key: str,
    state: str,
    bill_number: str,
    year: int,
    source_url: str,
) -> str | None:
    """Fetch the current change_hash for a bill from LegiScan.

    Uses getSearch with the 'bill' parameter (exact bill-number lookup).
    When multiple sessions match (e.g. TX HB123/2025 and TX HB123/2025/X1),
    we match the result whose 'url' matches the stored source_url.
    Falls back to the first result if no URL matches (unlikely but safe).

    Returns None on any error or if the bill is not found.
    """
    params: dict[str, Any] = {
        "key": api_key,
        "op": "getSearch",
        "state": state,
        "bill": bill_number,
        "year": year,
    }
    try:
        resp = await http.get(_LEGISCAN_BASE, params=params)
        if resp.status_code != 200:
            logger.warning("LegiScan HTTP %d for %s", resp.status_code, source_url)
            return None
        data: dict[str, Any] = resp.json()
        if data.get("status") != "OK":
            logger.warning("LegiScan status=%s for %s", data.get("status"), source_url)
            return None
        sr = data.get("searchresult", {})
        results = [v for k, v in sr.items() if k != "summary" and isinstance(v, dict)]
        if not results:
            logger.warning("No results from LegiScan for %s", source_url)
            return None
        # Prefer exact URL match.
        normalized_stored = source_url.rstrip("/").lower()
        for item in results:
            item_url = str(item.get("url", "")).rstrip("/").lower()
            if item_url == normalized_stored:
                return str(item["change_hash"])
        # Fall back to first result (same state+bill_number+year, first session).
        logger.info(
            "No exact URL match for %s — using first result (bill_number=%s)",
            source_url,
            results[0].get("bill_number"),
        )
        return str(results[0]["change_hash"])
    except Exception as exc:
        logger.warning("Error fetching change_hash for %s: %s", source_url, exc)
        return None


async def _run_backfill(session: AsyncSession, dry_run: bool = False) -> None:
    api_key = os.getenv("LEGISCAN_API_KEY", "")
    if not api_key:
        logger.error("LEGISCAN_API_KEY is not set — aborting.")
        return

    # Fetch all eligible rows.
    stmt = (
        select(SignalQueue.id, SignalQueue.source_url, SignalQueue.provenance)
        .where(
            SignalQueue.source_url.ilike("%legiscan%"),
            text("(provenance->>'change_hash') IS NULL"),
            SignalQueue.signal_status.notin_(list(_EXCLUDE_STATUSES)),
        )
        .order_by(SignalQueue.id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    logger.info("Found %d eligible rows to backfill.", len(rows))
    if not rows:
        return

    updated_count = 0
    skipped_count = 0
    before_after_samples: list[dict[str, Any]] = []

    async with ScoutHttpClient(rate_limit=1.0) as http:
        for row_id, source_url, provenance in rows:
            parsed = _parse_url(source_url or "")
            if not parsed:
                logger.warning("Row %s: could not parse URL %r — skipping.", row_id, source_url)
                skipped_count += 1
                continue

            state, bill_number, year = parsed
            change_hash = await _fetch_change_hash(http, api_key, state, bill_number, year, source_url)
            if change_hash is None:
                logger.warning("Row %s (%s): change_hash not found — skipping.", row_id, source_url)
                skipped_count += 1
                continue

            # Additive merge: copy provenance dict and add change_hash only.
            old_provenance = dict(provenance) if isinstance(provenance, dict) else {}
            new_provenance = {**old_provenance, "change_hash": change_hash}

            if len(before_after_samples) < 3:
                before_after_samples.append(
                    {
                        "row_id": row_id,
                        "source_url": source_url,
                        "before": old_provenance,
                        "after": new_provenance,
                    }
                )

            if not dry_run:
                # Use a raw UPDATE with JSONB concatenation operator (||) so
                # the merge is atomic and we never overwrite other provenance keys.
                # Note: asyncpg does not support the ::type cast syntax in text();
                # use CAST(:param AS jsonb) instead (per project feedback note).
                await session.execute(
                    text(
                        "UPDATE signal_queue"
                        " SET provenance = provenance || CAST(:patch AS jsonb)"
                        " WHERE id = :row_id"
                    ),
                    {"patch": json.dumps({"change_hash": change_hash}), "row_id": row_id},
                )

            logger.info(
                "Row %s (%s): change_hash=%s%s",
                row_id,
                source_url,
                change_hash,
                " [DRY RUN — not written]" if dry_run else "",
            )
            updated_count += 1

    if not dry_run:
        await session.commit()

    print("\n" + ("=" * 60))
    print(f"Backfill complete{'  [DRY RUN]' if dry_run else ''}:")
    print(f"  Updated:  {updated_count}")
    print(f"  Skipped:  {skipped_count}")
    print(f"  Total:    {len(rows)}")

    if before_after_samples:
        print("\nBefore/after samples (up to 3):")
        for sample in before_after_samples:
            print(f"\n  Row {sample['row_id']}  {sample['source_url']}")
            print(f"    BEFORE: {json.dumps(sample['before'])}")
            print(f"    AFTER:  {json.dumps(sample['after'])}")
    print("=" * 60)


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("DRY RUN mode — no DB writes will occur.")
    async with SessionLocal() as session:
        await _run_backfill(session, dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
