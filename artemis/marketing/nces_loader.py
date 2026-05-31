"""NCES district CSV loader machinery.

This module intentionally does not source the real NCES file. It provides the
loader contract plus test-fixture support for a small sample CSV.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.repository import upsert_district

LOGGER = logging.getLogger(__name__)
EXPECTED_COLUMNS = ["nces_id", "name", "state", "enrollment"]


async def load_districts_from_csv(session: AsyncSession, csv_path: Path) -> dict[str, int]:
    """Bulk-ingest NCES district reference data from a streamed CSV file."""

    loaded = 0
    skipped = 0

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        found_columns = list(reader.fieldnames or [])
        if found_columns != EXPECTED_COLUMNS:
            raise ValueError(
                "NCES CSV header mismatch: expected columns "
                f"{EXPECTED_COLUMNS!r}, found {found_columns!r}"
            )

        for row in reader:
            enrollment_raw = (row.get("enrollment") or "").strip()
            try:
                enrollment = int(enrollment_raw)
            except (TypeError, ValueError):
                skipped += 1
                LOGGER.warning(
                    "Skipping NCES row %s because enrollment is blank or non-integer: %r",
                    reader.line_num,
                    row,
                )
                continue

            await upsert_district(
                session,
                nces_id=(row.get("nces_id") or "").strip() or None,
                name=(row.get("name") or "").strip(),
                state=(row.get("state") or "").strip() or None,
                enrollment=enrollment,
                source="nces",
            )
            loaded += 1

    return {"loaded": loaded, "skipped": skipped}
