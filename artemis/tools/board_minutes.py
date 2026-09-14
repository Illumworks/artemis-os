"""Tool: board_minutes.fetch

Fetches board meeting minutes/agendas for a district using BoardDocs.
Reuses artemis.scouts.board_minutes.client.fetch_boarddocs.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.board_minutes.client import (
    _boarddocs_base,
    fetch_agenda_item_files,
    fetch_boarddocs,
)
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="board_minutes.fetch",
    description=(
        "Fetch board meeting minutes and agendas for a district from BoardDocs. "
        "Returns items as JSON [{title, date, source_url, text, speaker_attribution, "
        "attachment_urls}], newest first, capped at 25 items. "
        "'attachment_urls' are the item's actual PDF documents — the board doc, the "
        "bid sheet, the contract — where the substance usually is. Pass one of those "
        "to pdf_extractor.extract; do NOT pass 'source_url', which is an HTML page. "
        "Returns [] if the district has no boarddocs_url configured or on any error."
    ),
    input_schema={
        "type": "object",
        "required": ["district"],
        "properties": {
            "district": {
                "type": "object",
                "description": "District config dict with optional 'boarddocs_url' key.",
                "properties": {
                    "district_id": {"type": "string"},
                    "boarddocs_url": {"type": "string"},
                },
            }
        },
    },
)


#: Agenda items handed to a scout in one call. See the comment in _impl.
_MAX_ITEMS = 25

#: Items we look up attachments for. Each is one HTTP round trip, so this is a
#: time budget as much as a size one.
_ATTACHMENT_LOOKUPS = 25


def _sort_key(item: dict[str, Any]) -> str:
    """Sort agenda items newest-first on their date string.

    BoardDocs dates arrive ISO-shaped, where lexical order is chronological. A
    missing or malformed date sorts to the end rather than raising — losing an
    item to a formatting quirk would be the worse failure.
    """
    return str(item.get("date") or "")


async def _attachments_for(
    district: dict[str, Any],
    items: list[dict[str, Any]],
    http: ScoutHttpClient,
) -> dict[str, list[str]]:
    """Attachment URLs per ``item_unique``, best-effort.

    Without this a scout has only the item's ``goto`` permalink to hand to
    ``pdf_extractor.extract`` -- and that is an HTML page, so every attempt came
    back "Data format error" or 403. The documents themselves live behind a
    separate endpoint; see ``fetch_agenda_item_files``.

    Failures are swallowed per item: an item with no readable attachment is still
    worth reporting, and this must never cost us the agenda.
    """
    base = _boarddocs_base(str(district.get("boarddocs_url") or ""))
    if not base:
        return {}
    found: dict[str, list[str]] = {}
    for item in items[:_ATTACHMENT_LOOKUPS]:
        unique = str(item.get("item_unique") or "")
        if not unique:
            continue
        try:
            urls = await fetch_agenda_item_files(base, unique, http)
        except Exception as exc:  # noqa: BLE001 - best effort by design
            logger.debug("board_minutes.fetch: attachments for %s failed - %s", unique, exc)
            continue
        if urls:
            found[unique] = urls
    return found


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        district: dict[str, Any] = arguments.get("district") or {}
        if not district.get("boarddocs_url"):
            return json.dumps([])
        try:
            async with ScoutHttpClient(timeout=30.0) as http:
                items = await fetch_boarddocs(district, http)
                # Newest first, then capped. Trimming each item's text was never
                # enough on its own: one run pulled 273 agenda items across three
                # districts, which at 2,000 characters apiece is half a million
                # characters in one tool result -- past the point where the
                # harness spills it to a file and the scout stalls holding a
                # filename. Capping an unsorted list would drop this month's
                # meetings to keep last year's, so sort first.
                items = sorted(items, key=_sort_key, reverse=True)[:_MAX_ITEMS]
                attachments = await _attachments_for(district, items, http)
        except Exception as exc:
            logger.warning("board_minutes.fetch: error — %s", exc)
            return json.dumps([])
        trimmed = [
            {
                "title": it.get("title", ""),
                "date": it.get("date", ""),
                "source_url": it.get("source_url", ""),
                "text": (it.get("text", ""))[:2000],
                "speaker_attribution": it.get("speaker_attribution"),
                "attachment_urls": attachments.get(str(it.get("item_unique") or ""), []),
            }
            for it in items
        ]
        return json.dumps(trimmed)

    return (_DEF, _impl)


register_tool("board_minutes.fetch", _factory)
