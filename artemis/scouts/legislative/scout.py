"""LegislativeScout — polls LegiScan for literacy-related bills.

Iterates priority states, searches for literacy keywords, maps each bill to
a structured finding, and emits via BaseScout.emit_signals().
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.legislative.client import LegiScanClient, make_client
from artemis.scouts.legislative.mapping import bill_to_finding

_logger = logging.getLogger(__name__)

LITERACY_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "biliteracy",
    "outcomes-based",
    "assessment",
    "tutoring",
    "curriculum",
]

_DEFAULT_PRIORITY_STATES: list[str] = [
    "FL",
    "TX",
    "CA",
    "NY",
    "GA",
    "NC",
    "OH",
    "IL",
]


class LegislativeScout(BaseScout):
    """Polls LegiScan API for literacy-related state legislation.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    api_key:
        LegiScan API key. Defaults to the ``LEGISCAN_API_KEY`` environment
        variable.  When empty, ``_gather_findings`` returns ``[]`` gracefully.
    priority_states:
        Two-letter state abbreviations to search.  Defaults to the eight
        highest-priority states for Amira.
    keywords:
        Search terms forwarded to LegiScan's ``getSearch`` operation.  Defaults
        to :data:`LITERACY_KEYWORDS`.
    _client:
        Inject a pre-built ``LegiScanClient`` — intended for tests only.
    """

    scout_type: ClassVar[str] = "legislative_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        api_key: str = "",
        priority_states: list[str] | None = None,
        keywords: list[str] | None = None,
        _client: LegiScanClient | None = None,
    ) -> None:
        super().__init__(config)
        self._api_key: str = api_key or os.getenv("LEGISCAN_API_KEY", "")
        self._priority_states: list[str] = priority_states or list(_DEFAULT_PRIORITY_STATES)
        self._keywords: list[str] = keywords or list(LITERACY_KEYWORDS)
        self._legiscan: LegiScanClient = _client or make_client(
            dry_run=self.config.dry_run,
        )

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect legislative findings across all priority states.

        Returns an empty list immediately when ``api_key`` is not set.
        Per-state exceptions are caught and logged; collection continues for
        remaining states.
        """
        if not self._api_key:
            _logger.warning(
                "LegislativeScout: LEGISCAN_API_KEY is not set — returning empty findings."
            )
            return []

        findings: list[dict[str, Any]] = []

        for state in self._priority_states:
            try:
                summaries = await self._legiscan.search(state, self._keywords)
                for summary in summaries:
                    try:
                        bill = await self._legiscan.get_bill(summary.bill_id)
                        finding = bill_to_finding(bill, state)
                        findings.append(finding)
                    except Exception as exc:
                        _logger.warning(
                            "LegislativeScout: failed to fetch/map bill %d in %s: %s",
                            summary.bill_id,
                            state,
                            exc,
                        )
            except Exception as exc:
                _logger.warning(
                    "LegislativeScout: error searching state %s — skipping: %s",
                    state,
                    exc,
                )

        return findings
