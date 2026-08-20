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
    """Live Amira customer districts from Salesforce, keyed by NCES district id.

    Returns 7-digit NCES DISTRICT ids (e.g. "3500060" = Albuquerque), which is
    the only identifier that joins cleanly: Salesforce Account carries
    ``NCES_District_ID__c`` and our ``districts`` table carries ``nces_id``,
    both 7-digit. Verified live 2026-08-20: 9,177 customer school accounts
    resolve to 3,096 distinct districts, 2,636 of which are in our roster.

    Why NOT the slug ids the peer-scout watch list uses ("FL_pinellas"): that
    convention is hand-authored and not derivable from an account name, so
    matching on it would be guesswork. Callers holding slugs should resolve
    through ``districts`` on ``nces_id``; see ``customer_nces_ids_for_state``.

    IMPORTANT — the field this reads is Amira-specific on purpose. The org is
    shared with Istation (the merged company), so ``Is_Customer__c`` means
    "customer of anything in the portfolio" (11,360 accounts) rather than an
    Amira customer (~4,900). See ``settings.salesforce_customer_field``.

    FAIL SAFE, per this module's contract: on any error it returns the
    last-known good set, or the injected fallback if it has never succeeded.
    A scout run degrades; it never crashes. The trade-off is explicit — an
    empty set means customer districts may emit peer-validation signals, which
    downstream review catches.
    """

    def __init__(
        self,
        fallback_ids: Iterable[str] = (),
        *,
        ttl_seconds: float = 3600.0,
    ) -> None:
        self._fallback = StaticCustomerExclusions(fallback_ids)
        self._cache: set[str] | None = None
        self._fetched_at: float = 0.0
        self._ttl = ttl_seconds

    async def get_customer_district_ids(self) -> set[str]:
        import time

        if self._cache is not None and (time.monotonic() - self._fetched_at) < self._ttl:
            return set(self._cache)
        try:
            fresh = await self._fetch()
        except Exception:
            _logger.warning(
                "SalesforceCustomerExclusions: fetch failed — serving %s",
                "last-known set" if self._cache is not None else "injected fallback",
                exc_info=True,
            )
            if self._cache is not None:
                return set(self._cache)
            return await self._fallback.get_customer_district_ids()

        self._cache = fresh
        self._fetched_at = time.monotonic()
        _logger.info(
            "SalesforceCustomerExclusions: %d customer district id(s) refreshed", len(fresh)
        )
        return set(fresh)

    async def _fetch(self) -> set[str]:
        import artemis.db as _db
        from artemis.config import settings
        from artemis.integrations.config_resolver import resolve_salesforce_config
        from artemis.integrations.salesforce.client import SalesforceClient, fetch_access_token

        field = settings.salesforce_customer_field
        truthy = [
            v.strip() for v in settings.salesforce_customer_truthy_values.split(",") if v.strip()
        ]

        async with _db.SessionLocal() as session:
            cfg = await resolve_salesforce_config(session)
        token = await fetch_access_token(
            login_url=cfg.login_url, client_id=cfg.client_id, client_secret=cfg.client_secret
        )
        client = SalesforceClient(token.instance_url, token.access_token)

        if truthy:
            values = ",".join("'" + v.replace("'", r"\'") + "'" for v in truthy)
            predicate = f"{field} IN ({values})"
        else:
            predicate = f"{field} = true"

        # query_all, not query: >2,000 distinct districts means a single page
        # silently truncates and GROUP BY is rejected outright by Salesforce.
        rows = await client.query_all(
            f"SELECT NCES_District_ID__c FROM Account "
            f"WHERE {predicate} AND NCES_District_ID__c != null"
        )
        return {
            normalize_district_id(str(r["NCES_District_ID__c"]))
            for r in rows
            if r.get("NCES_District_ID__c")
        }
