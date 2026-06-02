"""Starbridge researcher adapter (scout 1.1).

Slightly thicker stub: reads STARBRIDGE_API_KEY from env, returns a placeholder
item when set so the end-to-end path can be smoke-tested.
TODO(M5c): replace with real Starbridge API call.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter

logger = logging.getLogger(__name__)


class StarbridgeAdapter(ScoutSourceAdapter):
    def fetch(
        self, territory_config: dict[str, Any] | None, last_run_at: datetime | None
    ) -> list[RawItem]:
        if not os.environ.get("STARBRIDGE_API_KEY"):
            logger.info("starbridge: STARBRIDGE_API_KEY not set — returning empty")
            return []
        logger.info("starbridge: STARBRIDGE_API_KEY present — stub placeholder")
        return [
            RawItem(
                content="Starbridge stub placeholder.", source_url="https://starbridge.example/stub"
            )
        ]
