"""Scout runner — operator CLI for one-shot and scheduled scout execution.

Usage:

    # Run one scout once and exit (useful for testing / manual trigger)
    uv run python -m artemis.scouts.runner --once legislative_scout

    # Dry-run: build findings but don't POST to the API
    uv run python -m artemis.scouts.runner --once legislative_scout --dry-run

    # Run the full scheduler (equivalent to worker.py) — kept for symmetry
    uv run python -m artemis.scouts.runner --watch

Exit codes:
    0 — success (including graceful no-op when API key is unset)
    1 — unknown scout_type, or unhandled exception
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.board_minutes.scout import BoardMinutesScout
from artemis.scouts.config import load_config, scout_config_for
from artemis.scouts.federal_funding.scout import FederalFundingScout
from artemis.scouts.leadership.scout import LeadershipTransitionScout
from artemis.scouts.legislative.scout import LegislativeScout
from artemis.scouts.linkedin.scout import LinkedInObserverScout
from artemis.scouts.procurement.scout import ProcurementScout
from artemis.scouts.regional_news.scout import RegionalNewsScout
from artemis.scouts.starbridge.scout import StarbridgeResearcherScout
from artemis.scouts.state_doe.scout import StateDoEScout

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scout registry  — maps scout_type → class.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseScout]] = {
    LegislativeScout.scout_type: LegislativeScout,
    FederalFundingScout.scout_type: FederalFundingScout,
    StarbridgeResearcherScout.scout_type: StarbridgeResearcherScout,
    StateDoEScout.scout_type: StateDoEScout,
    BoardMinutesScout.scout_type: BoardMinutesScout,
    ProcurementScout.scout_type: ProcurementScout,
    LeadershipTransitionScout.scout_type: LeadershipTransitionScout,
    RegionalNewsScout.scout_type: RegionalNewsScout,
    LinkedInObserverScout.scout_type: LinkedInObserverScout,
}


def _build_scout(scout_type: str, cfg: ScoutConfig) -> BaseScout:
    """Instantiate a scout from its type string and config."""
    cls = _REGISTRY.get(scout_type)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"Unknown scout_type {scout_type!r}. Known: {known}")
    return cls(cfg)


async def _run_once(scout_type: str, dry_run: bool) -> int:
    """Run one scout, return exit code."""
    worker_cfg = load_config()
    cfg = scout_config_for(worker_cfg, scout_type)
    if dry_run:
        cfg = ScoutConfig(
            api_url=cfg.api_url,
            api_token=cfg.api_token,
            dry_run=True,
            interval_minutes=cfg.interval_minutes,
            enabled=cfg.enabled,
        )

    try:
        scout = _build_scout(scout_type, cfg)
    except ValueError as exc:
        _logger.error("%s", exc)
        return 1

    result = await scout.run_once()
    _logger.info(
        "Runner --once %s: status=%s created=%d skipped=%d errors=%d",
        scout_type,
        result.status,
        result.created_count,
        result.skipped_count,
        len(result.errors),
    )
    if result.errors:
        for err in result.errors:
            _logger.warning("  error: %s", err)
    return 0


async def _run_watch() -> int:
    """Start the APScheduler loop (all enabled scouts)."""
    from artemis.scouts.scheduler import create_scheduler

    worker_cfg = load_config()
    scouts = [
        cls(scout_config_for(worker_cfg, cls.scout_type))
        for cls in _REGISTRY.values()
        if scout_config_for(worker_cfg, cls.scout_type).enabled
    ]
    scheduler = create_scheduler(scouts)
    scheduler.start()
    _logger.info("Runner --watch: scheduler started with %d jobs.", len(scheduler.get_jobs()))
    try:
        await asyncio.Event().wait()  # block until SIGTERM / KeyboardInterrupt
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="artemis.scouts.runner",
        description="Artemis scout runner — one-shot or scheduled.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--once",
        metavar="SCOUT_TYPE",
        help="Run one scout once and exit.",
    )
    group.add_argument(
        "--watch",
        action="store_true",
        help="Start the full scheduler (all enabled scouts).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("ARTEMIS_SCOUT_DRY_RUN") == "1",
        help="Force dry_run=True; findings are gathered but NOT posted to the API.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    if args.once:
        return asyncio.run(_run_once(args.once, dry_run=args.dry_run))
    else:
        return asyncio.run(_run_watch())


if __name__ == "__main__":
    sys.exit(main())
