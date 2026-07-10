"""Customer-exclusion input for the board peer-validation scout.

Peer-validation signals must come from NON-customer districts — a customer
board discussing Amira is a success story, not peer validation.  The real
customer list will come from Salesforce; until that integration lands the
provider is pluggable and injected.

Design contract:
- ``CustomerExclusionProvider`` — anything with
  ``async get_customer_district_ids() -> set[str]``.
- District identifiers are compared case-insensitively after strip().
- Providers must FAIL SAFE: on error, return the last-known set (or empty)
  rather than raising — a scout run should degrade, not crash.  Note the
  trade-off: an empty exclusion set means customer districts may emit
  peer-validation signals until the Salesforce feed is wired; downstream
  review catches these.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

_logger = logging.getLogger(__name__)


def normalize_district_id(district_id: str) -> str:
    """Canonical comparison form for district identifiers."""
    return district_id.strip().lower()


@runtime_checkable
class CustomerExclusionProvider(Protocol):
    """Anything that can supply the set of customer district identifiers."""

    async def get_customer_district_ids(self) -> set[str]:
        """Return normalized district identifiers that are current customers."""
        ...


class StaticCustomerExclusions:
    """In-memory exclusion set — for tests, config-file injection, and V1."""

    def __init__(self, district_ids: Iterable[str] = ()) -> None:
        self._ids: set[str] = {normalize_district_id(d) for d in district_ids if d and d.strip()}

    async def get_customer_district_ids(self) -> set[str]:
        return set(self._ids)


class SalesforceCustomerExclusions:
    """STUB — will query Salesforce for active customer accounts.

    TODO(salesforce): wire to the Salesforce integration once it exists.
    Expected implementation: query active Account records tagged as districts,
    map to our district_id convention, cache with a TTL.  Until then this
    returns the injected fallback set (default: empty) and logs once.
    """

    def __init__(self, fallback_ids: Iterable[str] = ()) -> None:
        self._fallback = StaticCustomerExclusions(fallback_ids)
        self._warned = False

    async def get_customer_district_ids(self) -> set[str]:
        if not self._warned:
            _logger.warning(
                "SalesforceCustomerExclusions is a stub — using fallback exclusion set "
                "(%d entries). Wire the Salesforce integration to get the real customer list.",
                len(await self._fallback.get_customer_district_ids()),
            )
            self._warned = True
        return await self._fallback.get_customer_district_ids()
