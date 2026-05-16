"""Scout worker entry point.

Usage:
    uv run python -m artemis.scouts.worker

Loads config from config/scouts.yaml + env vars (see scouts/config.py).
Starts APScheduler. Runs until SIGTERM or SIGINT.

All three scout slots are registered; real data-collection lands in D2+.
While all scouts default to enabled=false in the YAML, set enabled: true
and restart to activate a scout once its D2+ implementation is ready.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from artemis.scouts.base import BaseScout
from artemis.scouts.board_minutes.scout import BoardMinutesScout
from artemis.scouts.config import load_config, scout_config_for
from artemis.scouts.federal_funding.scout import FederalFundingScout
from artemis.scouts.leadership.scout import LeadershipTransitionScout
from artemis.scouts.legislative.scout import LegislativeScout
from artemis.scouts.linkedin.scout import LinkedInObserverScout
from artemis.scouts.procurement.scout import ProcurementScout
from artemis.scouts.regional_news.scout import RegionalNewsScout
from artemis.scouts.scheduler import create_scheduler
from artemis.scouts.starbridge.scout import StarbridgeResearcherScout
from artemis.scouts.state_doe.scout import StateDoEScout

_logger = logging.getLogger(__name__)

# All known scout classes. Scouts default to enabled=false in scouts.yaml;
# set enabled: true and restart to activate.
_SCOUT_CLASSES = [
    LegislativeScout,
    FederalFundingScout,
    StarbridgeResearcherScout,
    StateDoEScout,
    BoardMinutesScout,
    ProcurementScout,
    LeadershipTransitionScout,
    RegionalNewsScout,
    LinkedInObserverScout,
]


async def run() -> None:
    """Start the scheduler and run until a stop signal is received."""
    cfg = load_config()
    scouts: list[BaseScout] = [cls(scout_config_for(cfg, cls.scout_type)) for cls in _SCOUT_CLASSES]  # type: ignore[attr-defined]
    scheduler = create_scheduler(scouts)
    scheduler.start()

    enabled_count = len(scheduler.get_jobs())
    _logger.info("Scout worker started. %d job(s) scheduled.", enabled_count)

    stop_event = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        _logger.info("Stop signal received — shutting down.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    await stop_event.wait()
    scheduler.shutdown(wait=False)
    _logger.info("Scout worker stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())
