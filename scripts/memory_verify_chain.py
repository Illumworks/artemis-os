"""memory_verify_chain.py — Walk the observation superseded_by chain for integrity.

The memory system uses a "soft-delete" model: observations are never deleted,
only superseded. Each observation can point to a newer one via `superseded_by`.
This script walks every non-null superseded_by link and verifies:

  1. The referenced ID exists (no dangling FKs).
  2. There are no cycles (a → b → a would be infinite).
  3. Every observation that has superseded_by IS NULL is "active" — exactly
     the rows that retrieval reads.

This is not a cryptographic hash chain. The "chain" here refers to the logical
supersession chain of the memory keystone's lossless model.

Usage:
    uv run python -m scripts.memory_verify_chain
    uv run python -m scripts.memory_verify_chain --db artemis_drill

Returns:
    {
      "ok": bool,
      "total_observations": int,
      "active_observations": int,
      "chains_checked": int,
      "broken": [{"id": int, "superseded_by": int, "reason": str}],
    }
Exits 0 on ok, 1 on broken chain.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

_logger = logging.getLogger("artemis.memory_verify_chain")


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


def _psql_query(conn_params: dict[str, str], sql: str) -> str:
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
            sql,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql query failed: {result.stderr.strip()}")
    return result.stdout.strip()


def run_verify_chain(
    db_url: str | None = None,
    target_dbname: str | None = None,
) -> dict[str, object]:
    """Verify the supersession chain in memory_observations.

    Returns a result dict. The caller decides how to interpret it.
    """
    if db_url is None:
        db_url = os.environ.get(
            "ARTEMIS_DB_URL",
            "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os",
        )
    conn_params = _parse_db_url(db_url)
    if target_dbname:
        conn_params["dbname"] = target_dbname

    # Check if the table exists (drill DB might be freshly restored)
    try:
        table_check = _psql_query(
            conn_params,
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'memory_observations' AND table_schema = 'public';",
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "total_observations": 0,
            "active_observations": 0,
            "chains_checked": 0,
            "broken": [{"id": -1, "superseded_by": -1, "reason": str(exc)}],
        }

    if table_check != "1":
        return {
            "ok": True,
            "total_observations": 0,
            "active_observations": 0,
            "chains_checked": 0,
            "broken": [],
            "note": "memory_observations table not found — likely empty/unmigrated DB",
        }

    # Total and active counts
    total = int(_psql_query(conn_params, "SELECT COUNT(*) FROM memory_observations;"))
    active = int(
        _psql_query(
            conn_params,
            "SELECT COUNT(*) FROM memory_observations WHERE superseded_by IS NULL;",
        )
    )

    # Fetch all superseded_by links: (id, superseded_by)
    raw = _psql_query(
        conn_params,
        "SELECT id, superseded_by FROM memory_observations "
        "WHERE superseded_by IS NOT NULL ORDER BY id;",
    )

    if not raw:
        return {
            "ok": True,
            "total_observations": total,
            "active_observations": active,
            "chains_checked": 0,
            "broken": [],
        }

    # Build a lookup: id -> superseded_by (or None)
    all_ids_raw = _psql_query(
        conn_params,
        "SELECT id FROM memory_observations ORDER BY id;",
    )
    all_ids: set[int] = {int(x) for x in all_ids_raw.splitlines() if x.strip()}

    broken: list[dict[str, object]] = []
    chains: list[tuple[int, int]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 2:
            continue
        row_id, sup_id = int(parts[0]), int(parts[1])
        chains.append((row_id, sup_id))

    # Build forward map for cycle detection
    forward: dict[int, int] = {row_id: sup_id for row_id, sup_id in chains}

    def _detect_cycle(start: int) -> bool:
        slow, fast = start, start
        while True:
            slow = forward.get(slow, -1)
            fast = forward.get(forward.get(fast, -1), -1)
            if slow == -1 or fast == -1:
                return False
            if slow == fast:
                return True

    for row_id, sup_id in chains:
        # Check dangling FK
        if sup_id not in all_ids:
            broken.append(
                {
                    "id": row_id,
                    "superseded_by": sup_id,
                    "reason": f"superseded_by={sup_id} does not exist",
                }
            )
            continue
        # Check cycle (Floyd's tortoise and hare)
        if _detect_cycle(row_id):
            broken.append(
                {
                    "id": row_id,
                    "superseded_by": sup_id,
                    "reason": "cycle detected in supersession chain",
                }
            )

    return {
        "ok": len(broken) == 0,
        "total_observations": total,
        "active_observations": active,
        "chains_checked": len(chains),
        "broken": broken,
    }


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Verify the memory observation supersession chain."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Target database name (overrides ARTEMIS_DB_URL dbname).",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override ARTEMIS_DB_URL.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON.",
    )
    args = parser.parse_args()

    result = run_verify_chain(db_url=args.db_url, target_dbname=args.db)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        ok = result["ok"]
        print(f"Chain check: {'PASS' if ok else 'FAIL'}")
        print(f"  Total observations : {result['total_observations']}")
        print(f"  Active observations: {result['active_observations']}")
        print(f"  Chains checked     : {result['chains_checked']}")
        broken = result.get("broken", [])
        if broken:
            print(f"  Broken links       : {len(broken)}")
            for b in broken:  # type: ignore[union-attr]
                print(f"    id={b['id']} superseded_by={b['superseded_by']}: {b['reason']}")

    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    _cli()
