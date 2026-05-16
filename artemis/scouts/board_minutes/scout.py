"""BoardMinutesScout — polls school district board meeting minutes and agendas.

Iterates a hardcoded watch list of priority districts, fetches meeting items
from BoardDocs, Granicus, or district websites, maps each item to a structured
finding, and emits via BaseScout.emit_signals().

V1 stub: the watch list is hardcoded in this module.
TODO: read from territory_config / districts table when the districts table lands.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.board_minutes.client import (
    fetch_boarddocs,
    fetch_district_site,
    fetch_granicus,
)
from artemis.scouts.board_minutes.mapping import meeting_item_to_finding

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default watch list — hardcoded for V1
# ---------------------------------------------------------------------------

_DEFAULT_WATCH_LIST: list[dict[str, Any]] = [
    {
        "district_id": "FL_pinellas",
        "state": "FL",
        "boarddocs_url": "https://go.boarddocs.com/fl/pinellas/Board.nsf/Public",  # TODO: verify URL
        "granicus_url": None,
        "district_site_url": None,
    },
    {
        "district_id": "FL_duval",
        "state": "FL",
        "boarddocs_url": "https://go.boarddocs.com/fl/duval/Board.nsf/Public",  # TODO: verify URL
        "granicus_url": None,
        "district_site_url": None,
    },
    {
        "district_id": "TX_dallas",
        "state": "TX",
        "boarddocs_url": "https://go.boarddocs.com/tx/dallasisd/Board.nsf/Public",  # TODO: verify URL
        "granicus_url": "https://dallasisd.granicus.com/ViewPublisher.php?view_id=3",  # TODO: verify URL
        "district_site_url": None,
    },
    {
        "district_id": "IN_msd_pike",
        "state": "IN",
        "boarddocs_url": "https://go.boarddocs.com/in/msdpike/Board.nsf/Public",  # TODO: verify URL
        "granicus_url": None,
        "district_site_url": None,
    },
    {
        "district_id": "MD_baltimore_city",
        "state": "MD",
        "boarddocs_url": None,
        "granicus_url": "https://baltimorecity.granicus.com/ViewPublisher.php?view_id=4",  # TODO: verify URL
        "district_site_url": "https://www.baltimorecityschools.org/board-education/board-minutes",  # TODO: verify URL
    },
]
# TODO: expand from territory_config / districts table when districts table lands


class BoardMinutesScout(BaseScout):
    """Polls school district board meeting pages for literacy-related signals.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    watch_list:
        List of district config dicts.  Defaults to :data:`_DEFAULT_WATCH_LIST`.
    _http_client:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    _scraper_page:
        Reserved for future Playwright injection; not used in V1.
    _pdf_open_fn:
        Inject a PDF-open function forwarded to ``extract_text`` — tests only.
    """

    scout_type: ClassVar[str] = "board_minutes_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        watch_list: list[dict[str, Any]] | None = None,
        _http_client: ScoutHttpClient | None = None,
        _scraper_page: Any = None,
        _pdf_open_fn: Any = None,
    ) -> None:
        super().__init__(config)
        self._watch_list: list[dict[str, Any]] = (
            watch_list if watch_list is not None else list(_DEFAULT_WATCH_LIST)
        )
        self._http: ScoutHttpClient = _http_client or ScoutHttpClient(rate_limit=2.0)
        self._scraper_page = _scraper_page  # reserved, unused in V1
        self._pdf_open_fn = _pdf_open_fn

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect board minutes findings across all watch-list districts.

        For each district:
        1. Try BoardDocs first (if configured).
        2. Fall back to Granicus if BoardDocs returns nothing.
        3. Fall back to district site if both above return nothing.

        Items are mapped to findings; irrelevant items (no literacy keywords)
        are discarded.  Findings are deduplicated by (district_id, source_url).
        Per-district exceptions are caught and logged; collection continues.
        """
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for district in self._watch_list:
            district_id: str = district.get("district_id", "unknown")
            try:
                items = await self._fetch_district_items(district)
                for item in items:
                    dedup_key = (district_id, item.get("source_url", ""))
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    finding = meeting_item_to_finding(item, district)
                    if finding is not None:
                        findings.append(finding)
            except Exception as exc:
                _logger.warning(
                    "BoardMinutesScout: error processing district %s — skipping: %s",
                    district_id,
                    exc,
                )

        _logger.info("BoardMinutesScout: gathered %d findings (deduped)", len(findings))
        return findings

    async def _fetch_district_items(self, district: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch meeting items for a single district, using source priority."""
        if district.get("boarddocs_url"):
            items = await fetch_boarddocs(district, self._http, pdf_open_fn=self._pdf_open_fn)
            if items:
                return items

        if district.get("granicus_url"):
            items = await fetch_granicus(district, self._http, pdf_open_fn=self._pdf_open_fn)
            if items:
                return items

        if district.get("district_site_url"):
            return await fetch_district_site(district, self._http, pdf_open_fn=self._pdf_open_fn)

        return []
