"""Async HTTP clients for the Federal Funding Scout.

Three independent data sources:
- Federal Register API (free, no key)
- Grants.gov search API (key optional; higher rate limit with key)
- ED.gov press-releases RSS feed (free, no key)
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

import defusedxml.ElementTree as SafeET
from pydantic import BaseModel, ConfigDict

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

_FEDERAL_REGISTER_BASE = "https://www.federalregister.gov/api/v1/"
_GRANTS_GOV_SEARCH_URL = "https://apply07.grants.gov/grantsws/rest/opportunities/search/"
_ED_GOV_RSS_URL = "https://www.ed.gov/news/press-releases/feed"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class FedRegDocument(BaseModel):
    """A single Federal Register document."""

    model_config = ConfigDict(populate_by_name=True)

    document_number: str
    title: str
    abstract: str = ""
    publication_date: str = ""
    html_url: str = ""


class GrantOpportunity(BaseModel):
    """A single Grants.gov opportunity."""

    model_config = ConfigDict(populate_by_name=True)

    opportunity_id: str
    title: str
    agency_name: str = ""
    close_date: str | None = None
    award_floor: int | None = None
    synopsis: str | None = None


class RssItem(BaseModel):
    """A single RSS item from ED.gov."""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    link: str = ""
    description: str = ""
    pub_date: str | None = None


# ---------------------------------------------------------------------------
# FederalRegisterClient
# ---------------------------------------------------------------------------


class FederalRegisterClient:
    """Async client for the Federal Register public API.

    Parameters
    ----------
    _http:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    """

    def __init__(self, *, _http: ScoutHttpClient | None = None) -> None:
        self._http = _http or ScoutHttpClient(
            base_url=_FEDERAL_REGISTER_BASE,
            rate_limit=5.0,
        )

    async def search(self, keywords: list[str], since_days: int = 30) -> list[FedRegDocument]:
        """Search Federal Register documents for the given keywords.

        Parameters
        ----------
        keywords:
            Terms to search for (joined with ``+``).
        since_days:
            Only return documents published within this many days.

        Returns
        -------
        list[FedRegDocument]
            Parsed documents; empty list on non-200 or parse errors.
        """
        since_date = (date.today() - timedelta(days=since_days)).isoformat()
        term = " ".join(keywords)
        params: dict[str, str] = {
            "fields[]": "title",
            "fields[1]": "abstract",
            "fields[2]": "publication_date",
            "fields[3]": "html_url",
            "fields[4]": "document_number",
            "conditions[term]": term,
            "conditions[publication_date][gte]": since_date,
            "per_page": "20",
        }
        try:
            resp = await self._http.get("documents.json", params=params)
        except Exception:
            _logger.warning("FederalRegisterClient: HTTP error fetching documents", exc_info=True)
            return []

        if resp.status_code != 200:
            _logger.warning("FederalRegisterClient: non-200 response %d", resp.status_code)
            return []

        try:
            data: dict[str, Any] = resp.json()
        except Exception:
            _logger.warning("FederalRegisterClient: failed to parse JSON response")
            return []

        raw_results: list[Any] = data.get("results", [])
        documents: list[FedRegDocument] = []
        for raw in raw_results:
            try:
                documents.append(FedRegDocument.model_validate(raw))
            except Exception:
                _logger.warning("FederalRegisterClient: failed to parse document from %r", raw)
        return documents

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ---------------------------------------------------------------------------
# GrantsGovClient
# ---------------------------------------------------------------------------


class GrantsGovClient:
    """Async client for the Grants.gov search API.

    Parameters
    ----------
    api_key:
        Optional API key. When provided, adds ``Authorization: Bearer <key>``
        to requests for a higher rate limit. Omit to use the free anonymous tier.
    _http:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    """

    def __init__(
        self,
        api_key: str = "",
        *,
        _http: ScoutHttpClient | None = None,
    ) -> None:
        self._api_key = api_key
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = _http or ScoutHttpClient(
            headers=headers,
            rate_limit=2.0,
        )

    async def search(self, keywords: list[str]) -> list[GrantOpportunity]:
        """Search open and forecasted grant opportunities.

        Parameters
        ----------
        keywords:
            Terms to include in the keyword search string.

        Returns
        -------
        list[GrantOpportunity]
            Parsed opportunities; empty list on non-200 or parse errors.
        """
        keyword_str = " ".join(keywords)
        body: dict[str, Any] = {
            "keyword": keyword_str,
            "rows": 20,
            "oppStatuses": "forecasted|posted",
        }
        try:
            resp = await self._http.post(_GRANTS_GOV_SEARCH_URL, json=body)
        except Exception:
            _logger.warning("GrantsGovClient: HTTP error fetching opportunities", exc_info=True)
            return []

        if resp.status_code != 200:
            _logger.warning("GrantsGovClient: non-200 response %d", resp.status_code)
            return []

        try:
            data: dict[str, Any] = resp.json()
        except Exception:
            _logger.warning("GrantsGovClient: failed to parse JSON response")
            return []

        raw_opps: list[Any] = data.get("oppHits", [])
        opportunities: list[GrantOpportunity] = []
        for raw in raw_opps:
            try:
                opportunities.append(
                    GrantOpportunity(
                        opportunity_id=str(raw.get("id", "")),
                        title=str(raw.get("title", "")),
                        agency_name=str(raw.get("agencyName", "")),
                        close_date=raw.get("closeDate"),
                        award_floor=raw.get("awardFloor"),
                        synopsis=raw.get("synopsis"),
                    )
                )
            except Exception:
                _logger.warning("GrantsGovClient: failed to parse opportunity from %r", raw)
        return opportunities

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ---------------------------------------------------------------------------
# EdGovRssClient
# ---------------------------------------------------------------------------


class EdGovRssClient:
    """Async client that fetches and parses ED.gov press-release RSS.

    Parameters
    ----------
    _http:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    """

    def __init__(self, *, _http: ScoutHttpClient | None = None) -> None:
        self._http = _http or ScoutHttpClient(
            rate_limit=1.0,
        )

    async def fetch(self) -> list[RssItem]:
        """Fetch and parse the ED.gov press-releases RSS feed.

        Returns
        -------
        list[RssItem]
            Parsed RSS items; empty list on HTTP or XML errors.
        """
        try:
            resp = await self._http.get(_ED_GOV_RSS_URL)
        except Exception:
            _logger.warning("EdGovRssClient: HTTP error fetching RSS feed", exc_info=True)
            return []

        if resp.status_code != 200:
            _logger.warning("EdGovRssClient: non-200 response %d", resp.status_code)
            return []

        try:
            # defusedxml: rejects entity/DTD tricks in untrusted feed XML.
            root = SafeET.fromstring(resp.text)
        except (ET.ParseError, ValueError):
            _logger.critical("EdGovRssClient: RSS XML parse failure — signals may be missed")
            return []

        items: list[RssItem] = []
        for item_el in root.iter("item"):
            title_el = item_el.find("title")
            link_el = item_el.find("link")
            desc_el = item_el.find("description")
            pub_el = item_el.find("pubDate")
            title = title_el.text or "" if title_el is not None else ""
            link = link_el.text or "" if link_el is not None else ""
            description = desc_el.text or "" if desc_el is not None else ""
            pub_date = pub_el.text if pub_el is not None else None
            try:
                items.append(
                    RssItem(
                        title=title,
                        link=link,
                        description=description,
                        pub_date=pub_date,
                    )
                )
            except Exception:
                _logger.warning("EdGovRssClient: failed to parse RSS item")
        return items

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
