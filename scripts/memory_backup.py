"""memory_backup.py — Nightly pg_dump backup for the artemis_os database.

Safety model (create-verify-THEN-prune):
  1. Dump to a new timestamped file.
  2. Verify the new file is readable with pg_restore --list.
  3. Only after verification succeeds, prune files older than --keep-days.
  4. Refuse to prune if the live DB has 0 rows across critical tables
     (anomaly guard: an empty DB could indicate corruption or an attacker
     trying to wipe history — keep the old backups in that case).
  5. Never delete the sole remaining backup (always keep at least one).

Usage:
    uv run python -m scripts.memory_backup
    uv run python -m scripts.memory_backup --keep-days 14 --backup-dir ~/.artemis/backups

Environment:
    ARTEMIS_DB_URL   — SQLAlchemy async URL (parsed for pg_dump connection params)
    ARTEMIS_HOME     — root data dir (backups default to $ARTEMIS_HOME/backups)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

_logger = logging.getLogger("artemis.memory_backup")

# Tables that must have > 0 total rows before pruning is allowed.
_SENTINEL_TABLES = [
    "memory_drawers",
    "memory_observations",
    "integrations",
    "integration_configs",
    "okr_objectives",
    "okr_key_results",
    "raw_inputs",
]

_DEFAULT_KEEP_DAYS = 7


def _default_backup_dir() -> Path:
    home = os.environ.get("ARTEMIS_HOME", os.path.expanduser("~/.artemis"))
    return Path(home) / "backups"


def _parse_db_url(url: str) -> dict[str, str]:
    """Extract pg_dump-compatible connection params from a SQLAlchemy URL.

    Handles both:
      postgresql+asyncpg://user:pass@host:port/dbname
      postgresql://user:pass@host:port/dbname
    """
    # Strip SQLAlchemy driver suffix before parsing.
    clean = re.sub(r"^postgresql\+[^:]+://", "postgresql://", url)
    parsed = urlparse(clean)
    result: dict[str, str] = {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "artemis",
        "dbname": (parsed.path or "/artemis_os").lstrip("/"),
    }
    if parsed.password:
        result["password"] = parsed.password
    return result


def _live_row_count(conn_params: dict[str, str]) -> int:
    """Sum rows across sentinel tables in the live DB.

    Uses psql because we want a quick synchronous count without SQLAlchemy
    (which would require an async loop just for a guard check).
    """
    tables_sql = " + ".join(f"(SELECT COUNT(*) FROM {t})" for t in _SENTINEL_TABLES)
    sql = f"SELECT {tables_sql};"
    env = _pg_env(conn_params)
    cmd = [
        "psql",
        "-h",
        conn_params["host"],
        "-p",
        conn_params["port"],
        "-U",
        conn_params["user"],
        "-d",
        conn_params["dbname"],
        "-t",  # tuples-only
        "-A",  # unaligned
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"psql count failed: {result.stderr.strip()}")
    return int(result.stdout.strip())


def _pg_env(conn_params: dict[str, str]) -> dict[str, str]:
    """Env dict with PGPASSWORD set if a password is available."""
    env = os.environ.copy()
    if "password" in conn_params:
        env["PGPASSWORD"] = conn_params["password"]
    return env


def _find_pg_dump(conn_params: dict[str, str]) -> str:
    """Return the path to a pg_dump binary whose major version matches the server.

    pg_dump refuses to dump a server whose major version differs from its own.
    We query the server's major version and search common Homebrew paths for a
    matching binary. Falls back to the system `pg_dump` if no match is found.
    """
    env = _pg_env(conn_params)
    result = subprocess.run(
        [
            "psql",
            "-h",
            conn_params["host"],
            "-p",
            conn_params["port"],
            "-U",
            conn_params["user"],
            "-d",
            conn_params["dbname"],
            "-t",
            "-A",
            "-c",
            "SELECT current_setting('server_version_num')::int / 10000;",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        _logger.warning("Could not determine server version; using system pg_dump.")
        return "pg_dump"

    major = result.stdout.strip()
    # Try versioned Homebrew paths first (macOS common layout)
    candidates = [
        f"/opt/homebrew/opt/postgresql@{major}/bin/pg_dump",
        f"/usr/local/opt/postgresql@{major}/bin/pg_dump",
        "/opt/homebrew/opt/postgresql/bin/pg_dump",
        "pg_dump",
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists() and p.is_file():
            _logger.debug("Using pg_dump: %s", candidate)
            return candidate
    return "pg_dump"


def _run_pg_dump(conn_params: dict[str, str], dest: Path) -> None:
    """Run pg_dump in custom format (-Fc) directly to dest.

    pg_dump -Fc produces its own compressed binary (PGDMP magic). We do NOT
    wrap it in gzip — -Fc is already compressed and pg_restore reads it natively.
    """
    pg_dump_bin = _find_pg_dump(conn_params)
    cmd = [
        pg_dump_bin,
        "-h",
        conn_params["host"],
        "-p",
        conn_params["port"],
        "-U",
        conn_params["user"],
        "-Fc",  # custom format — compressed, pg_restore-compatible
        "-f",
        str(dest),
        conn_params["dbname"],
    ]
    env = _pg_env(conn_params)
    _logger.info("Running pg_dump (%s) to %s", pg_dump_bin, dest)
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, env=env)
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {proc.stderr.decode().strip()}")
    _logger.info("pg_dump complete: %s (%.1f KB)", dest, dest.stat().st_size / 1024)


def _verify_backup(path: Path) -> None:
    """Verify the pg_dump custom-format file is readable by pg_restore.

    Runs pg_restore --list (no DB connection needed) directly on the file.
    Raises RuntimeError if verification fails.
    """
    from scripts.memory_restore import _find_pg_restore_for_dump

    pg_restore_bin = _find_pg_restore_for_dump(path)
    result = subprocess.run(
        [pg_restore_bin, "--list", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore --list failed on {path}: {result.stderr.strip()}")
    toc_lines = [ln for ln in result.stdout.splitlines() if ln.strip() and not ln.startswith(";")]
    _logger.info("Backup verified: %s (%d TOC entries)", path.name, len(toc_lines))


def _prune_old_backups(backup_dir: Path, keep_days: int, live_row_count: int) -> list[Path]:
    """Delete backups older than keep_days. Returns list of deleted paths.

    Safety guards:
    - Never prune if live DB has 0 rows (anomaly).
    - Never delete the last remaining backup.
    - Prune only after the new backup has been created and verified.
    """
    if live_row_count == 0:
        _logger.warning(
            "PRUNE SKIPPED: live DB has 0 rows across sentinel tables — "
            "possible anomaly. All backups retained."
        )
        return []

    pattern = "artemis_os_*.pgdump"
    all_backups = sorted(backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime)

    if len(all_backups) <= 1:
        _logger.info("Only one backup exists; skipping prune.")
        return []

    cutoff = datetime.now(UTC).timestamp() - keep_days * 86400
    deleted: list[Path] = []

    # Keep at least one backup no matter what — the newest is all_backups[-1]
    for path in all_backups[:-1]:  # never touch the newest
        if path.stat().st_mtime < cutoff:
            _logger.info("Pruning old backup: %s", path.name)
            path.unlink()
            deleted.append(path)

    if deleted:
        _logger.info("Pruned %d old backup(s).", len(deleted))
    else:
        _logger.info("No backups old enough to prune (keep_days=%d).", keep_days)

    return deleted


def run_backup(
    backup_dir: Path | None = None,
    keep_days: int = _DEFAULT_KEEP_DAYS,
    db_url: str | None = None,
) -> Path:
    """Main entry point (importable by memory_drill.py).

    Returns the path to the new backup file.
    Raises RuntimeError on any failure.
    """
    if backup_dir is None:
        backup_dir = _default_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    if db_url is None:
        db_url = os.environ.get(
            "ARTEMIS_DB_URL",
            "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os",
        )

    conn_params = _parse_db_url(db_url)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"artemis_os_{ts}.pgdump"

    # Step 1: Dump
    _run_pg_dump(conn_params, dest)

    # Step 2: Verify the new backup before touching any existing files
    _verify_backup(dest)

    # Step 3: Count live rows (for prune guard)
    try:
        row_count = _live_row_count(conn_params)
    except Exception as exc:
        _logger.warning("Could not count live rows (%s); skipping prune.", exc)
        row_count = -1  # unknown — skip prune safely

    # Step 4: Prune old backups (only after verify succeeded)
    _prune_old_backups(backup_dir, keep_days, row_count)

    # Step 5: Write a JSON manifest alongside the backup
    manifest = {
        "backup_file": dest.name,
        "created_at": datetime.now(UTC).isoformat(),
        "db": conn_params["dbname"],
        "host": conn_params["host"],
        "size_bytes": dest.stat().st_size,
        "live_row_count": row_count,
        "keep_days": keep_days,
    }
    manifest_path = dest.with_suffix("").with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2))
    _logger.info("Manifest written: %s", manifest_path.name)

    return dest


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backup artemis_os database.")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Directory to write backups (default: $ARTEMIS_HOME/backups).",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=_DEFAULT_KEEP_DAYS,
        help=f"Keep backups this many days (default: {_DEFAULT_KEEP_DAYS}).",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Override ARTEMIS_DB_URL.",
    )
    args = parser.parse_args()

    try:
        path = run_backup(
            backup_dir=args.backup_dir,
            keep_days=args.keep_days,
            db_url=args.db_url,
        )
        print(f"Backup complete: {path}")
        sys.exit(0)
    except Exception as exc:
        _logger.error("Backup failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
