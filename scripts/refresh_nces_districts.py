#!/usr/bin/env python3
"""Refresh NCES district reference data from the Urban Institute Education Data API.

Pulls the CCD LEA directory (which includes total enrollment) for a given school
year and writes a trimmed CSV (nces_id,name,state,enrollment) consumable by
``artemis.marketing.nces_loader.load_districts_from_csv``.

WHY this source: NCES publishes the Common Core of Data (CCD) annually, but the
raw files are large fixed-width/long-format dumps. The Urban Institute Education
Data Portal mirrors the same CCD data behind a clean REST API, so this script
stays small and stdlib-only (no new dependencies).
  https://educationdata.urban.org/  (CCD LEA directory endpoint)

FRESHNESS / WHEN TO UPDATE:
  - CCD is an ANNUAL collection. The Urban API ``year`` = fall of the school year
    (year=2024 => 2024-25 school year).
  - NCES releases the PRELIMINARY directory in spring after the school year, and
    enrollment ("membership") fills in over the following months. As of 2026-05,
    year=2024 (2024-25) enrollment is ~99% populated.
  - Re-run this script once a year, around late spring / early summer, when a new
    ``year`` appears in the API with populated enrollment. Bump --year, re-run,
    re-load, then call repository.recompute_all_tiers() (or the DIST2 recompute
    button) so existing district rows pick up any enrollment changes.
  - Lossless: load_districts_from_csv UPSERTS by nces_id; it never deletes. A
    district that closed simply stops being updated; its row persists.

USAGE:
  uv run python scripts/refresh_nces_districts.py --year 2024 \
      --out artemis/marketing/data/nces_districts.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request

API = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/{year}/?limit=10000"

# CCD agency_type: 1 = regular local school district, 2 = component district of a
# supervisory union. These are the real "districts" a marketing signal references.
# (7 = charter agency, 3 = supervisory union, 4 = regional service agency, etc. —
# excluded for v1 to keep the name-matching universe clean. Revisit if signals
# start referencing charter networks.)
KEEP_AGENCY_TYPES = {1, 2}


def fetch_all(year: int) -> list[dict]:
    url: str | None = API.format(year=year)
    rows: list[dict] = []
    while url:
        with urllib.request.urlopen(url, timeout=180) as resp:  # noqa: S310 (trusted host)
            data = json.load(resp)
        rows.extend(data["results"])
        print(f"  fetched {len(rows)} records ...", file=sys.stderr)
        url = data.get("next")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024, help="Urban API year (fall of school year)")
    ap.add_argument("--out", default="artemis/marketing/data/nces_districts.csv")
    args = ap.parse_args()

    sy = f"{args.year}-{str(args.year + 1)[2:]}"
    print(f"Pulling CCD LEA directory for {args.year} ({sy} school year)...", file=sys.stderr)
    rows = fetch_all(args.year)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    kept = skipped = 0
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["nces_id", "name", "state", "enrollment"])
        for r in rows:
            enr = r.get("enrollment")
            if (
                r.get("agency_type") not in KEEP_AGENCY_TYPES
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

    print(f"Wrote {kept} districts to {args.out} (skipped {skipped}). Source year {args.year} ({sy}).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
