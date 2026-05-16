"""Tests for the migration dry-run mode.

Test IDs:
  H-DR-01  Empty source → clean empty report, exit 0
  H-DR-02  Synthetic fixture → correct row counts per table
  H-DR-03  Dry-run writes nothing to Postgres
  H-DR-04  Corrupt row (missing required field) → validation error captured
  H-DR-05  Corrupt row does not crash — remaining rows still validated
  H-DR-06  JSON-in-TEXT columns parsed without error
  H-DR-07  Unix-second timestamps recognised (no validation error)
  H-DR-08  Plan.has_validation_errors = True when row is corrupt
  H-DR-09  Plan.has_validation_errors = False when all rows are valid
  H-DR-10  Report file written as JSONL with 'summary' event + per-table events
  H-DR-11  Dry-run against real Node SQLite produces valid (non-crashing) report
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.migrate_okr_writing_rules import build_plan

# ── H-DR-01: Empty source produces clean empty report ────────────────────────


def test_dr01_empty_source(empty_sqlite_fixture: Path) -> None:
    plan = build_plan(empty_sqlite_fixture)
    assert not plan.has_validation_errors
    for r in plan.reports.values():
        assert r.source_count == 0
        assert r.valid_count == 0
        assert r.validation_errors == []


# ── H-DR-02: Synthetic fixture → correct row counts ──────────────────────────


def test_dr02_row_counts(sqlite_fixture: Path) -> None:
    plan = build_plan(sqlite_fixture)
    assert plan.reports["okr_objectives"].source_count == 2
    assert plan.reports["okr_key_results"].source_count == 1
    assert plan.reports["okr_activity"].source_count == 1
    assert plan.reports["okr_next_up"].source_count == 1
    assert plan.reports["writing_profiles"].source_count == 1
    assert plan.reports["writing_folders"].source_count == 1
    assert plan.reports["writing_rules"].source_count == 1
    assert plan.reports["writing_examples"].source_count == 1
    assert plan.reports["writing_sources"].source_count == 1


# ── H-DR-03: Dry-run does not touch Postgres ─────────────────────────────────


@pytest.mark.asyncio
async def test_dr03_dry_run_no_postgres_writes(sqlite_fixture: Path, db_session: object) -> None:
    """After a dry-run, Postgres tables must still be empty."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    build_plan(sqlite_fixture)  # purely in-memory; no DB writes

    # Postgres tables should still be empty (truncated by conftest fixture)
    session: AsyncSession = db_session  # type: ignore[assignment]
    for table in ("okr_objectives", "writing_profiles"):
        count = (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()  # noqa: S608
        assert count == 0, f"Expected 0 rows in {table} after dry-run, got {count}"


# ── H-DR-04: Corrupt row captured as validation error ────────────────────────


def test_dr04_corrupt_row_captured(tmp_path: Path) -> None:
    """A row missing `title` (required) on okr_objectives → validation error."""
    from tests.h_prep.conftest import _make_sqlite_fixture

    path = _make_sqlite_fixture(
        objectives=[
            {
                "id": 99,
                "title": None,  # NULL title — will fail OkrObjectiveRow validation (required field)
                "desc": "No title here",
                "progress": 0,
                "created_at": 1700000000,
                "updated_at": 1700000000,
            }
        ]
    )
    try:
        plan = build_plan(path)
        r = plan.reports["okr_objectives"]
        assert r.source_count == 1
        assert len(r.validation_errors) == 1
        assert r.validation_errors[0]["source_id"] == 99
    finally:
        path.unlink(missing_ok=True)


# ── H-DR-05: Corrupt row does not crash — other rows still validated ──────────


def test_dr05_corrupt_row_no_crash(tmp_path: Path) -> None:
    """One corrupt objective row + one valid → valid count = 1, error count = 1."""
    from tests.h_prep.conftest import _make_sqlite_fixture

    path = _make_sqlite_fixture(
        objectives=[
            {
                "id": 1,
                "title": "Good Objective",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
            {
                "id": 2,
                "title": None,
                "desc": "Bad row, no title",
                "created_at": 1700000001,
                "updated_at": 1700000001,
            },
        ]
    )
    try:
        plan = build_plan(path)
        r = plan.reports["okr_objectives"]
        assert r.valid_count == 1
        assert len(r.validation_errors) == 1
    finally:
        path.unlink(missing_ok=True)


# ── H-DR-06: JSON-in-TEXT columns parse without error ────────────────────────


def test_dr06_json_columns_ok(tmp_path: Path) -> None:
    """done_bullets as JSON string is accepted without a validation error."""
    import json as _json

    from tests.h_prep.conftest import _make_sqlite_fixture

    path = _make_sqlite_fixture(
        objectives=[{"id": 1, "title": "Obj", "created_at": 1700000000, "updated_at": 1700000000}],
        key_results=[
            {
                "id": 1,
                "objective_id": 1,
                "title": "KR with JSON bullets",
                "done_bullets": _json.dumps(["a", "b", "c"]),
                "gaps_bullets": _json.dumps([]),
                "updated_at": 1700000000,
            }
        ],
    )
    try:
        plan = build_plan(path)
        assert plan.reports["okr_key_results"].validation_errors == []
    finally:
        path.unlink(missing_ok=True)


# ── H-DR-07: Unix-second timestamps recognised ────────────────────────────────


def test_dr07_unix_timestamps_ok(sqlite_fixture: Path) -> None:
    """No validation errors when timestamps are integer unix-seconds."""
    plan = build_plan(sqlite_fixture)
    assert not plan.has_validation_errors


# ── H-DR-08: has_validation_errors = True on bad data ────────────────────────


def test_dr08_plan_has_errors_flag(tmp_path: Path) -> None:
    from tests.h_prep.conftest import _make_sqlite_fixture

    path = _make_sqlite_fixture(
        profiles=[
            {"id": 1, "name": None}  # NULL name — will fail WritingProfileRow validation
        ]
    )
    try:
        plan = build_plan(path)
        assert plan.has_validation_errors
    finally:
        path.unlink(missing_ok=True)


# ── H-DR-09: has_validation_errors = False on clean data ─────────────────────


def test_dr09_plan_no_errors_flag(sqlite_fixture: Path) -> None:
    plan = build_plan(sqlite_fixture)
    assert not plan.has_validation_errors


# ── H-DR-10: Report file is valid JSONL ──────────────────────────────────────


def test_dr10_report_file_jsonl(sqlite_fixture: Path, tmp_path: Path) -> None:
    plan = build_plan(sqlite_fixture)
    report_path = tmp_path / "test_report.jsonl"
    plan.write_report(report_path)

    lines = report_path.read_text().strip().splitlines()
    assert len(lines) >= 2, "Expected at least summary + one table event"

    summary = json.loads(lines[0])
    assert summary["event"] == "summary"
    assert "tables" in summary
    assert "has_validation_errors" in summary

    table_events = [json.loads(line) for line in lines[1:]]
    event_tables = {e["table"] for e in table_events}
    assert "okr_objectives" in event_tables
    assert "writing_profiles" in event_tables


# ── H-DR-11: Real Node SQLite dry-run doesn't crash ──────────────────────────


def test_dr11_real_sqlite_dry_run() -> None:
    """Run dry-run against the real Node SQLite. Should not raise."""
    real_db = Path.home() / ".artemis" / "data.db"
    if not real_db.exists():
        pytest.skip("Real Node SQLite not available in this environment")

    plan = build_plan(real_db)

    # The real DB has data — verify we got some rows
    total_source = sum(r.source_count for r in plan.reports.values())
    assert total_source > 0, "Expected at least some rows from real SQLite"

    # Validate report can be serialised
    for r in plan.reports.values():
        d = r.to_dict()
        assert isinstance(d["source_count"], int)
        assert isinstance(d["validation_errors"], list)
