"""Shared base for stub (not-yet-implemented) scout source adapters."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter


class NullAdapter(ScoutSourceAdapter):
    """Returns empty list and logs 'not yet implemented'. One subclass per stub scout."""

    _slug: str = "unknown"

    def fetch(self, territory_config: Any, last_run_at: datetime | None) -> list[RawItem]:
        logging.getLogger(__name__).info("%s: not yet implemented", self._slug)
        return []
