"""Tool: state_doe.fetch

Fetches state Department of Education RSS items for a given state.
Reuses artemis.scouts.state_doe.sources.fetch_doe_rss.

**Why this wrapper trims, and the shared fetch does not.** The raw feed for one
state runs to ~80,000 characters, and the scout asks for six states in a turn:
441,000 characters, roughly 110,000 tokens, in a single tool result. Past a
threshold in that band the harness spills the result to a file, which is exactly
what the traces show — "five of the six state fetches returned data too large to
parse inline (saved to files), while Missouri's data came through" — and the
agent then stalls holding a filename. `state_doe` produced 26 inconclusive runs in
the 30 days to 2026-09-14, and this is why.

Three things come out, and the third is the one that matters:

- **`summary` is a duplicate.** It is 48% of the payload, and stripped of its
  markup it reads `"<title>  <publisher>"` — both already in `title`, which ends
  " - Publisher". It also embeds the item's own link a second time. Measured
  across MD and FL: every single item, no exceptions.
- **`_source_type` is a constant** repeated per item.
- **Most items are old.** The feed is a merge of several sources and is NOT
  sorted — Maryland's third item is from July and its tail reaches back to 2024.
  Of 445 items across the six states, **38 were published in the last 42 days**.
  A daily scout was re-reading two years of history every run.

Filtering to recent items cuts 441,000 characters to 16,700 — 96% — while keeping
every item a daily scan could act on. That is a better instrument than a count
cap, which on an unsorted feed would have thrown away September to keep 2024.

**`link` is never touched — not shortened, not resolved, not truncated.**
`artemis/tools/_source_url_check.py` records 149 fabricated `state_doe` signals
caught precisely because a real Google News id is long and opaque. That field is
30% of the payload and it stays exactly as the feed gave it.

The trimming lives here rather than in `fetch_doe_rss` because six other callers
share that function — the deterministic `StateDoEScout`, `regional_news`, the
leadership aggregator and Argus — and `state_doe/mapping.py` reads `summary` for
keyword matching. This wrapper serves the LLM scout, which has different needs
from a deterministic pass.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.state_doe.sources import fetch_doe_rss
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

#: Matches the recency window the news tool settled on. A scout runs daily; an
#: item older than this has either been seen already or is not news.
_DEFAULT_DAYS = 42
_MAX_DAYS = 365

#: Backstop for a feed that floods with genuinely recent items. Applied AFTER
#: sorting newest-first, so it can only ever drop the oldest of the fresh ones.
_MAX_ITEMS = 40

#: Passed through verbatim. `summary` and `_source_type` are dropped; see module
#: docstring for the measurement.
_KEEP_FIELDS = ("title", "link", "published")

_DEF = Tool(
    name="state_doe.fetch",
    description=(
        "Fetch recent news items from a state Department of Education RSS feed. "
        "Returns items as JSON [{title, link, published}], newest first. "
        f"Only items published within the last {_DEFAULT_DAYS} days are returned "
        "unless you widen 'days' — the feeds carry years of history. "
        "'link' is the item's exact URL: cite it character for character, even "
        "when it is a long opaque redirect. "
        "Returns [] if the state is not configured, on any error, or when "
        "nothing recent was published — an empty result is a real answer."
    ),
    input_schema={
        "type": "object",
        "required": ["state"],
        "properties": {
            "state": {
                "type": "string",
                "description": "2-letter US state code, e.g. 'FL'.",
            },
            "days": {
                "type": "integer",
                "description": (
                    f"How far back to look. Default {_DEFAULT_DAYS}, max {_MAX_DAYS}. "
                    "Widen only when a narrower scan found nothing and you need history."
                ),
            },
        },
    },
)


def _published(item: dict[str, Any]) -> datetime | None:
    """The item's publication time, or None when it cannot be read."""
    raw = str(item.get("published") or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _recent_first(items: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    """Recent items, newest first, trimmed to the fields the scout needs.

    An item whose date will not parse is KEPT. We cannot show it is stale, and
    dropping intelligence on a formatting quirk is the more expensive mistake;
    it sorts to the end rather than jumping the queue.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for item in items:
        when = _published(item)
        if when is None:
            undated.append(item)
        elif when >= cutoff:
            dated.append((when, item))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    ordered = [item for _, item in dated] + undated
    return [{k: item.get(k) for k in _KEEP_FIELDS if k in item} for item in ordered[:_MAX_ITEMS]]


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        state: str = arguments.get("state", "").upper()
        if not state:
            return json.dumps([])
        raw_days = arguments.get("days")
        try:
            days = int(raw_days) if raw_days is not None else _DEFAULT_DAYS
        except (TypeError, ValueError):
            days = _DEFAULT_DAYS
        days = max(1, min(days, _MAX_DAYS))
        try:
            async with ScoutHttpClient(timeout=20.0) as http:
                items = await fetch_doe_rss(state, http)
        except Exception as exc:
            logger.warning("state_doe.fetch(%s): error — %s", state, exc)
            return json.dumps([])
        trimmed = _recent_first(items, days)
        logger.info(
            "state_doe.fetch(%s): %d items from the feed, %d within %dd",
            state,
            len(items),
            len(trimmed),
            days,
        )
        return json.dumps(trimmed)

    return (_DEF, _impl)


register_tool("state_doe.fetch", _factory)
