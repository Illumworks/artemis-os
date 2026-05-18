"""Tests for backup.py: pg_dump, prune, verify, restore.

Tests that call real pg_dump / pg_restore are marked integration and require
a running Postgres. Tests that only exercise Python logic use mocks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from artemis.memory.backup import (
    prune_old_backups,
    restore_to_scratch,
    run_backup,
    verify_backup,
)

# ── verify_backup ─────────────────────────────────────────────────────────────


def test_verify_backup_raises_on_bad_returncode(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.pg_dump.gz"
    bad_file.write_bytes(b"not a real dump")

    with patch("artemis.memory.backup.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"invalid archive")
        with pytest.raises(subprocess.CalledProcessError):
            verify_backup(bad_file)


def test_verify_backup_passes_on_zero_returncode(tmp_path: Path) -> None:
    good_file = tmp_path / "good.pg_dump.gz"
    good_file.write_bytes(b"fake")

    with patch("artemis.memory.backup.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        verify_backup(good_file)  # should not raise


# ── prune_old_backups ─────────────────────────────────────────────────────────


def test_prune_returns_zero_if_dir_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    assert prune_old_backups(backup_dir=missing, retain_days=30) == 0


def test_prune_deletes_old_files(tmp_path: Path) -> None:
    import time

    old = tmp_path / "2026-01-01-000000.pg_dump"
    old.write_bytes(b"x")
    # Make it appear old by setting mtime to 31 days ago.
    old_time = time.time() - (31 * 86_400)
    import os

    os.utime(old, (old_time, old_time))

    recent = tmp_path / "2026-05-17-000000.pg_dump"
    recent.write_bytes(b"y")

    deleted = prune_old_backups(backup_dir=tmp_path, retain_days=30)
    assert deleted == 1
    assert not old.exists()
    assert recent.exists()


# ── restore_to_scratch ────────────────────────────────────────────────────────


def test_restore_refuses_live_db_without_force(tmp_path: Path) -> None:
    dump = tmp_path / "dump.pg_dump.gz"
    dump.write_bytes(b"x")

    with pytest.raises(ValueError, match="force_live"):
        restore_to_scratch(dump, target_db="artemis_os", force_live=False)


def test_restore_allows_scratch_db(tmp_path: Path) -> None:
    dump = tmp_path / "dump.pg_dump.gz"
    dump.write_bytes(b"x")

    with (
        patch("artemis.memory.backup.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
        restore_to_scratch(dump, target_db="artemis_os_restore")
        # createdb + pg_restore calls
        assert mock_run.call_count == 2


# ── run_backup (integration) ──────────────────────────────────────────────────


@pytest.mark.integration
def test_run_backup_produces_readable_dump(tmp_path: Path) -> None:
    """Requires a running Postgres with artemis_os database."""
    out = run_backup(backup_dir=tmp_path, timestamp="test-backup")
    assert out.exists()
    assert out.stat().st_size > 0
    # verify_backup is called inside run_backup; if it didn't raise, dump is valid.


@pytest.mark.integration
def test_run_backup_and_restore_roundtrip(tmp_path: Path) -> None:
    """Full backup → restore to artemis_os_restore roundtrip. Requires running Postgres."""
    dump = run_backup(backup_dir=tmp_path, timestamp="roundtrip-test")
    restore_to_scratch(dump, target_db="artemis_os_restore")
    # If restore completes without exception, the roundtrip succeeded.
    # Cleanup: drop artemis_os_restore.
    subprocess.run(
        ["dropdb", "--if-exists", "-h", "localhost", "-U", "artemis", "artemis_os_restore"],
        capture_output=True,
    )
