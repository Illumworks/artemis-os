"""Cold-tier archive for raw_inputs rows older than the retention window.

Archive workflow:
  1. Find rows where created_at < now() - archive_age_days AND archived_at IS NULL.
  2. Group by month (year/month from created_at).
  3. Write gzipped JSONL to ~/.artemis/archive/{year}/{month}/raw_inputs-{date}.jsonl.gz
     atomically (write to .partial, then rename).
  4. NULL out payload on each archived row; set archived_at = now().
     The row stays in raw_inputs as a placeholder so the hash chain remains continuous.
  5. Verify the chain after archiving.

Rehydrate workflow:
  Given a row id (still present as placeholder), look up its year/month from created_at,
  search archive files in that directory for the row, restore payload.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.hashchain import payload_sha256, verify_chain

_logger = logging.getLogger(__name__)


def _default_archive_dir() -> Path:
    from artemis.config import settings

    return settings.archive_dir


def _archive_file_path(archive_dir: Path, year: int, month: int, run_date: str) -> Path:
    return archive_dir / str(year) / f"{month:02d}" / f"raw_inputs-{run_date}.jsonl.gz"


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "source_kind": row.source_kind,
        "source_id": row.source_id,
        "actor": row.actor,
        "scope_kind": row.scope_kind,
        "scope_id": row.scope_id,
        "payload": row.payload,
        "payload_hash": row.payload_hash,
        "prev_hash": row.prev_hash,
        "this_hash": row.this_hash,
    }


async def archive_cold(
    session: AsyncSession,
    *,
    archive_age_days: int = 90,
    archive_dir: Path | None = None,
    run_date: str | None = None,
) -> int:
    """Move payload of rows older than archive_age_days to gzipped JSONL files.

    Returns the number of rows archived. Idempotent: already-archived rows
    (archived_at IS NOT NULL) are skipped.

    The archive write is atomic per month: writes to .partial then renames.
    After all rows are written and DB is updated, the global hash chain is
    verified to confirm archiving did not break integrity.
    """
    from artemis.memory.raw_inputs import RawInput

    adir = archive_dir or _default_archive_dir()
    cutoff = datetime.now(UTC) - timedelta(days=archive_age_days)
    today = run_date or datetime.now(UTC).strftime("%Y-%m-%d")

    stmt = (
        select(RawInput)
        .where(RawInput.created_at < cutoff, RawInput.archived_at.is_(None))
        .order_by(RawInput.created_at, RawInput.id)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars())

    if not rows:
        _logger.info("archive_cold: no rows to archive")
        return 0

    # Group by (year, month)
    by_month: dict[tuple[int, int], list[Any]] = {}
    for row in rows:
        key = (row.created_at.year, row.created_at.month)
        by_month.setdefault(key, []).append(row)

    archived_ids: list[int] = []

    for (year, month), month_rows in by_month.items():
        out_path = _archive_file_path(adir, year, month, today)
        partial = out_path.with_suffix(".gz.partial")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Append mode: if a file already exists from a previous run today,
        # we append to it (read existing rows, merge, rewrite).
        existing: list[dict[str, Any]] = []
        if out_path.exists():
            with gzip.open(out_path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        existing.append(json.loads(line))

        existing_ids = {r["id"] for r in existing}
        new_rows = [_row_to_dict(r) for r in month_rows if r.id not in existing_ids]
        all_rows = existing + new_rows

        with gzip.open(partial, "wt", encoding="utf-8") as fh:
            for rec in all_rows:
                fh.write(json.dumps(rec, default=str) + "\n")

        partial.rename(out_path)
        _logger.info("archive_cold: wrote %d rows to %s", len(new_rows), out_path)
        archived_ids.extend(r.id for r in month_rows if r.id not in existing_ids)

    if archived_ids:
        await session.execute(
            update(RawInput)
            .where(RawInput.id.in_(archived_ids))
            .values(payload=None, archived_at=datetime.now(UTC))
        )
        await session.flush()

    chain = await verify_chain(session)
    if not chain.ok:
        _logger.error("archive_cold: chain verification failed after archive: %s", chain.message)

    return len(archived_ids)


async def rehydrate(
    session: AsyncSession,
    row_ids: list[int],
    *,
    archive_dir: Path | None = None,
) -> int:
    """Restore payload from archive files back into raw_inputs rows.

    Looks up each row's created_at to find the right archive directory,
    then searches all .jsonl.gz files in that month for the matching id.
    Verifies payload_hash after restoring to confirm integrity.

    Returns the number of rows successfully rehydrated.
    """
    from artemis.memory.raw_inputs import RawInput

    if not row_ids:
        return 0

    adir = archive_dir or _default_archive_dir()

    result = await session.execute(select(RawInput).where(RawInput.id.in_(row_ids)))
    db_rows = {row.id: row for row in result.scalars()}

    restored = 0
    for row_id in row_ids:
        row = db_rows.get(row_id)
        if row is None:
            _logger.warning("rehydrate: row id=%d not found in DB", row_id)
            continue
        if row.archived_at is None:
            _logger.debug("rehydrate: row id=%d not archived, skipping", row_id)
            continue
        if row.payload is not None:
            _logger.debug("rehydrate: row id=%d already has payload, skipping", row_id)
            continue

        year, month = row.created_at.year, row.created_at.month
        month_dir = adir / str(year) / f"{month:02d}"
        if not month_dir.exists():
            _logger.warning("rehydrate: archive dir %s not found for row id=%d", month_dir, row_id)
            continue

        payload: dict[str, Any] | None = None
        for archive_file in sorted(month_dir.glob("raw_inputs-*.jsonl.gz")):
            with gzip.open(archive_file, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("id") == row_id:
                        payload = rec.get("payload")
                        break
            if payload is not None:
                break

        if payload is None:
            _logger.warning("rehydrate: payload not found in archive for row id=%d", row_id)
            continue

        # Verify integrity before restoring.
        if payload_sha256(payload) != row.payload_hash:
            _logger.error(
                "rehydrate: payload_hash mismatch for row id=%d — archive may be corrupt",
                row_id,
            )
            continue

        await session.execute(
            update(RawInput).where(RawInput.id == row_id).values(payload=payload, archived_at=None)
        )
        restored += 1

    if restored:
        await session.flush()

    return restored
