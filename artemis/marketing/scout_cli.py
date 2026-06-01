"""Standalone entry point for a single scheduled scout run (#102).

Invoked as a subprocess from ``scout_scheduler._run_scout_job`` so the web
process never spawns ``claude`` directly. When this process exits, the OS
reaps the claude grandchild + every semaphore the subscription adapter
created — nothing leaks back into the FastAPI app. The scheduler stays an
in-process APScheduler timer; only the *execution* of one cycle moves out.

Usage:
    python -m artemis.marketing.scout_cli <agent_id>

Exit codes: 0 on a clean run_agent (regardless of how many signals it
emitted — zero signals is a valid empty cycle), non-zero on any exception.
A one-line JSON result is printed to stdout so the scheduler can log it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

logger = logging.getLogger(__name__)

# Type alias: a callable returning an async context manager yielding an
# AsyncSession. Production uses SessionLocal; tests inject a factory bound
# to the test engine.
SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]


async def _run_scout_in_db(
    agent_id: str,
    *,
    session_factory: SessionFactory | None = None,
) -> dict[str, Any]:
    """Run one scout cycle and record a scout_runs row. Returns a summary dict.

    Same behavior as the previous in-loop ``_run_scout_job``: invoke
    run_agent, count emitted signals via provenance.agent_run_id, and write
    a scout_runs row so the freshness panel + history stay accurate. The
    only difference is the caller's process — this helper is called from
    the CLI subprocess, not the web event loop.

    The session_factory parameter exists for tests; production passes None
    and the real ``artemis.db.SessionLocal`` is used.
    """
    from sqlalchemy import select

    from artemis.builders.executor import default_agent_instruction, run_agent
    from artemis.marketing.models import SignalQueue
    from artemis.marketing.repository import create_scout_run, update_scout_run

    if session_factory is None:
        from artemis.db import SessionLocal as _SessionLocal

        session_factory = _SessionLocal

    slug = agent_id.split(".")[-1]
    async with session_factory() as session:
        try:
            run = await run_agent(
                session=session,
                agent_id=agent_id,
                user_message=default_agent_instruction(agent_id),
            )
            signal_ids = (
                (
                    await session.execute(
                        select(SignalQueue.id).where(
                            SignalQueue.provenance["agent_run_id"].astext == run.run_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            scout_run = await create_scout_run(
                session,
                run_id=f"sched_{run.run_id}",
                scout_type=slug,
                status="pending",
            )
            await update_scout_run(
                session,
                scout_run.id,
                status="complete" if run.status == "completed" else "failed",
                created_signal_ids=[str(sid) for sid in signal_ids],
            )
            await session.commit()
            return {
                "agent_id": agent_id,
                "run_id": run.run_id,
                "status": run.status,
                "emitted": len(signal_ids),
                "scout_run_id": scout_run.id,
            }
        except Exception:
            await session.rollback()
            raise


async def _amain(agent_id: str) -> int:
    try:
        result = await _run_scout_in_db(agent_id)
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        logger.exception("scout_cli: run failed for %s", agent_id)
        print(
            json.dumps({"agent_id": agent_id, "status": "failed", "error": repr(exc)}),
            flush=True,
        )
        return 1
    print(json.dumps(result), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m artemis.marketing.scout_cli",
        description="Run one scheduled scout cycle out-of-process.",
    )
    parser.add_argument(
        "agent_id",
        help="Marketing scout agent id (e.g. marketing.scout.regional_news).",
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
    return asyncio.run(_amain(ns.agent_id))


# Anchored so the scheduler can build argv as
# `[sys.executable, "-m", _MODULE, agent_id]` without re-hardcoding the name.
MODULE_NAME = "artemis.marketing.scout_cli"


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    sys.exit(main())
