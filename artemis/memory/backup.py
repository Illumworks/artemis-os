"""pg_dump wrapper: nightly backup and scripted restore.

Backup workflow:
  pg_dump -Fc -h HOST -U USER DBNAME | gzip > ~/.artemis/backups/TIMESTAMP.pg_dump.gz
  Prune files older than retain_days.
  Verify backup is readable via pg_restore --list.

Restore workflow:
  Restore to artemis_os_restore (never directly to the live DB without --force).
  Operator verifies, then manually swaps. See docs/MEMORY-DURABILITY.md.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

_logger = logging.getLogger(__name__)


def _default_backup_dir() -> Path:
    from artemis.config import settings

    return settings.backup_dir


def _db_parts() -> tuple[str, str, str, int]:
    """Return (host, user, dbname, port) from settings."""
    from artemis.config import settings

    return (
        settings.backup_pg_host,
        settings.backup_pg_user,
        settings.backup_pg_dbname,
        settings.backup_pg_port,
    )


def _pg_bin(name: str) -> str:
    """Resolve a Postgres binary from the configured bin directory."""
    from artemis.config import settings

    return str(Path(settings.backup_pg_bindir) / name)


def run_backup(
    *,
    backup_dir: Path | None = None,
    timestamp: str | None = None,
) -> Path:
    """Run pg_dump -Fc and write a custom-format dump file.

    pg_dump's custom format (-Fc) is already zlib-compressed internally.
    No additional gzip layer is applied — the file is read directly by pg_restore.

    Returns the path to the created file. Raises subprocess.CalledProcessError
    on failure. Verifies the dump is readable before returning.
    """
    bdir = backup_dir or _default_backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)

    ts = timestamp or datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    out_path = bdir / f"{ts}.pg_dump"
    partial = out_path.with_suffix(".pg_dump.partial")

    host, user, dbname, port = _db_parts()

    dump_cmd = [
        _pg_bin("pg_dump"),
        "-Fc",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-f",
        str(partial),
        dbname,
    ]

    _logger.info("backup: running pg_dump → %s", out_path)
    result = subprocess.run(dump_cmd, capture_output=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise subprocess.CalledProcessError(
            result.returncode, dump_cmd, stderr=result.stderr.decode()
        )

    partial.rename(out_path)

    verify_backup(out_path)  # raises on failure
    _logger.info("backup: verified %s (%.1f MB)", out_path, out_path.stat().st_size / 1_048_576)
    return out_path


def prune_old_backups(*, backup_dir: Path | None = None, retain_days: int = 30) -> int:
    """Delete .pg_dump files older than retain_days. Returns count deleted."""

    bdir = backup_dir or _default_backup_dir()
    if not bdir.exists():
        return 0

    retain_secs = retain_days * 86_400
    now = datetime.now(UTC).timestamp()
    deleted = 0
    for f in bdir.glob("*.pg_dump"):
        if now - f.stat().st_mtime > retain_secs:
            f.unlink()
            _logger.info("backup: pruned %s", f)
            deleted += 1
    return deleted


def verify_backup(path: Path) -> None:
    """Run pg_restore --list on the file; raises CalledProcessError if unreadable."""
    pg_restore = _pg_bin("pg_restore")
    result = subprocess.run(
        [pg_restore, "--list", str(path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            [pg_restore, "--list", str(path)],
            stderr=result.stderr.decode(),
        )


def restore_to_scratch(
    dump_path: Path,
    *,
    target_db: str = "artemis_os_restore",
    force_live: bool = False,
) -> None:
    """Restore a dump to a scratch database.

    Refuses to restore directly to artemis_os unless force_live=True.
    Creates the target DB if it doesn't exist. Restores clean (--clean --if-exists).

    After restore, the operator should:
      1. Verify data in artemis_os_restore.
      2. Run: psql -c 'ALTER DATABASE artemis_os RENAME TO artemis_os_old'
      3. Run: psql -c 'ALTER DATABASE artemis_os_restore RENAME TO artemis_os'
      See docs/MEMORY-DURABILITY.md for the full swap checklist.
    """
    from artemis.config import settings

    live_db = settings.backup_pg_dbname
    if target_db == live_db and not force_live:
        raise ValueError(
            f"Refusing to restore directly to {live_db!r}. "
            "Pass force_live=True to override, or restore to artemis_os_restore first."
        )

    host, user, _, port = _db_parts()

    # Create target DB (ignore error if already exists).
    subprocess.run(
        [_pg_bin("createdb"), "-h", host, "-p", str(port), "-U", user, target_db],
        capture_output=True,
    )

    _logger.info("backup: restoring %s → %s", dump_path, target_db)
    result = subprocess.run(
        [
            _pg_bin("pg_restore"),
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            target_db,
            "--clean",
            "--if-exists",
            str(dump_path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode()
        raise subprocess.CalledProcessError(result.returncode, "pg_restore", stderr=stderr)

    _logger.info("backup: restore complete → %s", target_db)
