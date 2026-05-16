"""LinkedIn Observer scout — stub for D4+.

Mode A (follower digest) is deferred pending legal/privacy review.
Mode B (post → signal) lands in D4+.
"""

from __future__ import annotations

from typing import Any

from artemis.scouts.base import BaseScout


class LinkedInObserverScout(BaseScout):
    """Observes LinkedIn posts by district leaders for event-driven campaign signals.

    Mode B only. Mode A deferred per legal/privacy review.
    Real data-collection logic lands in D4+.
    """

    scout_type = "linkedin_observer"

    async def _gather_findings(self) -> list[dict[str, Any]]:
        return []  # D4+ implements LinkedIn post observation
