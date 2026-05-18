"""Tests for memory durability scripts.

These tests use a live postgres connection (artemis_test DB). They exercise:
  - memory_backup.py  — backup creation and pruning safety
  - memory_restore.py — restore guards and row count reporting
  - memory_verify_chain.py — supersession chain integrity
  - memory_drill.py   — full end-to-end drill

Requires:
  - Postgres running at ARTEMIS_DB_URL (artemis_test DB, already migrated)
  - pg_dump / pg_restore / psql on PATH

Run:
    uv run pytest tests/test_memory_drill.py -v
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest

# Force test DB before any artemis imports (mirrors conftest.py pattern)
_TEST_DB_URL = "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test"
os.environ.setdefault("ARTEMIS_DB_URL", _TEST_DB_URL)

from scripts.memory_backup import _parse_db_url, _pg_env, _verify_backup, run_backup  # noqa: E402
from scripts.memory_drill import run_drill  # noqa: E402
from scripts.memory_restore import _drop_db_if_exists, _row_counts, run_restore  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SOURCE_DB = "artemis_test"
_DRILL_TARGET = "artemis_drill_test"  # isolated from main drill


@pytest.fixture(scope="module")
def conn_params() -> dict[str, str]:
    return _parse_db_url(_TEST_DB_URL)


@pytest.fixture()
def tmp_backup_dir(tmp_path: Path) -> Path:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    return backup_dir


@pytest.fixture(autouse=True)
def cleanup_drill_test_db(conn_params: dict[str, str]) -> Generator[None, None, None]:
    """Drop the drill test DB before and after each test that might create it."""
    _drop_db_if_exists(conn_params, _DRILL_TARGET)
    yield
    _drop_db_if_exists(conn_params, _DRILL_TARGET)


# ── Helper ────────────────────────────────────────────────────────────────────


def _make_backup(tmp_backup_dir: Path) -> Path:
    """Create a real backup of artemis_test for use in tests."""
    return run_backup(
        backup_dir=tmp_backup_dir,
        keep_days=9999,  # never prune during tests
        db_url=_TEST_DB_URL,
    )


# ── Test 1: Drill on a seed DB → all steps green ─────────────────────────────


def test_drill_passes_on_seed_db(tmp_backup_dir: Path) -> None:
    """Full drill against artemis_test should pass with all steps ok."""
    report = run_drill(
        db_url=_TEST_DB_URL,
        backup_dir=tmp_backup_dir,
    )

    assert report["pass"] is True, f"Drill failed: {report['notes']}"

    steps = {s["name"]: s for s in report["steps"]}  # type: ignore[union-attr]

    assert steps["backup"]["ok"], "backup step failed"
    assert steps["verify_backup"]["ok"], "verify_backup step failed"
    assert steps["live_row_counts"]["ok"], "live_row_counts step failed"
    assert steps["restore_to_drill_db"]["ok"], "restore_to_drill_db step failed"
    assert steps["verify_chain"]["ok"], "verify_chain step failed"
    assert steps["row_count_comparison"]["ok"], "row_count_comparison step failed"
    assert steps["cleanup_drill_db"]["ok"], "cleanup_drill_db step failed"

    # Drill DB should be cleaned up
    env = _pg_env(_parse_db_url(_TEST_DB_URL))
    result = subprocess.run(
        [
            "psql",
            "-h",
            "localhost",
            "-p",
            "5432",
            "-U",
            "artemis",
            "-d",
            "postgres",
            "-t",
            "-A",
            "-c",
            "SELECT 1 FROM pg_database WHERE datname = 'artemis_drill';",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.strip() != "1", "artemis_drill DB was not cleaned up"


# ── Test 2: Row count mismatch → fail with which table ───────────────────────


def test_drill_fails_on_row_count_mismatch(
    tmp_backup_dir: Path, conn_params: dict[str, str]
) -> None:
    """If live and drill row counts differ, drill reports fail + names the table."""
    import unittest.mock as mock

    # Take a real backup first
    backup_path = _make_backup(tmp_backup_dir)

    # Restore to drill DB
    run_restore(
        backup_path=backup_path,
        target_dbname=_DRILL_TARGET,
        db_url=_TEST_DB_URL,
        drop_before_restore=True,
    )

    # Patch live counts to simulate a mismatch on 'memory_drawers'
    real_row_counts = _row_counts

    def _fake_live_counts(cp: dict, dbname: str) -> dict[str, int]:
        counts = real_row_counts(cp, dbname)
        if dbname == _SOURCE_DB:
            counts["memory_drawers"] = counts.get("memory_drawers", 0) + 999
        return counts

    with mock.patch("scripts.memory_drill._row_counts", side_effect=_fake_live_counts):
        # We also need the drill to use the already-restored _DRILL_TARGET
        # Override the restore step to just return pre-computed counts
        def _fast_restore(**kwargs: object) -> dict:
            return {
                "target_dbname": _DRILL_TARGET,
                "backup_file": str(backup_path),
                "row_counts": real_row_counts(conn_params, _DRILL_TARGET),
                "toc_line_count": 1,
            }

        with mock.patch("scripts.memory_drill.run_restore", side_effect=_fast_restore):
            report = run_drill(
                db_url=_TEST_DB_URL,
                backup_dir=tmp_backup_dir,
            )

    assert report["pass"] is False
    steps = {s["name"]: s for s in report["steps"]}  # type: ignore[union-attr]
    assert not steps["row_count_comparison"]["ok"]
    # Should mention memory_drawers specifically
    detail = steps["row_count_comparison"]["detail"]
    assert "memory_drawers" in detail


# ── Test 3: Broken chain → fail with the broken row id ───────────────────────


def test_drill_fails_on_broken_chain(tmp_backup_dir: Path, conn_params: dict[str, str]) -> None:
    """Chain verify returning a broken link causes drill to fail with that row id."""
    import unittest.mock as mock

    broken_chain_result = {
        "ok": False,
        "total_observations": 10,
        "active_observations": 9,
        "chains_checked": 1,
        "broken": [{"id": 42, "superseded_by": 99, "reason": "superseded_by=99 does not exist"}],
    }

    with mock.patch("scripts.memory_drill.run_verify_chain", return_value=broken_chain_result):
        report = run_drill(
            db_url=_TEST_DB_URL,
            backup_dir=tmp_backup_dir,
        )

    assert report["pass"] is False
    steps = {s["name"]: s for s in report["steps"]}  # type: ignore[union-attr]
    assert not steps["verify_chain"]["ok"]
    assert (
        "42" in steps["verify_chain"]["detail"]
        or "broken" in steps["verify_chain"]["detail"].lower()
    )


# ── Test 4: Restore --target rejection for artemis_os ────────────────────────


def test_restore_rejects_live_db_without_flags(tmp_backup_dir: Path) -> None:
    """run_restore to 'artemis_os' without both safety flags must raise."""
    backup_path = _make_backup(tmp_backup_dir)

    with pytest.raises(RuntimeError, match="Refusing to restore"):
        run_restore(
            backup_path=backup_path,
            target_dbname="artemis_os",
            db_url=_TEST_DB_URL,
            force_live=False,
            i_understand_live_overwrite=False,
        )

    # force alone is not enough
    with pytest.raises(RuntimeError, match="Refusing to restore"):
        run_restore(
            backup_path=backup_path,
            target_dbname="artemis_os",
            db_url=_TEST_DB_URL,
            force_live=True,
            i_understand_live_overwrite=False,
        )

    # i_understand alone is not enough
    with pytest.raises(RuntimeError, match="Refusing to restore"):
        run_restore(
            backup_path=backup_path,
            target_dbname="artemis_os",
            db_url=_TEST_DB_URL,
            force_live=False,
            i_understand_live_overwrite=True,
        )


# ── Test 5: Backup + prune — newest backup always survives ───────────────────


def test_backup_prune_keeps_latest(tmp_backup_dir: Path) -> None:
    """After pruning with keep_days=0, the most recent backup must survive."""
    import time

    # Create two backups with a slight delay so they have different mtimes
    path1 = _make_backup(tmp_backup_dir)
    time.sleep(1.1)  # ensure different mtime
    path2 = run_backup(
        backup_dir=tmp_backup_dir,
        keep_days=0,  # prune everything older than 0 days
        db_url=_TEST_DB_URL,
    )

    # path1 should be pruned; path2 (the newest) must survive
    assert not path1.exists(), f"Old backup should have been pruned: {path1.name}"
    assert path2.exists(), f"Newest backup must survive pruning: {path2.name}"

    # Verify the surviving backup is readable
    _verify_backup(path2)


# ── Test 6: Backup refuses to prune when only one backup exists ──────────────


def test_backup_never_prunes_sole_backup(tmp_backup_dir: Path) -> None:
    """When only one backup exists, it must never be pruned regardless of age."""
    # Create exactly one backup
    path = run_backup(
        backup_dir=tmp_backup_dir,
        keep_days=0,  # aggressive prune
        db_url=_TEST_DB_URL,
    )

    # The sole backup must still exist
    assert path.exists(), "Sole backup was pruned — must never prune last backup"
    backups = list(tmp_backup_dir.glob("artemis_os_*.pgdump"))
    assert len(backups) == 1
