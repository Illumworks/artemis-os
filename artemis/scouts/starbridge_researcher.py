"""Starbridge Researcher scout — stub for D2.

D2 will implement real Starbridge API calls. For now this is a no-op scaffold
that validates the per-scout module pattern and is registered with the scheduler.
"""

from __future__ import annotations

from typing import Any

from artemis.scouts.base import BaseScout


class StarbridgeResearcherScout(BaseScout):
    """Discovers legislation and funding signals from Starbridge records.

    Real data-collection logic lands in D2 (LegiScan API integration).
    """

    scout_type = "starbridge_researcher"

    async def _gather_findings(self) -> list[dict[str, Any]]:
        return []  # D2 implements Starbridge API calls
