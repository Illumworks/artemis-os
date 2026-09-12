"""Keep the Gong call window warm, so nobody's question pays for it.

`recent_calls_for_account` has to page the whole window and match names locally —
Gong offers no server-side account filter, `/v2/calls/extensive` takes a date
range and nothing else. Caching that window took a district question from 86
seconds to 1.7 once warm, but the cache lives in this process and this process
restarts often. So the FIRST question after every restart still paid ~57 seconds,
and a restart usually happens because someone is actively working.

This moves that cost off the critical path entirely: the window is built shortly
after startup and refreshed on a cadence, so a question arriving at any point
finds it already there.

Cheap by construction. It is one paging pass against an API with no per-call
cost, it holds a few hundred small dataclasses, and it refreshes slightly faster
than the cache expires so there is no window in which a user pays for the rebuild.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

#: Refreshed a minute inside the cache's own five-minute TTL, so the entry is
#: replaced before it can expire and hand somebody a cold fetch.
CADENCE_MINUTES = 4

#: The window the district brief and `check_salesforce_activity` both use. Warming
#: a different one would warm nothing anyone asks for.
WARM_DAYS = 180

_scheduler: AsyncIOScheduler | None = None


async def warm_call_window() -> None:
    """Rebuild the cached window. Never raises into the scheduler."""
    from artemis.config import settings
    from artemis.integrations.gong.client import GongMetadataClient

    if not settings.gong_access_key or not settings.gong_access_key_secret:
        return
    try:
        client = GongMetadataClient(settings.gong_access_key, settings.gong_access_key_secret)
        # Any account name would do; this exists for the paging it forces, and the
        # empty-name guard means a real one has to be passed.
        calls = await client._call_window(WARM_DAYS)
        logger.info("gong warmer: window holds %d call(s) over %d days", len(calls), WARM_DAYS)
    except Exception:
        # A cold cache is a slow question, not a broken one. Never escalate.
        logger.warning("gong warmer: could not refresh the call window", exc_info=True)


def start_gong_warmer() -> None:
    """Start the warmer. Called from FastAPI lifespan startup."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        warm_call_window,
        trigger=IntervalTrigger(minutes=CADENCE_MINUTES),
        id="gong_call_window_warm",
        replace_existing=True,
        max_instances=1,  # a second pass while one is running buys nothing
        misfire_grace_time=60,
        # Runs shortly after startup rather than after the first interval, which
        # is the whole point: the gap this closes is the one right after a restart.
        next_run_time=None,
    )
    if not _scheduler.running:
        _scheduler.start()
    # Fire once now, off the startup path so it cannot delay the app coming up.
    import asyncio

    asyncio.create_task(warm_call_window())
    logger.info("Gong call-window warmer started (cadence=%d min)", CADENCE_MINUTES)


def stop_gong_warmer() -> None:
    """Stop the warmer. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
