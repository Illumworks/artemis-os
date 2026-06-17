"""Board minutes scout adapter (scout 1.8).

Calls the real BoardDocs (and Granicus/district-site) fetchers from
artemis.scouts.board_minutes.client, converts meeting items into RawItems
for the LLM scout_runner path.

The fetchers are async; this adapter bridges them with asyncio.run() since
ScoutSourceAdapter.fetch() is synchronous.  The watch list mirrors the
hardcoded list in artemis.scouts.board_minutes.scout._DEFAULT_WATCH_LIST.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter
from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.board_minutes.client import (
    fetch_boarddocs,
    fetch_district_site,
    fetch_granicus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Watch list — mirrors _DEFAULT_WATCH_LIST in artemis.scouts.board_minutes.scout
# TODO: read from territory_config / districts table when that table is populated
# ---------------------------------------------------------------------------

_WATCH_LIST: list[dict[str, Any]] = [
    {
        "district_id": "FL_pinellas",
        "state": "FL",
        "boarddocs_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public",
        "granicus_url": None,
        "district_site_url": None,
    },
    {
        "district_id": "TX_dallas",
        "state": "TX",
        "boarddocs_url": "https://go.boarddocs.com/tx/disd/Board.nsf/Public",
        "granicus_url": "https://dallasisd.granicus.com/ViewPublisher.php?view_id=3",
        "district_site_url": None,
    },
    {
        "district_id": "IN_msd_pike",
        "state": "IN",
        "boarddocs_url": "https://go.boarddocs.com/in/pike/Board.nsf/Public",
        "granicus_url": None,
        "district_site_url": None,
    },
    # FL_duval: Duval has no public BoardDocs slug; granicus URL is a landing page,
    # not a direct agenda feed. Omitted until a working URL is confirmed.
    # MD_baltimore_city: Granicus + district site; district site is HTML-only.
    # Omitted for V1 — add when a working PDF or structured agenda feed is confirmed.
]

# Maximum per-item text length forwarded to the LLM (avoids blowing context).
_MAX_ITEM_TEXT = 800


async def _fetch_all_items() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Async: fetch agenda items for all watch-list districts.

    Returns a flat list of (district_config, meeting_item) pairs.
    Per-district errors are caught and logged; collection continues.
    """
    http = ScoutHttpClient(rate_limit=2.0, timeout=30.0)
    all_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    try:
        for district in _WATCH_LIST:
            district_id = district.get("district_id", "unknown")
            try:
                items: list[dict[str, Any]] = []

                if district.get("boarddocs_url"):
                    items = await fetch_boarddocs(district, http)

                if not items and district.get("granicus_url"):
                    items = await fetch_granicus(district, http)

                if not items and district.get("district_site_url"):
                    items = await fetch_district_site(district, http)

                logger.info(
                    "BoardMinutesAdapter: %s → %d items", district_id, len(items)
                )
                for item in items:
                    all_pairs.append((district, item))

            except Exception:
                logger.warning(
                    "BoardMinutesAdapter: error fetching %s — skipping",
                    district_id,
                    exc_info=True,
                )
    finally:
        await http.aclose()

    return all_pairs


def _item_to_raw(district: dict[str, Any], item: dict[str, Any]) -> RawItem:
    """Convert a meeting item dict to a RawItem for the LLM path."""
    title: str = item.get("title", "")
    text: str = item.get("text", "")
    date: str = item.get("date", "")
    source_url: str = item.get("source_url", "")
    speaker: str | None = item.get("speaker_attribution")

    # Build the LLM-facing content block.  Include district context so the LLM
    # can emit the correct district_id and state without guessing.
    content_parts = [
        f"District: {district.get('district_id', '')} (state: {district.get('state', '')})",
        f"Meeting date: {date}",
        f"Agenda item title: {title}",
    ]
    if text and text != title:
        content_parts.append(f"Item text: {text[:_MAX_ITEM_TEXT]}")
    if speaker:
        content_parts.append(f"Speaker attribution: {speaker}")

    return RawItem(
        content="\n".join(content_parts),
        source_url=source_url or None,
        source_title=title[:200] if title else None,
        source_published_at=date or None,
        metadata={
            "district_id": district.get("district_id"),
            "state": district.get("state"),
            "meeting_date": date,
            "speaker_attribution": speaker,
        },
    )


class BoardMinutesAdapter(ScoutSourceAdapter):
    """Fetches board meeting items from BoardDocs and converts them to RawItems.

    Runs the async fetcher synchronously via asyncio.run().  On any top-level
    error returns [] and logs a warning so the scout runner can continue.
    """

    def fetch(
        self, territory_config: dict[str, Any] | None, last_run_at: datetime | None
    ) -> list[RawItem]:
        try:
            pairs = asyncio.run(_fetch_all_items())
        except Exception:
            logger.warning("BoardMinutesAdapter.fetch: top-level error", exc_info=True)
            return []

        raw_items: list[RawItem] = []
        seen: set[tuple[str, str]] = set()
        for district, item in pairs:
            district_id = district.get("district_id", "")
            url = item.get("source_url", "")
            dedup_key = (district_id, url)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            raw_items.append(_item_to_raw(district, item))

        logger.info(
            "BoardMinutesAdapter.fetch: %d raw items across %d districts",
            len(raw_items),
            len(_WATCH_LIST),
        )
        return raw_items
