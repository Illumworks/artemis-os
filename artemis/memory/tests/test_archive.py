"""Tests for the cold-tier archive (archive.py)."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.archive import archive_cold, rehydrate
from artemis.memory.hashchain import verify_chain
from artemis.memory.raw_inputs import RawInput, insert_raw_input


def _old_ts(days: int = 95) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _new_ts() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


async def _insert_old(session: AsyncSession, payload: dict, days: int = 95) -> RawInput:  # type: ignore[type-arg]
    return await insert_raw_input(
        session,
        source_kind="user_turn",
        scope_kind="global",
        scope_id="default",
        payload=payload,
        created_at=_old_ts(days),
    )


async def _insert_new(session: AsyncSession, payload: dict) -> RawInput:  # type: ignore[type-arg]
    return await insert_raw_input(
        session,
        source_kind="user_turn",
        scope_kind="global",
        scope_id="default",
        payload=payload,
        created_at=_new_ts(),
    )


# ── archive selects correct rows ──────────────────────────────────────────────


async def test_archive_skips_recent_rows(db_session: AsyncSession, tmp_path: Path) -> None:
    async with db_session.begin_nested():
        await _insert_new(db_session, {"recent": True})

    count = await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path)
    assert count == 0


async def test_archive_picks_old_rows(db_session: AsyncSession, tmp_path: Path) -> None:
    async with db_session.begin_nested():
        await _insert_old(db_session, {"old": True})
        await _insert_new(db_session, {"new": True})

    count = await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path)
    assert count == 1


# ── archive file path + contents ─────────────────────────────────────────────


async def test_archive_writes_correct_path(db_session: AsyncSession, tmp_path: Path) -> None:
    ts = _old_ts(95)
    async with db_session.begin_nested():
        await _insert_old(db_session, {"check": "path"}, days=95)

    run_date = "2026-05-18"
    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path, run_date=run_date)

    expected = tmp_path / str(ts.year) / f"{ts.month:02d}" / f"raw_inputs-{run_date}.jsonl.gz"
    assert expected.exists()


async def test_archive_sets_archived_at_nulls_payload(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    async with db_session.begin_nested():
        row = await _insert_old(db_session, {"sensitive": "data"})

    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path)

    refreshed = await db_session.get(RawInput, row.id)
    assert refreshed is not None
    assert refreshed.payload is None
    assert refreshed.archived_at is not None
    assert refreshed.payload_hash  # preserved


async def test_archive_preserves_row_for_hash_chain(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    async with db_session.begin_nested():
        await _insert_old(db_session, {"old": 1})
        await _insert_new(db_session, {"new": 2})

    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path)

    result = await db_session.execute(select(RawInput))
    rows = list(result.scalars())
    assert len(rows) == 2  # old row still present as placeholder


# ── archived rows do not break chain ─────────────────────────────────────────


async def test_archived_rows_do_not_break_chain(db_session: AsyncSession, tmp_path: Path) -> None:
    async with db_session.begin_nested():
        await _insert_old(db_session, {"a": 1})
        await _insert_new(db_session, {"b": 2})

    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path)

    chain = await verify_chain(db_session)
    assert chain.ok


# ── rehydrate ─────────────────────────────────────────────────────────────────


async def test_rehydrate_restores_payload(db_session: AsyncSession, tmp_path: Path) -> None:
    original = {"rehydrate": "me", "value": 42}
    async with db_session.begin_nested():
        row = await _insert_old(db_session, original)

    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path)

    restored = await rehydrate(db_session, [row.id], archive_dir=tmp_path)
    assert restored == 1

    refreshed = await db_session.get(RawInput, row.id)
    assert refreshed is not None
    assert refreshed.payload == original
    assert refreshed.archived_at is None


# ── idempotency ───────────────────────────────────────────────────────────────


async def test_archive_is_idempotent(db_session: AsyncSession, tmp_path: Path) -> None:
    async with db_session.begin_nested():
        await _insert_old(db_session, {"x": 1})

    run_date = "2026-05-18"
    count1 = await archive_cold(
        db_session, archive_age_days=90, archive_dir=tmp_path, run_date=run_date
    )
    count2 = await archive_cold(
        db_session, archive_age_days=90, archive_dir=tmp_path, run_date=run_date
    )
    assert count1 == 1
    assert count2 == 0  # already archived


async def test_archive_file_no_duplicates_on_second_run(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    ts = _old_ts(95)
    async with db_session.begin_nested():
        await _insert_old(db_session, {"once": True}, days=95)

    run_date = "2026-05-18"
    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path, run_date=run_date)
    await archive_cold(db_session, archive_age_days=90, archive_dir=tmp_path, run_date=run_date)

    archive_file = tmp_path / str(ts.year) / f"{ts.month:02d}" / f"raw_inputs-{run_date}.jsonl.gz"
    with gzip.open(archive_file, "rt") as fh:
        lines = [line for line in fh if line.strip()]
    assert len(lines) == 1  # not duplicated
