"""NCES district CSV loader machinery.

This module intentionally does not source the real NCES file. It provides the
loader contract plus test-fixture support for a small sample CSV.

After a successful bulk load the loader upserts a row in ``district_data_meta``
(the DIST5 singleton) so the Signal Playbook panel can display freshness state.
The caller must pass ``school_year`` (e.g. "2024-25") so the stamp is accurate.
Derive it from the ``--year`` CLI arg: ``f"{year}-{str(year+1)[2:]}"``.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import DistrictDataMeta
from artemis.marketing.repository import upsert_district

LOGGER = logging.getLogger(__name__)
EXPECTED_COLUMNS = ["nces_id", "name", "state", "enrollment"]

_DEFAULT_SOURCE = "NCES CCD via Urban Institute Education Data API"


async def load_districts_from_csv(
    session: AsyncSession,
    csv_path: Path,
    *,
    school_year: str | None = None,
    source: str = _DEFAULT_SOURCE,
) -> dict[str, int]:
    """Bulk-ingest NCES district reference data from a streamed CSV file.

    Args:
        session:     SQLAlchemy async session (caller owns commit).
        csv_path:    Path to the NCES CSV (columns: nces_id, name, state, enrollment).
        school_year: School-year string stamped in district_data_meta
                     (e.g. "2024-25"). Derive from ``--year N`` as
                     ``f"{N}-{str(N+1)[2:]}"``. If omitted, meta is not stamped
                     and the caller is responsible for updating it.
        source:      Provenance label stored in district_data_meta.

    Returns:
        dict with "loaded" and "skipped" counts.
    """

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

    # Stamp district_data_meta after a successful load (if school_year provided).
    if school_year is not None:
        await _upsert_district_data_meta(
            session,
            school_year=school_year,
            source=source,
            row_count=loaded,
        )

    return {"loaded": loaded, "skipped": skipped}


async def _upsert_district_data_meta(
    session: AsyncSession,
    *,
    school_year: str,
    source: str,
    row_count: int,
) -> None:
    """Upsert the singleton district_data_meta row."""
    now = datetime.now(tz=UTC)
    result = await session.execute(select(DistrictDataMeta).order_by(DistrictDataMeta.id).limit(1))
    existing = result.scalar_one_or_none()

    if existing is None:
        session.add(
            DistrictDataMeta(
                source=source,
                school_year=school_year,
                loaded_at=now,
                row_count=row_count,
                updated_at=now,
            )
        )
    else:
        existing.source = source
        existing.school_year = school_year
        existing.loaded_at = now
        existing.row_count = row_count
        existing.updated_at = now

    await session.flush()
