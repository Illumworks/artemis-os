"""Federal Funding Scout — collects federal grant signals from three sources.

Data sources polled each cycle:
1. Federal Register API  (free, no key)
2. Grants.gov search API (key optional)
3. ED.gov press-release RSS feed (free, no key)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, ClassVar

import httpx

from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.federal_funding.client import (
    EdGovRssClient,
    FederalRegisterClient,
    GrantsGovClient,
)
from artemis.scouts.federal_funding.mapping import (
    fed_reg_to_finding,
    grant_to_finding,
    rss_item_to_finding,
)

_logger = logging.getLogger(__name__)

LITERACY_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "biliteracy",
    "assessment",
    "curriculum",
    "clsd",
    "esser",
    "title i",
    "idea",
]


class FederalFundingScout(BaseScout):
    """Scout that tracks federal grant cycles and funding announcements.

    Polls Federal Register, Grants.gov, and ED.gov RSS concurrently and
    deduplicates findings by title before emitting.
    """

    scout_type: ClassVar[str] = "federal_funding_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        grants_api_key: str = "",
        _fed_reg_client: FederalRegisterClient | None = None,
        _grants_client: GrantsGovClient | None = None,
        _rss_client: EdGovRssClient | None = None,
        _client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config, _client=_client)
        api_key = grants_api_key or os.getenv("GRANTS_GOV_API_KEY", "")
        self._fed_reg = _fed_reg_client or FederalRegisterClient()
        self._grants = _grants_client or GrantsGovClient(api_key=api_key)
        self._rss = _rss_client or EdGovRssClient()

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect findings from all three sources concurrently.

        Per-source exceptions are caught and logged; other sources continue.
        Results are deduplicated by evidence title to avoid emitting the same
        grant from multiple sources.
        """
        fed_reg_task = asyncio.create_task(self._fed_reg.search(LITERACY_KEYWORDS, since_days=30))
        grants_task = asyncio.create_task(self._grants.search(LITERACY_KEYWORDS))
        rss_task = asyncio.create_task(self._rss.fetch())

        results = await asyncio.gather(fed_reg_task, grants_task, rss_task, return_exceptions=True)

        fed_reg_result, grants_result, rss_result = results

        findings: list[dict[str, Any]] = []
        seen_titles: set[str] = set()

        # --- Federal Register ---
        if isinstance(fed_reg_result, BaseException):
            _logger.warning(
                "FederalFundingScout: Federal Register source failed: %s", fed_reg_result
            )
        else:
            for doc in fed_reg_result:
                title_key = doc.title.strip().lower()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                findings.append(fed_reg_to_finding(doc))

        # --- Grants.gov ---
        if isinstance(grants_result, BaseException):
            _logger.warning("FederalFundingScout: Grants.gov source failed: %s", grants_result)
        else:
            for grant in grants_result:
                title_key = grant.title.strip().lower()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                findings.append(grant_to_finding(grant))

        # --- ED.gov RSS ---
        if isinstance(rss_result, BaseException):
            _logger.warning("FederalFundingScout: ED.gov RSS source failed: %s", rss_result)
        else:
            for item in rss_result:
                title_key = item.title.strip().lower()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                findings.append(rss_item_to_finding(item))

        _logger.info("FederalFundingScout: gathered %d findings (deduped by title)", len(findings))
        return findings
