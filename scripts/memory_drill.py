"""memory_drill.py — End-to-end recovery drill for artemis_os memory durability.

Runs the full backup → verify → restore → chain-check → row-count comparison
dance against a throwaway `artemis_drill` database. Reports structured
pass/fail JSON. Cleans up when done.

Usage:
    uv run python -m scripts.memory_drill
    uv run python -m scripts.memory_drill --output ~/.artemis/drill-reports/2026-05-18.json

Exit codes:
    0 — all steps passed
    1 — one or more steps failed

Launchd calls this script monthly. The output JSON is the single source of
truth for auditing backup health. A fail from this script is a page-able event.

Environment:
    ARTEMIS_DB_URL   — source database URL (default: artemis_os on localhost)
    ARTEMIS_HOME     — root data dir (default: ~/.artemis)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# Module-level imports so tests can patch these names directly.
from scripts.memory_backup import _parse_db_url, run_backup
from scripts.memory_restore import _drop_db_if_exists, _row_counts, _verify_backup_file, run_restore
from scripts.memory_verify_chain import run_verify_chain

_logger = logging.getLogger("artemis.memory_drill")

_DRILL_DB = "artemis_drill"
_CRITICAL_TABLES = [
    "memory_drawers",
    "memory_observations",
    "integrations",
    "integration_configs",
    "okr_objectives",
    "okr_key_results",
    "raw_inputs",
]


# ── Step helpers ──────────────────────────────────────────────────────────────


def _step(
    name: str,
    fn: Callable[[], object],
) -> dict[str, object]:
    """Run fn(), timing it. Returns a step result dict."""
    _logger.info("STEP: %s", name)
    start = time.monotonic()
    ok = False
    detail = ""
    try:
        result = fn()
        ok = True
        detail = str(result) if result is not None else "ok"
    except Exception as exc:
        detail = str(exc)
        _logger.error("  FAIL: %s", exc)
    duration_ms = int((time.monotonic() - start) * 1000)
    status = "PASS" if ok else "FAIL"
    _logger.info("  %s (%dms): %s", status, duration_ms, detail[:120])
    return {"name": name, "duration_ms": duration_ms, "ok": ok, "detail": detail}


# ── Drill orchestration ───────────────────────────────────────────────────────


def run_drill(
    db_url: str | None = None,
    backup_dir: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, object]:
    """Run the full drill. Returns the structured report dict.

    The caller receives the dict and decides whether to write it or print it.
    """
    if db_url is None:
        db_url = os.environ.get(
            "ARTEMIS_DB_URL",
            "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os",
        )

    if backup_dir is None:
        home = os.environ.get("ARTEMIS_HOME", os.path.expanduser("~/.artemis"))
        backup_dir = Path(home) / "backups"

    conn_params = _parse_db_url(db_url)
    steps: list[dict[str, object]] = []
    notes: list[str] = []
    backup_path: Path | None = None
    live_counts: dict[str, int] = {}

    # ── Step 1: Backup ────────────────────────────────────────────────────────
    def _do_backup() -> str:
        nonlocal backup_path
        # Use a temp dir so the drill doesn't pollute the real backup dir
        # but if backup_dir is provided use it.
        p = run_backup(backup_dir=backup_dir, keep_days=9999, db_url=db_url)
        backup_path = p
        return f"wrote {p.name} ({p.stat().st_size:,} bytes)"

    steps.append(_step("backup", _do_backup))

    # ── Step 2: Verify backup readable (pg_restore --list) ───────────────────
    def _do_verify() -> str:
        if backup_path is None:
            raise RuntimeError("No backup path from step 1")
        toc = _verify_backup_file(backup_path)
        toc_lines = [ln for ln in toc.splitlines() if ln.strip() and not ln.startswith(";")]
        return f"{len(toc_lines)} TOC entries"

    steps.append(_step("verify_backup", _do_verify))

    # ── Step 3: Live row counts (before restore) ──────────────────────────────
    def _do_live_counts() -> str:
        nonlocal live_counts
        live_counts = _row_counts(conn_params, conn_params["dbname"])
        total = sum(v for v in live_counts.values() if v >= 0)
        return f"total across critical tables: {total}"

    steps.append(_step("live_row_counts", _do_live_counts))

    # ── Step 4: Restore to artemis_drill ─────────────────────────────────────
    drill_counts: dict[str, int] = {}

    def _do_restore() -> str:
        nonlocal drill_counts
        if backup_path is None:
            raise RuntimeError("No backup path from step 1")
        info = run_restore(
            backup_path=backup_path,
            target_dbname=_DRILL_DB,
            db_url=db_url,
            drop_before_restore=True,
        )
        drill_counts = info["row_counts"]  # type: ignore[assignment]
        return f"restored to {_DRILL_DB}, {info['toc_line_count']} TOC entries"

    steps.append(_step("restore_to_drill_db", _do_restore))

    # ── Step 5: Chain verification on drill DB ───────────────────────────────
    chain_result: dict[str, object] = {}

    def _do_chain() -> str:
        nonlocal chain_result
        chain_result = run_verify_chain(db_url=db_url, target_dbname=_DRILL_DB)
        if not chain_result["ok"]:
            broken = chain_result.get("broken", [])
            raise RuntimeError(
                f"Chain broken: {len(broken)} issue(s). "  # type: ignore[arg-type]
                f"First: {broken[0] if broken else 'unknown'}"  # type: ignore[index]
            )
        return (
            f"ok — {chain_result['chains_checked']} links checked, "
            f"{chain_result['active_observations']} active observations"
        )

    steps.append(_step("verify_chain", _do_chain))

    # ── Step 6: Compare row counts live vs drill ──────────────────────────────
    count_mismatches: list[str] = []

    def _do_count_compare() -> str:
        for table in _CRITICAL_TABLES:
            live = live_counts.get(table, -1)
            drill = drill_counts.get(table, -1)
            if live != drill:
                count_mismatches.append(f"{table}: live={live} drill={drill}")
        if count_mismatches:
            raise RuntimeError(
                f"Row count mismatch in {len(count_mismatches)} table(s): "
                + ", ".join(count_mismatches)
            )
        return f"all {len(_CRITICAL_TABLES)} tables match"

    steps.append(_step("row_count_comparison", _do_count_compare))

    # ── Step 7: Cleanup ───────────────────────────────────────────────────────
    def _do_cleanup() -> str:
        _drop_db_if_exists(conn_params, _DRILL_DB)
        return f"dropped {_DRILL_DB}"

    steps.append(_step("cleanup_drill_db", _do_cleanup))

    # ── Build report ──────────────────────────────────────────────────────────
    passed = all(s["ok"] for s in steps)
    if count_mismatches:
        notes.extend(count_mismatches)
    if chain_result and not chain_result.get("ok"):
        for b in chain_result.get("broken", []):  # type: ignore[union-attr]
            notes.append(f"broken chain: id={b['id']} reason={b['reason']}")  # type: ignore[index]

    report: dict[str, object] = {
        "pass": passed,
        "run_at": datetime.now(UTC).isoformat(),
        "db": conn_params["dbname"],
        "backup_file": str(backup_path) if backup_path else None,
        "steps": steps,
        "notes": notes,
        "live_row_counts": live_counts,
        "drill_row_counts": drill_counts,
    }

    return report


def _default_output_path() -> Path:
    home = os.environ.get("ARTEMIS_HOME", os.path.expanduser("~/.artemis"))
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    reports_dir = Path(home) / "drill-reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f"{today}.json"


def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run the memory durability drill.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the JSON report (default: ~/.artemis/drill-reports/YYYY-MM-DD.json).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Directory to write the temporary drill backup.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Override ARTEMIS_DB_URL.",
    )
    args = parser.parse_args()

    output_path = args.output or _default_output_path()

    report = run_drill(
        db_url=args.db_url,
        backup_dir=args.backup_dir,
        output_path=output_path,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    _logger.info("Drill report written: %s", output_path)

    # Print a human-readable summary
    overall = "PASS" if report["pass"] else "FAIL"
    print(f"\n=== Memory Drill {overall} ===")
    print(f"Report: {output_path}")
    for s in report["steps"]:  # type: ignore[union-attr]
        status = "PASS" if s["ok"] else "FAIL"
        print(f"  [{status}] {s['name']} ({s['duration_ms']}ms): {s['detail'][:80]}")
    if report["notes"]:
        print("\nNotes:")
        for note in report["notes"]:  # type: ignore[union-attr]
            print(f"  - {note}")

    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    _cli()
