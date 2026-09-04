"""Starbridge Researcher Scout — reads the signal feed.

**Starbridge is a feed, not a search engine.** This scout used to loop over
priority states crossed with search terms and POST each pair to a ``search``
endpoint. No such endpoint exists. An organisation configures *bridges* --
standing monitors for RFPs, board meetings, buyers, contacts and purchases -- and
the API returns the rows those bridges matched. Ours has 68 bridges holding
174,544 rows, already tuned for literacy and screening.

So the scout reads what the bridges found and filters locally, rather than
sending terms nobody is listening for.

The old shape was 40 requests a run (8 states x 5 terms) against a host that does
not resolve. This is one.
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.starbridge.client import StarbridgeClient
from artemis.scouts.starbridge.mapping import item_to_finding

_logger = logging.getLogger(__name__)

# Default priority states for Amira Learning market coverage.
_DEFAULT_PRIORITY_STATES: list[str] = ["FL", "TX", "CA", "NY", "GA", "NC", "OH", "IL"]

# Kept for callers that still reference it; the feed is not term-driven.
SEARCH_TERMS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "education funding",
    "curriculum",
]

#: Starbridge scores each row 1-5 against its bridge. Below this the row matched
#: the monitor loosely and is not worth a signal; the live feed's top entries sit
#: at 4 and 5.
MIN_MATCH_SCORE = 4

#: How many feed rows to pull per run. Deduplication happens inside the client --
#: overlapping bridges re-report the same event, and 50 raw rows carried only 28
#: distinct titles when measured.
FEED_PAGE_SIZE = 100


class StarbridgeResearcherScout(BaseScout):
    """Discovers legislation and funding signals from the Starbridge API.

    Runs on a 4-hour cadence (configured via ``ScoutConfig.interval_minutes``).
    With no API key, the scout logs a warning and returns no findings
    (graceful no-op — suitable for local dev without credentials).

    Parameters
    ----------
    config:
        Standard scout runtime config.
    api_key:
        Starbridge API key. Falls back to ``STARBRIDGE_API_KEY`` env var
        when not provided explicitly.
    priority_states:
        List of two-letter state abbreviations to search. Defaults to
        ``_DEFAULT_PRIORITY_STATES``.
    _client:
        Inject a pre-built ``StarbridgeClient`` — intended for tests only.
    """

    scout_type: ClassVar[str] = "starbridge_researcher"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        api_key: str = "",
        priority_states: list[str] | None = None,
        _client: StarbridgeClient | None = None,
    ) -> None:
        super().__init__(config)
        self._api_key = api_key or os.getenv("STARBRIDGE_API_KEY", "")
        self._priority_states: list[str] = priority_states or _DEFAULT_PRIORITY_STATES
        # _client injection takes precedence over api_key; build lazily if needed.
        self._starbridge_client: StarbridgeClient | None = _client

    def _get_client(self) -> StarbridgeClient:
        """Return (or lazily build) the StarbridgeClient."""
        if self._starbridge_client is None:
            self._starbridge_client = StarbridgeClient(api_key=self._api_key)
        return self._starbridge_client

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Query Starbridge for legislative and funding items.

        Returns an empty list immediately if the API key is not configured.
        Exceptions for individual query attempts are caught and logged; the
        scout continues with remaining queries.
        """
        if not self._api_key:
            _logger.warning(
                "StarbridgeResearcherScout: STARBRIDGE_API_KEY not set — skipping run. "
                "Set the environment variable to enable Starbridge data collection."
            )
            return []

        client = self._get_client()

        # One call. The bridges have already done the searching; asking them what
        # they found is the entire integration.
        items = await client.top_signals(limit=FEED_PAGE_SIZE)

        findings: list[dict[str, Any]] = []
        skipped_low_score = 0
        skipped_unclassified = 0
        for item in items:
            if item.match_score is not None and item.match_score < MIN_MATCH_SCORE:
                skipped_low_score += 1
                continue
            finding = item_to_finding(item)
            # No reason code means we could not place it in Josh's registry.
            # Emitting it anyway is how a Kansas procurement notice became a
            # federal grant; an unclassified signal is worth less than nothing
            # once it carries a confident wrong label.
            if not finding.get("reasonCodes"):
                skipped_unclassified += 1
                continue
            findings.append(finding)

        _logger.info(
            "StarbridgeResearcherScout: %d signals after dedupe, %d kept, "
            "%d below score %d, %d unclassified",
            len(items),
            len(findings),
            skipped_low_score,
            MIN_MATCH_SCORE,
            skipped_unclassified,
        )
        return findings
