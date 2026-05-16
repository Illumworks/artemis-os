"""Starbridge Researcher Scout — real implementation.

Discovers legislation and funding signals by querying the Starbridge API
across Amira Learning's priority states.

NOTE: The Starbridge API is in bench-test period. The API shape is not yet
confirmed with the vendor. All ambiguous assumptions are marked with
``# TODO: confirm with Starbridge team``.
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

# Search terms relevant to Amira Learning's product and policy focus.
SEARCH_TERMS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "education funding",
    "curriculum",
]


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
        findings: list[dict[str, Any]] = []

        for state in self._priority_states:
            for term in SEARCH_TERMS:
                # TODO: confirm with Starbridge team — whether to filter by state in API
                # or post-filter the results. For now, include state in query.
                query = f"{term} {state}"
                filters: dict[str, Any] = {
                    "state": state,  # TODO: confirm filter key name with Starbridge team
                }
                try:
                    results = await client.search(query=query, filters=filters)
                    _logger.info(
                        "Starbridge API call: query=%r results=%d",
                        query,
                        len(results),
                    )
                    for item in results:
                        findings.append(item_to_finding(item))
                except Exception:
                    _logger.warning(
                        "StarbridgeResearcherScout: query=%r state=%s failed; continuing.",
                        query,
                        state,
                        exc_info=True,
                    )

        return findings
