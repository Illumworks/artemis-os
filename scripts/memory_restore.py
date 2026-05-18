"""Restore a pg_dump backup to a scratch database.

Restores to artemis_os_restore by default. The operator verifies,
then manually swaps. See docs/MEMORY-DURABILITY.md for the full procedure.

Usage:
    python -m scripts.memory_restore PATH_TO_DUMP.pg_dump.gz [--force]

Exit codes:
    0  restore complete
    1  restore failed or refused
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    from artemis.memory.backup import restore_to_scratch

    parser = argparse.ArgumentParser(
        description="Restore a pg_dump to artemis_os_restore for operator verification."
    )
    parser.add_argument("dump", type=Path, help="Path to .pg_dump.gz backup file.")
    parser.add_argument(
        "--target-db",
        default="artemis_os_restore",
        help="Target DB name (default: artemis_os_restore).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow restoring directly to the live DB (dangerous).",
    )
    args = parser.parse_args()

    if not args.dump.exists():
        print(f"Error: dump file not found: {args.dump}", file=sys.stderr)
        sys.exit(1)

    try:
        restore_to_scratch(args.dump, target_db=args.target_db, force_live=args.force)
        print(f"Restore complete → {args.target_db}")
        if args.target_db == "artemis_os_restore":
            print(
                "\nNext steps (see docs/MEMORY-DURABILITY.md):\n"
                "  1. Verify data in artemis_os_restore\n"
                "  2. psql -c 'ALTER DATABASE artemis_os RENAME TO artemis_os_old'\n"
                "  3. psql -c 'ALTER DATABASE artemis_os_restore RENAME TO artemis_os'\n"
                "  4. Restart the app\n"
                "  5. Drop artemis_os_old once satisfied"
            )
    except ValueError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
