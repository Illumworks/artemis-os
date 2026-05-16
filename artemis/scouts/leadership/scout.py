"""LeadershipTransitionScout — D8 cross-source leadership transition aggregator.

Monitors a watch list of school districts for superintendent and senior leader
transitions.  Combines board minutes, state DoE feeds, and news articles, then
applies a two-source verification rule before emitting findings.

V1 simplification: the "write back to districts table on confirmed hire" path
is stubbed as a logger.info call pending schema support.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, ClassVar

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.leadership.aggregator import (
    NewsFetcher,
    gather_board_items,
    gather_doe_items,
    gather_news_items,
)
from artemis.scouts.leadership.mapping import item_to_transition_finding

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default watch list (same 5 districts as D6)
# ---------------------------------------------------------------------------

_DEFAULT_WATCH_LIST: list[dict[str, Any]] = [
    {
        "district_id": "FL_pinellas",
        "state": "FL",
        "district_name": "Pinellas County Schools",
        "boarddocs_url": "https://go.boarddocs.com/fl/pinellas/Board.nsf/Public",
        "granicus_url": None,
    },
    {
        "district_id": "FL_duval",
        "state": "FL",
        "district_name": "Duval County Public Schools",
        "boarddocs_url": "https://go.boarddocs.com/fl/duval/Board.nsf/Public",
        "granicus_url": None,
    },
    {
        "district_id": "TX_dallas",
        "state": "TX",
        "district_name": "Dallas ISD",
        "boarddocs_url": "https://go.boarddocs.com/tx/dallasisd/Board.nsf/Public",
        "granicus_url": None,
    },
    {
        "district_id": "IN_msd_pike",
        "state": "IN",
        "district_name": "MSD of Pike Township",
        "boarddocs_url": "https://go.boarddocs.com/in/msdpike/Board.nsf/Public",
        "granicus_url": None,
    },
    {
        "district_id": "MD_baltimore_city",
        "state": "MD",
        "district_name": "Baltimore City Public Schools",
        "boarddocs_url": None,
        "granicus_url": None,
    },
]

# Type alias for the boarddocs fetcher injectable.
_BoarddocsFetcher = Callable[
    [dict[str, Any], ScoutHttpClient],
    Coroutine[Any, Any, list[dict[str, Any]]],
]
_DoeFetcher = Callable[
    [str, ScoutHttpClient],
    Coroutine[Any, Any, list[dict[str, Any]]],
]


def _shared_transition_keywords(title_a: str, title_b: str) -> bool:
    """Return True if the two titles share at least one transition-relevant word."""
    # Normalise both titles to lowercase word sets.
    words_a = set(title_a.lower().split())
    words_b = set(title_b.lower().split())
    # Filter to meaningful words (length > 3 avoids noise words like "and").
    meaningful_a = {w for w in words_a if len(w) > 3}
    meaningful_b = {w for w in words_b if len(w) > 3}
    return bool(meaningful_a & meaningful_b)


def _items_overlap(items_a: list[dict[str, Any]], items_b: list[dict[str, Any]]) -> bool:
    """Return True if any item in *items_a* shares keywords with any item in *items_b*."""
    for a in items_a:
        title_a = (a.get("title") or "") + " " + (a.get("text") or a.get("snippet") or "")
        for b in items_b:
            title_b = (b.get("title") or "") + " " + (b.get("text") or b.get("snippet") or "")
            if _shared_transition_keywords(title_a, title_b):
                return True
    return False


class LeadershipTransitionScout(BaseScout):
    """Monitor school districts for superintendent and senior leader transitions.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    watch_list:
        List of district dicts to monitor.  Defaults to :data:`_DEFAULT_WATCH_LIST`.
    _http_client:
        Inject a pre-built :class:`~artemis.scouts._http.ScoutHttpClient` — tests only.
    _boarddocs_fetcher:
        Inject a replacement for :func:`~artemis.scouts.leadership.aggregator.gather_board_items`
        — tests only.
    _doe_fetcher:
        Inject a replacement for :func:`~artemis.scouts.leadership.aggregator.gather_doe_items`
        — tests only.
    _news_fetcher:
        Inject a replacement for :func:`~artemis.scouts.leadership.aggregator.gather_news_items`
        — tests only.
    """

    scout_type: ClassVar[str] = "leadership_transition_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        watch_list: list[dict[str, Any]] | None = None,
        _http_client: ScoutHttpClient | None = None,
        _boarddocs_fetcher: _BoarddocsFetcher | None = None,
        _doe_fetcher: _DoeFetcher | None = None,
        _news_fetcher: NewsFetcher | None = None,
    ) -> None:
        super().__init__(config)
        self._watch_list: list[dict[str, Any]] = (
            watch_list if watch_list is not None else list(_DEFAULT_WATCH_LIST)
        )
        self._http: ScoutHttpClient = _http_client or ScoutHttpClient()
        self._boarddocs_fetcher: _BoarddocsFetcher | None = _boarddocs_fetcher
        self._doe_fetcher: _DoeFetcher | None = _doe_fetcher
        self._news_fetcher: NewsFetcher | None = _news_fetcher

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect leadership transition findings across all watch-list districts.

        For each district:
        1. Gather board-minutes items.
        2. Gather state DoE items.
        3. Gather news items.
        4. Apply two-source verification rule.
        5. Map confirmed items to finding dicts.

        Per-district exceptions are caught and logged; processing continues.

        Returns
        -------
        list[dict]
            Deduplicated finding dicts ready for :meth:`emit_signals`.
        """
        all_findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        for district in self._watch_list:
            district_id: str = district["district_id"]
            state: str = district["state"]
            district_name: str = district.get("district_name", district_id)

            try:
                board_items = await gather_board_items(
                    district,
                    self._http,
                    _boarddocs_fetcher=self._boarddocs_fetcher,
                )
                doe_items = await gather_doe_items(
                    state,
                    self._http,
                    _doe_fetcher=self._doe_fetcher,
                )
                news_items = await gather_news_items(
                    district_name,
                    self._http,
                    _news_fetcher=self._news_fetcher,
                )
            except Exception as exc:
                _logger.warning(
                    "LeadershipTransitionScout: error gathering items for %s — skipping: %s",
                    district_id,
                    exc,
                )
                continue

            # Group by source_type.
            by_source: dict[str, list[dict[str, Any]]] = {
                "board_minutes": board_items,
                "state_doe": doe_items,
                "news_article": news_items,
            }

            # Two-source verification.
            official_source_types = {"board_minutes", "state_doe"}
            non_empty_sources = {k: v for k, v in by_source.items() if v}

            if not non_empty_sources:
                continue

            # Determine which items to emit.
            items_to_emit: list[tuple[dict[str, Any], str]] = []  # (item, source_type)

            if len(non_empty_sources) >= 2:
                # Multi-source: check if any two sources share transition keywords.
                source_keys = list(non_empty_sources.keys())
                has_overlap = False
                for i in range(len(source_keys)):
                    for j in range(i + 1, len(source_keys)):
                        if _items_overlap(
                            non_empty_sources[source_keys[i]],
                            non_empty_sources[source_keys[j]],
                        ):
                            has_overlap = True
                            break
                    if has_overlap:
                        break

                if has_overlap:
                    # Emit from all non-empty sources.
                    for src_type, items in non_empty_sources.items():
                        for item in items:
                            items_to_emit.append((item, src_type))
                else:
                    # No keyword overlap — treat each source independently.
                    for src_type, items in non_empty_sources.items():
                        if src_type in official_source_types:
                            for item in items:
                                items_to_emit.append((item, src_type))
                        else:
                            _logger.info(
                                "LeadershipTransitionScout: single news source for %s "
                                "(no official corroboration) — holding.",
                                district_id,
                            )
            else:
                # Exactly one source.
                src_type = next(iter(non_empty_sources))
                items = non_empty_sources[src_type]
                if src_type in official_source_types:
                    for item in items:
                        items_to_emit.append((item, src_type))
                else:
                    # News-only — hold.
                    _logger.info(
                        "LeadershipTransitionScout: single news source for %s "
                        "(no official corroboration) — holding.",
                        district_id,
                    )

            source_count = len(non_empty_sources)

            for item, src_type in items_to_emit:
                finding = item_to_transition_finding(item, district, src_type, source_count)

                reason_code = finding["reasonCodes"][0] if finding["reasonCodes"] else ""
                source_url = finding["metadata"].get("source_url", "")
                dedup_key: tuple[str, str, str] = (district_id, reason_code, source_url)

                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                if reason_code == "SUPE_FORMAL_HIRE":
                    _logger.info(
                        "TODO: write to districts table for %s",
                        district_id,
                    )

                all_findings.append(finding)

        return all_findings
