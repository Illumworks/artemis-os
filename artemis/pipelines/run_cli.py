"""Standalone entry point for a single pipeline run (#103).

Invoked as a subprocess from ``pipelines.routes._dispatch_execution`` and
``pipelines.scheduler._fire_scheduled_pipeline`` so the web process never
spawns ``claude`` directly. When this process exits, the OS reaps the
claude grandchild + every semaphore the subscription adapter created —
nothing leaks back into the FastAPI app. The DB row already exists before
dispatch; everything flows through the DB so there is no context to
marshal across the subprocess boundary.

Usage:
    python -m artemis.pipelines.run_cli <run_id>

Exit codes: 0 on a clean executor run (including ``awaiting_approval`` —
the executor returned cleanly, just suspended on a gate), non-zero on any
exception. A one-line JSON result is printed to stdout so the dispatcher
can log it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logger = logging.getLogger(__name__)


async def _amain(run_id: str) -> int:
    # Imported lazily so ``python -m artemis.pipelines.run_cli --help``
    # doesn't pay the full FastAPI / SQLAlchemy import cost.
    from artemis.pipelines.routes import _execute_pipeline_run

    try:
        await _execute_pipeline_run(run_id)
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        logger.exception("run_cli: pipeline run %s failed", run_id)
        print(
            json.dumps({"run_id": run_id, "status": "failed", "error": repr(exc)}),
            flush=True,
        )
        return 1
    print(json.dumps({"run_id": run_id, "status": "ok"}), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m artemis.pipelines.run_cli",
        description="Execute one pipeline run out-of-process.",
    )
    parser.add_argument(
        "run_id",
        help="Existing pipeline_runs.id — must already be created (status=queued).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging to stderr.",
    )
    ns = parser.parse_args(argv)
    if ns.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    return asyncio.run(_amain(ns.run_id))


# Anchored so dispatchers can build argv as
# `[sys.executable, "-m", MODULE_NAME, run_id]` without re-hardcoding the name.
MODULE_NAME = "artemis.pipelines.run_cli"


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    sys.exit(main())
