"""Runtime guardrails for Writing Studio collab deployment assumptions.

WS5 collab rooms are process-local in-memory state in v1. That is safe only
when the app runs with a single uvicorn worker. Before fan-out lands (Redis
pub/sub or Postgres LISTEN/NOTIFY per docs/ws5-coedit-architecture.md R10),
log a loud startup warning if the configured worker count is >1 so a future
scale-out cannot silently break co-editing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def warn_if_multiworker_collab(workers: int) -> None:
    """Warn loudly when WS5 collab is configured with >1 worker."""
    if workers <= 1:
        return
    logger.warning(
        "WS5 collab requires a single uvicorn worker in v1; configured workers=%d. "
        "Rooms are process-local and multi-worker fan-out is not implemented yet "
        "(see docs/ws5-coedit-architecture.md R10).",
        workers,
    )
