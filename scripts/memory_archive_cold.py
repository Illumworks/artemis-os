"""memory_archive_cold.py — Cold-tier archive of old backup files.

Moves backups older than --cold-after-days from the hot backup directory
to a cold archive directory. Cold archives are not automatically pruned —
they are meant to be retained indefinitely (or managed manually).

This is the third tier in the three-layer durability model:
  Live DB → nightly backup (hot) → cold archive (long-term)

Usage:
    uv run python -m scripts.memory_archive_cold
    uv run python -m scripts.memory_archive_cold \\
        --backup-dir ~/.artemis/backups \\
        --cold-dir ~/.artemis/cold-archive \\
        --cold-after-days 30

Environment:
    ARTEMIS_HOME — root data dir; defaults to ~/.artemis
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

_logger = logging.getLogger("artemis.memory_archive_cold")

_DEFAULT_COLD_AFTER_DAYS = 30


def _default_backup_dir() -> Path:
    home = os.environ.get("ARTEMIS_HOME", os.path.expanduser("~/.artemis"))
    return Path(home) / "backups"


def _default_cold_dir() -> Path:
    home = os.environ.get("ARTEMIS_HOME", os.path.expanduser("~/.artemis"))
    return Path(home) / "cold-archive"


def run_cold_archive(
    backup_dir: Path | None = None,
    cold_dir: Path | None = None,
    cold_after_days: int = _DEFAULT_COLD_AFTER_DAYS,
    dry_run: bool = False,
) -> list[Path]:
    """Move hot backups older than cold_after_days to cold_dir.

    Returns list of moved paths.
    """
    if backup_dir is None:
        backup_dir = _default_backup_dir()
    if cold_dir is None:
        cold_dir = _default_cold_dir()

    if not backup_dir.exists():
        _logger.warning("Backup dir does not exist: %s", backup_dir)
        return []

    cold_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(UTC).timestamp() - cold_after_days * 86400
    moved: list[Path] = []

    pattern = "artemis_os_*.pgdump"
    all_backups = sorted(backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime)

    # Always keep the newest backup in hot storage regardless of age.
    for path in all_backups[:-1]:
        if path.stat().st_mtime < cutoff:
            dest = cold_dir / path.name
            if dry_run:
                _logger.info("[dry-run] Would move %s → %s", path.name, cold_dir)
            else:
                shutil.move(str(path), dest)
                # Also move the companion JSON manifest if present.
                manifest = path.with_suffix("").with_suffix(".json")
                if manifest.exists():
                    shutil.move(str(manifest), cold_dir / manifest.name)
                _logger.info("Archived %s → %s", path.name, cold_dir)
            moved.append(path)

    if not moved:
        _logger.info("No backups to archive (cold_after_days=%d).", cold_after_days)

    return moved


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Move old backups to cold archive.")
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--cold-dir", type=Path, default=None)
    parser.add_argument(
        "--cold-after-days",
        type=int,
        default=_DEFAULT_COLD_AFTER_DAYS,
        help=f"Age in days before a backup is moved to cold storage (default: {_DEFAULT_COLD_AFTER_DAYS}).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    moved = run_cold_archive(
        backup_dir=args.backup_dir,
        cold_dir=args.cold_dir,
        cold_after_days=args.cold_after_days,
        dry_run=args.dry_run,
    )
    print(f"Archived {len(moved)} file(s) to cold storage.")
    sys.exit(0)


if __name__ == "__main__":
    _cli()
