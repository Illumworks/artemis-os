"""Starbridge async HTTP client for the Starbridge Researcher Scout.

Wraps ScoutHttpClient to provide typed methods for the two Starbridge
operations used by this scout: ``search`` and ``get_document``.

NOTE: The Starbridge API shape is not yet confirmed with the vendor.
All ambiguous fields and endpoints are marked with
``# TODO: confirm with Starbridge team``.

Rate limit: scout runs on a 4-hour cadence; no strict per-request limit
documented. Using rate_limit=2.0 (2 req/s) as a conservative default.

All API calls include ``bench_test_period=True`` in the request body for
usage tracking during the renewal-decision window.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# TODO: confirm with Starbridge team
_STARBRIDGE_BASE = "https://api.starbridge.io/v1/"


class StarbridgeItem(BaseModel):
    """Lightweight item record returned by Starbridge search."""

    model_config = ConfigDict(populate_by_name=True)

    item_id: str
    title: str
    summary: str | None = None
    item_type: str = "unknown"  # TODO: confirm with Starbridge team
    state: str | None = None
    deadline_date: str | None = None  # TODO: confirm field name
    source_url: str | None = None  # TODO: confirm field name


class StarbridgeDocument(BaseModel):
    """Full document record returned by Starbridge get_document."""

    model_config = ConfigDict(populate_by_name=True)

    doc_id: str
    title: str
    content: str
    metadata: dict[str, Any] = {}  # TODO: confirm schema


class StarbridgeClient:
    """Async client for the Starbridge API.

    Parameters
    ----------
    api_key:
        Starbridge API key. If empty, ``search`` and ``get_document`` will
        raise ``ValueError``. Upstream scout logic should guard against calling
        this with an empty key.
    _http:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    """

    def __init__(
        self,
        api_key: str,
        *,
        _http: ScoutHttpClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._http = _http or ScoutHttpClient(
            base_url=_STARBRIDGE_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            rate_limit=2.0,
        )

    def _require_key(self) -> None:
        """Raise ValueError if API key is not set."""
        if not self._api_key:
            raise ValueError(
                "STARBRIDGE_API_KEY not set. "
                "Set the environment variable or pass api_key= explicitly."
            )

    async def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
    ) -> list[StarbridgeItem]:
        """Search Starbridge for items matching *query*.

        Parameters
        ----------
        query:
            Free-text search query.
        filters:
            Optional filter dict sent in the request body.
            # TODO: confirm filter schema with Starbridge team

        Returns
        -------
        list[StarbridgeItem]
            Parsed item list. Returns empty list on HTTP errors.
        """
        self._require_key()

        # TODO: confirm with Starbridge team — exact endpoint path
        body: dict[str, Any] = {
            "query": query,
            "filters": filters or {},
            "bench_test_period": True,  # tag all bench-test API calls
        }

        try:
            resp = await self._http.post(
                "search",  # TODO: confirm with Starbridge team
                json=body,
            )
        except Exception:
            _logger.exception("Starbridge search request failed for query=%r", query)
            return []

        if resp.status_code >= 400:
            _logger.warning(
                "Starbridge search → HTTP %d for query=%r",
                resp.status_code,
                query,
            )
            return []

        data: Any = resp.json()
        # TODO: confirm with Starbridge team — response envelope shape
        raw_items: list[Any] = data if isinstance(data, list) else data.get("items", [])

        items: list[StarbridgeItem] = []
        for raw in raw_items:
            try:
                items.append(StarbridgeItem.model_validate(raw))
            except Exception:
                _logger.warning("Failed to parse StarbridgeItem from %r", raw)
        return items

    async def get_document(self, doc_id: str) -> StarbridgeDocument:
        """Fetch a full document by ID.

        Parameters
        ----------
        doc_id:
            Starbridge document identifier.

        Returns
        -------
        StarbridgeDocument
            Parsed document record.

        Raises
        ------
        ValueError
            If API key is not set.
        httpx.HTTPStatusError
            Propagated on non-2xx responses (after retries).
        """
        self._require_key()

        # TODO: confirm with Starbridge team — exact endpoint path
        resp = await self._http.get(
            f"documents/{doc_id}",  # TODO: confirm with Starbridge team
            headers={"bench_test_period": "true"},  # TODO: confirm header vs body
        )
        resp.raise_for_status()

        data: Any = resp.json()
        return StarbridgeDocument.model_validate(data)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> StarbridgeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
