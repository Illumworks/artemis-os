"""Run a pg_dump backup and prune old backup files.

Usage:
    python -m scripts.memory_backup [--backup-dir PATH] [--retain-days N]

Exit codes:
    0  backup created and verified
    1  backup failed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    from artemis.config import settings
    from artemis.memory.backup import prune_old_backups, run_backup

    parser = argparse.ArgumentParser(description="Run pg_dump and prune old backups.")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=settings.backup_dir,
        help="Backup directory (default: %(default)s).",
    )
    parser.add_argument(
        "--retain-days",
        type=int,
        default=settings.backup_retain_days,
        help="Days to retain backup files (default: %(default)s).",
    )
    args = parser.parse_args()

    try:
        out = run_backup(backup_dir=args.backup_dir)
        print(f"Backup: {out} ({out.stat().st_size // 1024} KB)")
        pruned = prune_old_backups(backup_dir=args.backup_dir, retain_days=args.retain_days)
        if pruned:
            print(f"Pruned {pruned} old backup(s).")
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
