"""StateDoEScout — polls state Departments of Education for literacy signals.

Sources per state (in priority order):
1. State DoE RSS feed (falls back to HTML scrape when empty)
2. Governor press-release RSS feed
3. State board agenda page / PDF

All sources are deduplicated by (state, url) before mapping.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, ClassVar

import httpx

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts._scraper import scraper_context
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.state_doe.mapping import item_to_finding
from artemis.scouts.state_doe.sources import (
    STATE_DOE_SOURCES,
    fetch_doe_html,
    fetch_doe_rss,
    fetch_governor_rss,
    fetch_state_board_agenda,
)

_logger = logging.getLogger(__name__)

_DEFAULT_PRIORITY_STATES: list[str] = list(STATE_DOE_SOURCES.keys())


class StateDoEScout(BaseScout):
    """Scout that tracks state Department of Education literacy signals.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    priority_states:
        Two-letter state abbreviations to poll.  Defaults to all states
        defined in :data:`~artemis.scouts.state_doe.sources.STATE_DOE_SOURCES`.
    _http_client:
        Inject a :class:`~artemis.scouts._http.ScoutHttpClient` — tests only.
    _scraper_page:
        Inject a :class:`~artemis.scouts._scraper.BrowserPage` — tests only.
        When provided, Playwright is never launched.
    _pdf_open_fn:
        Inject a PDF-open callable — tests only.  Forwarded to
        :func:`~artemis.scouts._pdf.extract_text` as ``_open_fn``.
    _client:
        Inject an ``httpx.AsyncClient`` for :meth:`~BaseScout.emit_signals`.
    """

    scout_type: ClassVar[str] = "state_doe_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        priority_states: list[str] | None = None,
        _http_client: ScoutHttpClient | None = None,
        _scraper_page: Any | None = None,
        _pdf_open_fn: Callable[[Any], Any] | None = None,
        _client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config, _client=_client)
        self._priority_states: list[str] = priority_states or list(_DEFAULT_PRIORITY_STATES)
        self._http: ScoutHttpClient = _http_client or ScoutHttpClient(rate_limit=2.0)
        self._scraper_page: Any | None = _scraper_page
        self._pdf_open_fn: Callable[[Any], Any] | None = _pdf_open_fn

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect findings across all priority states.

        For each state:
        - Tries the DoE RSS feed first; falls back to HTML scrape when empty.
        - Also fetches the governor RSS feed.
        - Also fetches the state board agenda page/PDF.

        Deduplicates by (state, url) across all sources within one run.
        Per-state exceptions are caught and logged; collection continues.
        """
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()  # (state, url)

        for state in self._priority_states:
            try:
                state_items = await self._collect_state(state)
                for item in state_items:
                    url = str(item.get("link") or item.get("source_url") or "")
                    key = (state.upper(), url)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(item_to_finding(item, state))
            except Exception as exc:
                _logger.warning(
                    "StateDoEScout: error collecting state %s — skipping: %s", state, exc
                )

        _logger.info("StateDoEScout: gathered %d findings (deduped by state+url)", len(findings))
        return findings

    async def _collect_state(self, state: str) -> list[dict[str, Any]]:
        """Collect raw items for a single state from all available sources."""
        items: list[dict[str, Any]] = []

        # --- DoE RSS (primary); fall back to HTML scrape when empty ---
        rss_items = await fetch_doe_rss(state, self._http)
        if rss_items:
            items.extend(rss_items)
        else:
            # HTML scrape fallback — reuse injected page if available
            async with scraper_context(_page=self._scraper_page) as session:
                html_items = await fetch_doe_html(state, session)
            items.extend(html_items)

        # --- Governor RSS ---
        gov_items = await fetch_governor_rss(state, self._http)
        items.extend(gov_items)

        # --- State board agenda ---
        agenda_items = await fetch_state_board_agenda(
            state, self._http, pdf_open_fn=self._pdf_open_fn
        )
        items.extend(agenda_items)

        return items
