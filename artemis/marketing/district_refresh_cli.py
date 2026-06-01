"""Standalone entry point for refreshing NCES district data (#100 / DIST5).

Three steps, run sequentially:
  1. Pull a fresh CCD LEA directory from the Urban Institute Education Data
     Portal and write the CSV (same logic as ``scripts/refresh_nces_districts.py``).
  2. Bulk-upsert the CSV into the ``districts`` table via
     ``load_districts_from_csv``.
  3. Recompute every district's tier + supported flag against the current
     ``district_tier_bands`` config.

Invoked as a subprocess from the DIST5 "Refresh district data" button so
the long network + CPU work runs out-of-process (same #103 / #102
pattern) and a partial / failed refresh is non-destructive — the loader
upserts and never deletes, so nothing is lost when the network is flaky.

Usage:
    python -m artemis.marketing.district_refresh_cli [--year YEAR] [--csv PATH]

Exit codes: 0 on a clean refresh, non-zero on any exception. A one-line
JSON summary is printed to stdout for the dispatcher to log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default CSV path matches the script + the live load.
_DEFAULT_CSV = Path("artemis/marketing/data/nces_districts.csv")

# Default to the latest CCD year we have data for (matches refresh script
# default). Bump when a newer school year ships.
_DEFAULT_YEAR = 2024


def _school_year(year: int) -> str:
    return f"{year}-{str(year + 1)[2:]}"


async def _refresh(year: int, csv_path: Path) -> dict[str, Any]:
    """Pull CSV → load → recompute. Returns a summary dict."""
    # Lazy imports so ``--help`` doesn't pay the full DB import cost.
    from artemis.db import SessionLocal
    from artemis.marketing.nces_loader import load_districts_from_csv
    from artemis.marketing.repository import recompute_all_tiers
    from scripts import refresh_nces_districts as fetch_script

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: pull fresh CSV. Reuses the existing script — invoked
    # via its main() so we inherit its argparse defaults and logging.
    # We can't call main() directly because it parses sys.argv; instead,
    # use the underlying helpers.
    logger.info("refresh: pulling CCD year=%d → %s", year, csv_path)
    rows = fetch_script.fetch_all(year)
    kept = skipped = 0
    import csv as _csv

    with csv_path.open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["nces_id", "name", "state", "enrollment"])
        for r in rows:
            enr = r.get("enrollment")
            if (
                r.get("agency_type") not in fetch_script.KEEP_AGENCY_TYPES
                or not isinstance(enr, int)
                or enr < 0
            ):
                skipped += 1
                continue
            leaid = str(r.get("leaid") or "").strip()
            name = (r.get("lea_name") or "").strip()
            state = (r.get("state_location") or "").strip().upper()
            if not leaid or not name:
                skipped += 1
                continue
            w.writerow([leaid, name, state, enr])
            kept += 1

    logger.info("refresh: wrote %d rows (skipped %d) to %s", kept, skipped, csv_path)

    # Step 2 + 3: load + recompute, both in the same DB session so a
    # failed recompute rolls back the load.
    sy = _school_year(year)
    async with SessionLocal() as session:
        load_result = await load_districts_from_csv(session, csv_path, school_year=sy)
        recomputed = await recompute_all_tiers(session)
        await session.commit()

    return {
        "year": year,
        "school_year": sy,
        "csv_kept": kept,
        "csv_skipped": skipped,
        "loaded": load_result.get("loaded", 0),
        "load_skipped": load_result.get("skipped", 0),
        "recomputed": recomputed,
    }


async def _amain(year: int, csv_path: Path) -> int:
    try:
        result = await _refresh(year, csv_path)
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        logger.exception("district_refresh_cli: refresh failed")
        print(
            json.dumps({"status": "failed", "year": year, "error": repr(exc)}),
            flush=True,
        )
        return 1
    print(json.dumps({"status": "ok", **result}), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m artemis.marketing.district_refresh_cli",
        description="Refresh NCES district data: pull CSV, load, recompute tiers.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=_DEFAULT_YEAR,
        help=f"Urban API year — fall of the school year (default: {_DEFAULT_YEAR}).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=_DEFAULT_CSV,
        help=f"CSV output path (default: {_DEFAULT_CSV}).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging to stderr.",
    )
    ns = parser.parse_args(argv)
    if ns.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    return asyncio.run(_amain(ns.year, ns.csv))


MODULE_NAME = "artemis.marketing.district_refresh_cli"


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    sys.exit(main())
