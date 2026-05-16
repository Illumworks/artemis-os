"""Regional News Scout — stub for D3+.

D3+ will implement board-minutes, regional news, and state DoE RSS parsing.
For now this is a no-op scaffold registered with the scheduler.
"""

from __future__ import annotations

from typing import Any

from artemis.scouts.base import BaseScout


class RegionalNewsScout(BaseScout):
    """Discovers district board minutes, regional education news, and state DoE announcements.

    Real data-collection logic lands in D3+.
    """

    scout_type = "regional_news_scout"

    async def _gather_findings(self) -> list[dict[str, Any]]:
        return []  # D3+ implements RSS + PDF parsing
