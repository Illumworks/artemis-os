"""LegiScan async HTTP client for the Legislative Scout.

Wraps ScoutHttpClient to provide typed methods for the two LegiScan operations
used by this scout: ``getSearch`` and ``getBill``.

LegiScan free tier: 30k queries/month, ~1 req/sec sustained.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

_LEGISCAN_BASE = "https://api.legiscan.com/"

# LegiScan bill status codes.
STATUS_INTRODUCED = 1
STATUS_ENGROSSED = 2
STATUS_ENROLLED = 3
STATUS_PASSED = 4
STATUS_VETOED = 5
STATUS_FAILED = 6


class BillSummary(BaseModel):
    """Lightweight bill record returned by LegiScan getSearch."""

    model_config = ConfigDict(populate_by_name=True)

    bill_id: int
    number: str
    title: str
    status: int
    last_action: str = ""
    last_action_date: str = ""
    url: str = ""


class Bill(BaseModel):
    """Full bill record returned by LegiScan getBill."""

    model_config = ConfigDict(populate_by_name=True)

    bill_id: int
    bill_number: str
    title: str
    description: str = ""
    status: int
    last_action: str = ""
    state: str = ""
    body: str = ""
    session: dict[str, Any] = Field(default_factory=dict)


class LegiScanClient:
    """Async client for the LegiScan API.

    Parameters
    ----------
    api_key:
        LegiScan API key. If empty and ``dry_run`` is ``False``, any method
        that makes a real HTTP call will raise ``ValueError``.
    dry_run:
        When ``True`` and ``api_key`` is empty, return empty results instead
        of raising.
    _http:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    """

    def __init__(
        self,
        api_key: str = "",
        *,
        dry_run: bool = False,
        _http: ScoutHttpClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._dry_run = dry_run
        self._http = _http or ScoutHttpClient(
            base_url=_LEGISCAN_BASE,
            rate_limit=1.0,
        )

    def _require_key(self) -> None:
        """Raise ValueError if API key is missing and we are not in dry-run mode."""
        if not self._api_key:
            if self._dry_run:
                return
            raise ValueError(
                "LEGISCAN_API_KEY is not set. "
                "Set the environment variable or pass api_key= explicitly."
            )

    async def search(
        self,
        state: str,
        keywords: list[str],
        year: int | None = None,
    ) -> list[BillSummary]:
        """Search bills for a state.

        Parameters
        ----------
        state:
            Two-letter state abbreviation, e.g. ``"FL"``.
        keywords:
            Words to search for (joined with spaces before sending).
        year:
            LegiScan year parameter. ``2`` means current session (default).

        Returns
        -------
        list[BillSummary]
            Parsed bill summaries.  Empty list when key is missing + dry_run.
        """
        self._require_key()
        if not self._api_key and self._dry_run:
            _logger.warning("LegiScanClient: api_key empty, dry_run=True — returning []")
            return []

        query = " ".join(keywords)
        params: dict[str, str | int] = {
            "key": self._api_key,
            "op": "getSearch",
            "state": state,
            "query": query,
            "year": year if year is not None else 2,
        }
        resp = await self._http.get("", params=params)
        data: dict[str, Any] = resp.json()
        if data.get("status") != "OK":
            _logger.warning(
                "LegiScan getSearch returned status=%s for state=%s", data.get("status"), state
            )
            return []

        raw_results = data.get("searchresult", {}).get("results", [])
        summaries: list[BillSummary] = []
        for raw in raw_results:
            try:
                summaries.append(BillSummary.model_validate(raw))
            except Exception:
                _logger.warning("Failed to parse BillSummary from %r", raw)
        return summaries

    async def get_bill(self, bill_id: int) -> Bill:
        """Fetch full bill details.

        Parameters
        ----------
        bill_id:
            LegiScan numeric bill identifier.

        Returns
        -------
        Bill
            Parsed bill record.
        """
        self._require_key()

        params: dict[str, str | int] = {
            "key": self._api_key,
            "op": "getBill",
            "id": bill_id,
        }
        resp = await self._http.get("", params=params)
        data: dict[str, Any] = resp.json()
        if data.get("status") != "OK":
            raise ValueError(
                f"LegiScan getBill returned status={data.get('status')} for bill_id={bill_id}"
            )

        raw_bill: dict[str, Any] = data.get("bill", {})
        return Bill.model_validate(raw_bill)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> LegiScanClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def make_client(*, dry_run: bool = False, _http: ScoutHttpClient | None = None) -> LegiScanClient:
    """Construct a LegiScanClient from the environment."""
    api_key = os.getenv("LEGISCAN_API_KEY", "")
    return LegiScanClient(api_key=api_key, dry_run=dry_run, _http=_http)
