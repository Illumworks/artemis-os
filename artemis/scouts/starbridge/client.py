"""Starbridge async HTTP client, written against the real API.

**What was here before was fiction.** Every endpoint, field and filter carried
``TODO: confirm with Starbridge team``, and the base URL was
``https://api.starbridge.io/v1/`` -- a host that does not resolve, on a domain
that belongs to someone else. The company is ``starbridge.ai``. Nothing in this
module could ever have returned a single row.

The real surface, confirmed 2026-09-04 against the published OpenAPI 3.1 spec at
``https://dashboard.starbridge.ai/swagger/documentation.yaml`` and exercised
live: base ``https://dashboard.starbridge.ai``, bearer auth, endpoints under
``/api/external/``.

**The model is a feed, not a search.** This is the part the old code got most
wrong. Starbridge does not answer free-text queries; an organisation configures
*bridges* (standing monitors -- RFP, Meeting, Buyer, Contact, Purchase) and the
API hands back the rows those bridges matched. Ours has 68 bridges holding
174,544 rows. So the integration reads a feed and filters locally; it does not
send search terms and hope.

**Duplication is inherent.** Overlapping bridges re-report the same event: 50
signals off the top feed carried only 28 distinct titles. Anything consuming this
must dedupe or the same Kansas RFP arrives four times.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

_STARBRIDGE_BASE = "https://dashboard.starbridge.ai"

#: Column names Starbridge puts on a bridge row. Read by NAME rather than by
#: position: bridges are user-configured, so column order differs between them
#: and an index would silently read "Match Score" as a summary on some bridges.
_COL_SUMMARY = "Match reasoning"
_COL_RELEVANCE = "Summarized Relevance"
_COL_SCORE = "Match Score"
_COL_BUYER = "Buyer Name"
_COL_SOURCE_URL = "Source Url"
_COL_ADDED = "Added to Bridge"


class StarbridgeUnavailableError(Exception):
    """The API could not be reached or refused us -- NOT "there were no results".

    Before this existed, ``search`` returned ``[]`` on a 401, a 404, a wrong base
    URL and any exception alike, so a completely unconfigured integration
    reported "0 signals" in exactly the words a working one uses on a quiet day.
    Given the base URL was a hostname that does not exist, that is precisely what
    it would have done, forever.

    That is how Argus sat idle for five weeks while its progress was relayed in
    good faith. A caller must be able to tell "nothing is happening in the world"
    from "nothing is happening here".
    """


class StarbridgeItem(BaseModel):
    """One signal, normalised. A signal is a bridge (monitor) plus a matched row."""

    model_config = ConfigDict(populate_by_name=True)

    item_id: str
    title: str
    summary: str | None = None
    item_type: str = "unknown"
    state: str | None = None
    deadline_date: str | None = None
    source_url: str | None = None

    #: Starbridge's own 1-5 relevance score for this row against its bridge.
    match_score: int | None = None
    buyer_name: str | None = None
    bridge_name: str | None = None


class StarbridgeBridge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bridge_id: str
    name: str
    filter_type: str
    status: str
    row_count: int = 0


def _cell_values(signal: dict[str, Any]) -> dict[str, Any]:
    """Flatten one signal's row into ``{column name: value}``.

    Row cells come back keyed by column NAME, while ``bridge.columns`` lists the
    same columns with both an id and a name. Both spellings are accepted here: a
    key that matches a known ``columnId`` is translated, anything else is taken
    as the name it appears to be.

    That tolerance is not hypothetical caution. Reading the live payload, a debug
    line printed ``names.get(key, key)`` -- falling back to echoing the key --
    which made an id-keyed join look like it worked when it had never run. The
    spec is explicitly "subject to change before finalization of a 1.0", so the
    envelope may yet move again.
    """
    bridge = signal.get("bridge") or {}
    row = signal.get("row") or {}
    names = {c.get("columnId"): c.get("name") for c in (bridge.get("columns") or [])}
    cells = row.get("columns") or {}
    if not isinstance(cells, dict):
        return {}
    out: dict[str, Any] = {}
    for key, cell in cells.items():
        name = names.get(key, key)
        if name and isinstance(cell, dict):
            out[str(name)] = cell.get("value")
    return out


def signal_to_item(signal: dict[str, Any]) -> StarbridgeItem:
    """Normalise one feed signal. Pure -- no I/O."""
    bridge = signal.get("bridge") or {}
    row = signal.get("row") or {}
    values = _cell_values(signal)

    score = values.get(_COL_SCORE)
    try:
        match_score = int(score) if score is not None else None
    except (TypeError, ValueError):
        match_score = None

    # Prefer the fuller "Summarized Relevance" bullets; fall back to the one-line
    # reasoning. Either is Starbridge's own text about why this row matched.
    summary = values.get(_COL_RELEVANCE) or values.get(_COL_SUMMARY)

    return StarbridgeItem(
        item_id=str(row.get("rowId") or ""),
        title=str(row.get("name") or ""),
        summary=str(summary) if summary else None,
        item_type=str(bridge.get("filterType") or "unknown").lower(),
        source_url=(str(values[_COL_SOURCE_URL]) if values.get(_COL_SOURCE_URL) else None),
        deadline_date=(str(values[_COL_ADDED]) if values.get(_COL_ADDED) else None),
        match_score=match_score,
        buyer_name=(str(values[_COL_BUYER]) if values.get(_COL_BUYER) else None),
        bridge_name=str(bridge.get("name") or ""),
    )


class StarbridgeClient:
    """Read-only client for the Starbridge external API."""

    def __init__(self, api_key: str, *, _http: ScoutHttpClient | None = None) -> None:
        self._api_key = api_key
        self._http = _http or ScoutHttpClient(
            base_url=_STARBRIDGE_BASE,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            rate_limit=2.0,
        )

    def _require_key(self) -> None:
        if not self._api_key:
            raise ValueError(
                "STARBRIDGE_API_KEY not set. "
                "Set the environment variable or pass api_key= explicitly."
            )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._require_key()
        try:
            resp = await self._http.get(path, params=params or {})
        except Exception as exc:
            _logger.exception("Starbridge GET %s failed", path)
            raise StarbridgeUnavailableError(
                f"Starbridge {path} could not be reached ({type(exc).__name__}). "
                "This is not an empty result -- report it as unavailable and do not "
                "say the scan was clear."
            ) from exc

        if resp.status_code >= 400:
            _logger.error("Starbridge GET %s -> HTTP %d", path, resp.status_code)
            hint = ""
            if resp.status_code in (401, 403):
                hint = "The API key was rejected. "
            elif resp.status_code == 404:
                hint = "The endpoint path is wrong. "
            elif resp.status_code == 429:
                hint = "Rate limited. "
            raise StarbridgeUnavailableError(
                f"Starbridge {path} returned HTTP {resp.status_code}. {hint}"
                "This is not an empty result -- report it as unavailable and do not "
                "say the scan was clear."
            )
        return resp.json()

    async def list_bridges(self, *, page_size: int = 50) -> list[StarbridgeBridge]:
        """Every standing monitor configured in the org."""
        out: list[StarbridgeBridge] = []
        page = 1
        while True:
            data = await self._get(
                "/api/external/bridge", {"pageNumber": page, "pageSize": page_size}
            )
            for raw in data.get("result", []):
                out.append(
                    StarbridgeBridge(
                        bridge_id=str(raw.get("bridgeId") or ""),
                        name=str(raw.get("name") or ""),
                        filter_type=str(raw.get("filterType") or ""),
                        status=str(raw.get("status") or ""),
                        row_count=int(raw.get("rowCount") or 0),
                    )
                )
            if page >= int(data.get("totalPages") or 1):
                return out
            page += 1

    async def top_signals(self, *, limit: int = 50, sort: str = "Hotness") -> list[StarbridgeItem]:
        """Recent signals across the whole org, best first.

        Deduplicated on title: overlapping bridges re-report the same event, and
        50 raw rows carried only 28 distinct titles when measured. The first
        occurrence wins, so the highest-scoring copy is the one kept.
        """
        data = await self._get(
            "/api/external/feed/all/top-signals", {"pageSize": limit, "sort": sort}
        )
        seen: set[str] = set()
        items: list[StarbridgeItem] = []
        for raw in data.get("result", []):
            try:
                item = signal_to_item(raw)
            except Exception:
                _logger.warning("Failed to normalise Starbridge signal", exc_info=True)
                continue
            key = item.title.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)
        return items

    async def search_buyers(self, name: str, *, state: str | None = None) -> list[dict[str, Any]]:
        """Find a public-sector buyer by name. Returns candidates, never picks one."""
        params: dict[str, Any] = {"buyerName": name, "limit": "10"}
        if state:
            params["buyerStateCode"] = state
        data = await self._get("/api/external/buyer/quick/search", params)
        result = data.get("result", data)
        return list(result) if isinstance(result, list) else []
