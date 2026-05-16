"""ProcurementScout — polls statewide procurement portals for literacy RFPs.

Iterates configured portal IDs, fetches postings from each statewide portal,
maps each posting to a structured finding, deduplicates by (state, rfp_id),
and emits via BaseScout.emit_signals().
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.procurement.mapping import posting_to_finding
from artemis.scouts.procurement.portals import PORTAL_REGISTRY, fetch_portal_postings

_logger = logging.getLogger(__name__)


class ProcurementScout(BaseScout):
    """Polls statewide procurement portals for literacy-related RFPs.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    portals:
        List of portal IDs to poll (keys of ``PORTAL_REGISTRY``).  Defaults
        to all registered portals.
    _http_client:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    _pdf_open_fn:
        Inject a PDF-open function — intended for tests only.
    """

    scout_type: ClassVar[str] = "procurement_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        portals: list[str] | None = None,
        _http_client: ScoutHttpClient | None = None,
        _pdf_open_fn: Any = None,
    ) -> None:
        super().__init__(config)
        self._portals: list[str] = portals or list(PORTAL_REGISTRY.keys())
        self._scout_http: ScoutHttpClient = _http_client or ScoutHttpClient()
        self._pdf_open_fn: Any = _pdf_open_fn

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect procurement findings from all configured portals.

        Per-portal exceptions are caught and logged; collection continues
        for remaining portals. Results are deduplicated by (state, rfp_id).
        """
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for portal_id in self._portals:
            portal = PORTAL_REGISTRY.get(portal_id)
            if portal is None:
                _logger.warning("ProcurementScout: unknown portal_id '%s' — skipping.", portal_id)
                continue

            try:
                postings = await fetch_portal_postings(
                    portal_id,
                    portal,
                    self._scout_http,
                    self._pdf_open_fn,
                )
            except Exception as exc:
                _logger.warning(
                    "ProcurementScout: error fetching portal %s — skipping: %s",
                    portal_id,
                    exc,
                )
                continue

            for posting in postings:
                state: str = posting.get("state", "")
                rfp_id: str = posting.get("rfp_id", "")
                key: tuple[str, str] = (state, rfp_id)

                if key in seen:
                    _logger.debug(
                        "ProcurementScout: duplicate (state=%s, rfp_id=%s) — skipping.",
                        state,
                        rfp_id,
                    )
                    continue

                seen.add(key)
                findings.append(posting_to_finding(posting))

        return findings
