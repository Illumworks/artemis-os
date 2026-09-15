"""Populate district_contacts from Salesforce.

**Why this exists.** On 2026-09-15 Jon asked Callie for a New Mexico contact and
she said there were none. True of `district_contacts`, which held seven rows —
six of them test leftovers on unreachable `.example` addresses — for all 13,466
districts. It is the reason 1,095 signals sit at `unrouted_no_contact`: not a
routing bug, an empty table.

Salesforce already holds what is missing. Probed the same day:

| | |
|---|---|
| Contacts | 793,169 |
| …with an email | 706,672 |
| …not hard-bounced, mapped to an NCES district | **671,478** |
| New Mexico alone | **7,653** |

So this reads from the source of record on a cycle rather than loading a export
that is stale the day it lands.

**The join is `Account.NCES_District_ID__c`.** Salesforce stores schools as well
as districts, so 1,137 New Mexico accounts collapse to about 200 district ids, of
which **130 match our districts table — exactly the number of New Mexico
districts we hold.** Unmatched ids are mostly charters carrying a state-assigned
number that is not in the federal district file; they are reported, never guessed
at.

**Read-only, and it stays that way.** Nothing here writes to Salesforce, by Jon's
instruction.

**What it deliberately does NOT do:** decide anyone may be emailed. A contact
existing in a CRM is not consent to market to them, and that judgement belongs to
whoever owns deliverability, not to a sync job. Loading these fixes ROUTING —
whether a signal has somebody behind it — and the send path still runs through
the approval gate and the Salesforce suppression check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.deliverable import is_deliverable
from artemis.marketing.models import DistrictContact

logger = logging.getLogger(__name__)

#: Rows this sync owns. Manual and Argus contacts are never touched.
_SOURCE = "salesforce"

#: Fields we read. Kept minimal on purpose — a contact record carries 174 fields
#: and we want four of them. Everything else stays in Salesforce.
_FIELDS = (
    "Id",
    "Name",
    "Title",
    "Email",
    "Phone",
    "Account.NCES_District_ID__c",
    "LastModifiedDate",
)


@dataclass
class SyncResult:
    """What one pass did, in terms an operator can act on."""

    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_undeliverable: int = 0
    skipped_unmapped: int = 0
    skipped_duplicate: int = 0
    unmapped_nces_ids: set[str] = field(default_factory=set)
    dry_run: bool = True
    since: datetime | None = None

    def summary(self) -> str:
        mode = "DRY RUN — nothing written" if self.dry_run else "written"
        return (
            f"{mode}: fetched {self.fetched:,}, inserted {self.inserted:,}, "
            f"updated {self.updated:,}, skipped {self.skipped_undeliverable:,} "
            f"undeliverable, {self.skipped_unmapped:,} with no matching district "
            f"({len(self.unmapped_nces_ids):,} distinct NCES ids) and "
            f"{self.skipped_duplicate:,} duplicate district+email rows"
        )


def _soql(*, states: tuple[str, ...] | None, since: datetime | None, limit: int | None) -> str:
    """Build the contact query.

    `IsEmailBounced = false` is a filter rather than a post-check so Salesforce
    does the work: 1,962 contacts are hard-bounced and there is no reason to page
    them across the network to drop them here.
    """
    where = [
        "Email != null",
        "IsEmailBounced = false",
        "Account.NCES_District_ID__c != null",
    ]
    if states:
        joined = ", ".join(f"'{s.upper()}'" for s in states)
        where.append(f"Account.BillingState IN ({joined})")
    if since is not None:
        # SOQL wants an ISO-8601 instant with an offset, unquoted.
        where.append(f"LastModifiedDate > {since.astimezone().isoformat(timespec='seconds')}")
    query = f"SELECT {', '.join(_FIELDS)} FROM Contact WHERE {' AND '.join(where)}"
    if limit:
        query += f" LIMIT {int(limit)}"
    return query


async def _nces_to_district_id(session: AsyncSession) -> dict[str, int]:
    """Our districts keyed by NCES id, for mapping Salesforce accounts."""
    from artemis.marketing.models import District

    rows = (await session.execute(select(District.nces_id, District.id))).all()
    return {str(nces).strip(): did for nces, did in rows if nces}


async def last_synced_at(session: AsyncSession) -> datetime | None:
    """The watermark for an incremental pass.

    Derived from the rows themselves rather than kept in a separate table, so it
    cannot drift out of step with what was actually written — and a restored or
    partially-loaded database self-corrects instead of silently skipping.
    """
    watermark: datetime | None = await session.scalar(
        select(func.max(DistrictContact.updated_at)).where(DistrictContact.source == _SOURCE)
    )
    return watermark


async def sync_district_contacts(
    session: AsyncSession,
    *,
    client: Any,
    states: tuple[str, ...] | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    dry_run: bool = True,
) -> SyncResult:
    """Pull Salesforce contacts into `district_contacts`.

    Defaults to a dry run. Nothing about loading two-thirds of a million contacts
    should happen because somebody called a function with the arguments they
    happened to have.
    """
    result = SyncResult(dry_run=dry_run, since=since)
    mapping = await _nces_to_district_id(session)
    records = await client.query_all(_soql(states=states, since=since, limit=limit))
    result.fetched = len(records)

    rows: list[dict[str, Any]] = []
    for rec in records:
        email = (rec.get("Email") or "").strip()
        if not is_deliverable(email):
            # Salesforce filters the hard bounces; this catches the reserved and
            # malformed addresses it happily stores.
            result.skipped_undeliverable += 1
            continue
        account = rec.get("Account") or {}
        nces = str(account.get("NCES_District_ID__c") or "").strip()
        district_id = mapping.get(nces)
        if district_id is None:
            result.skipped_unmapped += 1
            if nces:
                result.unmapped_nces_ids.add(nces)
            continue
        rows.append(
            {
                "district_id": district_id,
                "name": (rec.get("Name") or "").strip() or email,
                "title": (rec.get("Title") or "").strip() or None,
                "email": email,
                "phone": (rec.get("Phone") or "").strip() or None,
                "source": _SOURCE,
                "external_id": str(rec.get("Id") or "").strip() or None,
                "active": True,
            }
        )

    rows, collapsed = _one_per_district_email(rows)
    result.skipped_duplicate = collapsed

    if dry_run:
        result.inserted = len(rows)
        logger.info("salesforce_contact_sync: %s", result.summary())
        return result

    for row in rows:
        stmt = (
            pg_insert(DistrictContact)
            .values(**row)
            # Keyed on the Salesforce id, so somebody who changes district or
            # title updates in place instead of accumulating duplicates. The
            # index is partial (external_id IS NOT NULL), so the predicate has
            # to be repeated here or Postgres will not use it as the arbiter.
            .on_conflict_do_update(
                index_elements=["source", "external_id"],
                index_where=DistrictContact.external_id.isnot(None),
                set_={
                    "district_id": row["district_id"],
                    "name": row["name"],
                    "title": row["title"],
                    "email": row["email"],
                    "phone": row["phone"],
                    "active": True,
                    "updated_at": func.now(),
                },
            )
            .returning(DistrictContact.created_at, DistrictContact.updated_at)
        )
        returned = (await session.execute(stmt)).first()
        if returned is not None and returned[0] == returned[1]:
            result.inserted += 1
        else:
            result.updated += 1

    logger.info("salesforce_contact_sync: %s", result.summary())
    return result


def _one_per_district_email(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse rows that share a district and an email address.

    A second unique index — ``(district_id, lower(email)) WHERE active`` — would
    otherwise abort the load, and Salesforce genuinely holds the same person more
    than once: 249 of 7,095 New Mexico rows collided, one address four times.

    First wins, and the query orders by nothing in particular, so this is stable
    only in the sense that it always produces ONE row. Which duplicate survives
    is not meaningful — they are the same person with the same address, and the
    Salesforce id of the survivor becomes the external key.
    """
    seen: set[tuple[int, str]] = set()
    kept: list[dict[str, Any]] = []
    collapsed = 0
    for row in rows:
        key = (int(row["district_id"]), str(row["email"]).strip().lower())
        if key in seen:
            collapsed += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, collapsed
