"""memory_restore.py — Restore an artemis_os backup to a target database.

Usage:
    uv run python -m scripts.memory_restore <backup.pgdump.gz>
    uv run python -m scripts.memory_restore <backup.pgdump.gz> --target artemis_restore
    uv run python -m scripts.memory_restore <backup.pgdump.gz> \\
        --target artemis_os --force --i-understand-this-overwrites-live-data

Safety guards:
  - Verifies the file is a valid pg_dump custom-format file BEFORE touching any DB.
  - Refuses to restore to `artemis_os` (the live DB) unless BOTH --force and
    --i-understand-this-overwrites-live-data are passed.
  - Prints a restore summary (rows restored, recommended next steps) on completion.

Environment:
    ARTEMIS_DB_URL   — used to extract default host/port/user if not specified via --target-url
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

_logger = logging.getLogger("artemis.memory_restore")

_LIVE_DB_NAME = "artemis_os"

# Tables to report row counts on after restore.
_COUNT_TABLES = [
    "memory_drawers",
    "memory_observations",
    "integrations",
    "integration_configs",
    "okr_objectives",
    "okr_key_results",
    "raw_inputs",
]


def _parse_db_url(url: str) -> dict[str, str]:
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


def _pg_env(conn_params: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    if "password" in conn_params:
        env["PGPASSWORD"] = conn_params["password"]
    return env


def _find_pg_tool(tool: str, major: str | None = None) -> str:
    """Return the path to a versioned pg tool (pg_dump or pg_restore).

    If major is given, tries versioned Homebrew paths first so that the
    client major version matches the server. Falls back to the system tool.
    """
    if major:
        candidates = [
            f"/opt/homebrew/opt/postgresql@{major}/bin/{tool}",
            f"/usr/local/opt/postgresql@{major}/bin/{tool}",
        ]
        for candidate in candidates:
            p = Path(candidate)
            if p.exists() and p.is_file():
                return candidate
    return tool


def _dump_major_version(dump_path: Path) -> str | None:
    """Return the minimum pg_restore major version needed to read this dump.

    pg_dump -Fc writes a 9-byte binary header:
      bytes 0-4:  b'PGDMP'  (magic)
      byte  5:    archive format vmaj (always 1)
      byte  6:    archive format vmin  <-- this is what we read
      byte  7:    archive format vrev
      byte  8:    intSize
      ...

    The archive format vmin determines which pg_restore can read the file.
    pg_restore refuses files whose archive vmin it doesn't support.
    Mapping from observed vmin values to minimum pg major version needed:
      16 -> 17  (pg_dump 17 introduced format 1.16)
      15 -> 14  (format 1.15 since PG 14)
      14 -> 12  (format 1.14 since PG 12)
      13 -> 10  (format 1.13 since PG 10)
      <=12 -> 9 (older)
    """
    # vmin -> minimum pg major that introduced that format version
    format_to_pg: dict[int, int] = {
        16: 17,
        15: 14,
        14: 12,
        13: 10,
    }
    try:
        with dump_path.open("rb") as fh:
            header = fh.read(8)
        if len(header) >= 7 and header[:5] == b"PGDMP":
            vmin = header[6]
            pg_major = format_to_pg.get(vmin, 9)
            return str(pg_major)
    except Exception:
        pass
    return None


def _find_pg_restore_for_dump(dump_path: Path) -> str:
    """Return the pg_restore binary whose version matches the dump's server version."""
    major = _dump_major_version(dump_path)
    return _find_pg_tool("pg_restore", major)


def _verify_backup_file(path: Path) -> str:
    """Verify the file is a valid pg_dump custom-format dump.

    Checks the PGDMP magic bytes, then runs pg_restore --list (no DB needed).
    Returns the TOC listing. Raises RuntimeError on any failure.

    pg_dump -Fc writes a compressed binary with a PGDMP header — NOT gzip.
    """
    if not path.exists():
        raise RuntimeError(f"Backup file not found: {path}")

    # Check pg_dump custom-format magic bytes (PGDMP)
    with path.open("rb") as fh:
        magic = fh.read(5)
    if magic != b"PGDMP":
        raise RuntimeError(
            f"File does not appear to be a pg_dump custom-format file (magic={magic!r}): {path}"
        )

    pg_restore_bin = _find_pg_restore_for_dump(path)
    result = subprocess.run(
        [pg_restore_bin, "--list", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_restore --list failed — not a valid pg_dump file: {result.stderr.strip()}"
        )
    return result.stdout


def _create_db_if_missing(conn_params: dict[str, str], target_dbname: str) -> bool:
    """Create target_dbname if it doesn't already exist. Returns True if created."""
    check_sql = f"SELECT 1 FROM pg_database WHERE datname = '{target_dbname}';"
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
            "postgres",
            "-t",
            "-A",
            "-c",
            check_sql,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.stdout.strip() == "1":
        return False  # already exists

    create_sql = f"CREATE DATABASE {target_dbname} WITH OWNER = {conn_params['user']};"
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
            "postgres",
            "-c",
            create_sql,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create DB {target_dbname}: {result.stderr.strip()}")
    return True


def _drop_db_if_exists(conn_params: dict[str, str], dbname: str) -> None:
    """Drop a database (used by drill cleanup). Never call on live DB."""
    if dbname == _LIVE_DB_NAME:
        raise RuntimeError("Refusing to drop the live database.")
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
            "postgres",
            "-c",
            f"DROP DATABASE IF EXISTS {dbname};",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to drop DB {dbname}: {result.stderr.strip()}")


def _row_counts(conn_params: dict[str, str], dbname: str) -> dict[str, int]:
    """Return row counts for COUNT_TABLES in the given database.

    Tables that don't exist in the target DB return -1 (e.g. migrated schema
    might differ; the drill handles this as a warning rather than an error).
    """
    env = _pg_env(conn_params)
    counts: dict[str, int] = {}
    for table in _COUNT_TABLES:
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
                dbname,
                "-t",
                "-A",
                "-c",
                f"SELECT COUNT(*) FROM {table};",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            counts[table] = -1  # table doesn't exist in target
        else:
            counts[table] = int(result.stdout.strip())
    return counts


def run_restore(
    backup_path: Path,
    target_dbname: str = "artemis_restore",
    db_url: str | None = None,
    force_live: bool = False,
    i_understand_live_overwrite: bool = False,
    drop_before_restore: bool = False,
) -> dict[str, object]:
    """Main entry point (importable by memory_drill.py).

    Returns a result dict with:
      {
        "target_dbname": str,
        "backup_file": str,
        "row_counts": {table: int, ...},
        "toc_line_count": int,
      }
    Raises RuntimeError on any failure.
    """
    # ── Live DB guard ─────────────────────────────────────────────────────────
    if target_dbname == _LIVE_DB_NAME:
        if not (force_live and i_understand_live_overwrite):
            raise RuntimeError(
                f"Refusing to restore to '{_LIVE_DB_NAME}' (the live database). "
                "To override, pass BOTH --force AND --i-understand-this-overwrites-live-data."
            )
        _logger.warning(
            "*** OVERWRITING LIVE DATABASE '%s' — you asked for it ***",
            _LIVE_DB_NAME,
        )

    if db_url is None:
        db_url = os.environ.get(
            "ARTEMIS_DB_URL",
            "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_os",
        )
    conn_params = _parse_db_url(db_url)
    # The target DB may differ from the source DB in the URL.
    conn_params["dbname"] = target_dbname

    # ── Step 1: Verify backup BEFORE touching any DB ─────────────────────────
    _logger.info("Verifying backup file: %s", backup_path)
    toc = _verify_backup_file(backup_path)
    toc_lines = [ln for ln in toc.splitlines() if ln.strip() and not ln.startswith(";")]
    _logger.info("Backup verified: %d TOC entries", len(toc_lines))

    # ── Step 2: Create (or drop-and-recreate) the target DB ──────────────────
    if drop_before_restore:
        _drop_db_if_exists(conn_params, target_dbname)
    _create_db_if_missing(conn_params, target_dbname)

    # ── Step 3: Restore (backup_path is the native pg_dump -Fc file) ─────────
    env = _pg_env(conn_params)
    pg_restore_bin = _find_pg_restore_for_dump(backup_path)
    cmd = [
        pg_restore_bin,
        "-h",
        conn_params["host"],
        "-p",
        conn_params["port"],
        "-U",
        conn_params["user"],
        "-d",
        target_dbname,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        str(backup_path),
    ]
    _logger.info("Restoring to database: %s", target_dbname)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore failed: {result.stderr.strip()}")

    # ── Step 4: Count rows ────────────────────────────────────────────────────
    counts = _row_counts(conn_params, target_dbname)

    return {
        "target_dbname": target_dbname,
        "backup_file": str(backup_path),
        "row_counts": counts,
        "toc_line_count": len(toc_lines),
    }


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Restore an artemis_os backup.")
    parser.add_argument("backup_file", type=Path, help="Path to the .pgdump.gz file.")
    parser.add_argument(
        "--target",
        default="artemis_restore",
        help="Target database name (default: artemis_restore).",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override ARTEMIS_DB_URL for connection params.",
    )
    parser.add_argument(
        "--drop-before-restore",
        action="store_true",
        help="Drop and recreate the target DB before restoring.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required (with --i-understand-this-overwrites-live-data) to restore to artemis_os.",
    )
    parser.add_argument(
        "--i-understand-this-overwrites-live-data",
        action="store_true",
        dest="i_understand",
        help="Second confirmation required to restore to the live DB.",
    )
    args = parser.parse_args()

    try:
        info = run_restore(
            backup_path=args.backup_file,
            target_dbname=args.target,
            db_url=args.db_url,
            force_live=args.force,
            i_understand_live_overwrite=args.i_understand,
            drop_before_restore=args.drop_before_restore,
        )
        print("\n=== Restore Summary ===")
        print(f"Target DB:   {info['target_dbname']}")
        print(f"Backup file: {info['backup_file']}")
        print(f"TOC entries: {info['toc_line_count']}")
        print("\nRow counts:")
        for table, count in info["row_counts"].items():
            status = str(count) if count >= 0 else "TABLE NOT FOUND"
            print(f"  {table:<30} {status}")
        print("\nRecommended next steps:")
        print("  1. Run memory_verify_chain.py --db", info["target_dbname"])
        print("  2. Smoke-test key queries against the restored DB.")
        print("  3. If this is a drill, drop the DB when done.")
        sys.exit(0)
    except Exception as exc:
        _logger.error("Restore failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
