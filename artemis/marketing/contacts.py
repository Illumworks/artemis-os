"""Repository functions for district_contacts.

Two different lossless contracts live in this one table, kept apart by
``source`` (CONTACTS-1):

- 'manual' / 'salesforce' rows: never hard-deleted. Deactivate via
  deactivate_contact(); re-add re-activates the existing row. These are
  real send targets a human explicitly entered.
- 'argus' rows: genuinely, permanently hard-deletable on request via
  delete_contact() / delete_contacts_for_district(). They are people Argus
  read about in research prose and who did not ask to be in this database;
  CLAUDE.md rule 3's lossless guarantee covers memory_observations /
  memory_drawers, never this table. See artemis.argus.contacts for how rows
  are created and DistrictContact's docstring in models.py for why deleting
  one never touches the observation it came from.

All functions call ``await session.flush()``; the caller owns commit.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import District, DistrictContact

logger = logging.getLogger(__name__)

ARGUS_CONTACT_SOURCE = "argus"


async def create_contact(
    session: AsyncSession,
    *,
    district_id: int,
    name: str,
    email: str,
    title: str | None = None,
    phone: str | None = None,
    source: str = "manual",
    external_id: str | None = None,
) -> DistrictContact:
    """Create (or reactivate) a district contact.

    - Normalizes email to lower-case stripped.
    - For source='manual', upserts on (district_id, lower(email)): if an
      inactive row exists, it is reactivated and updated rather than duplicated.
    - Raises ValueError for empty name/email or if the district does not exist.
    """
    name = name.strip()
    email = email.lower().strip()

    if not name:
        raise ValueError("name must not be empty")
    if not email:
        raise ValueError("email must not be empty")
    if source not in ("manual", "salesforce"):
        raise ValueError(f"unknown source {source!r}; must be 'manual' or 'salesforce'")

    # Verify district exists
    district = await session.get(District, district_id)
    if district is None:
        raise ValueError(f"district_id={district_id} does not exist")

    # For manual source: check for existing row (active or inactive) by (district_id, email)
    if source == "manual":
        stmt = select(DistrictContact).where(
            DistrictContact.district_id == district_id,
            DistrictContact.email == email,
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            # Reactivate and update fields if currently inactive, or just ensure active
            existing.active = True
            existing.name = name
            if title is not None:
                existing.title = title
            if phone is not None:
                existing.phone = phone
            if external_id is not None:
                existing.external_id = external_id
            existing.updated_at = datetime.now(tz=UTC)
            await session.flush()
            return existing

    contact = DistrictContact(
        district_id=district_id,
        name=name,
        title=title,
        email=email,
        phone=phone,
        source=source,
        external_id=external_id,
        active=True,
    )
    session.add(contact)
    await session.flush()
    logger.debug(
        "create_contact: district_id=%s email=%s contact_id=%s",
        district_id,
        email,
        contact.id,
    )
    return contact


async def list_contacts_for_district(
    session: AsyncSession,
    district_id: int,
    *,
    active_only: bool = True,
) -> list[DistrictContact]:
    """Return contacts for a district, sorted by id.

    When active_only=True (default), only active contacts are returned.
    """
    stmt = select(DistrictContact).where(DistrictContact.district_id == district_id)
    if active_only:
        stmt = stmt.where(DistrictContact.active.is_(True))
    stmt = stmt.order_by(DistrictContact.id)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def list_active_contacts_for_districts(
    session: AsyncSession,
    district_ids: Sequence[int],
) -> list[DistrictContact]:
    """Bulk-fetch active, EMAIL-BEARING contacts for multiple districts.

    Used by SEND2-B recipient resolution (artemis.marketing.sends) to build
    the recipients snapshot for an actual outbound send. Returns all active
    contacts with a non-null email for each district in district_ids, sorted
    by id.

    The email filter is CONTACTS-1 fallout, not cosmetic: before that work,
    ``email`` was NOT NULL at the column level, so "active" and "has an
    email" were the same set by construction. Now that 'argus' rows can be
    active with no email (a person Argus only knows the name and title of),
    a caller that means "someone real we can send this to" must say so
    explicitly, or it will build a recipients snapshot containing
    ``{"email": null, ...}`` and (per resolve_recipients_for_candidate's own
    contract) treat that as ">= 1 resolved recipient" — queuing a send that
    can never actually go anywhere. See contact_db_stub.has_contact and the
    signal_queue "routable" classification for the same fix applied to the
    other two read sites that made the identical assumption.
    """
    if not district_ids:
        return []
    stmt = (
        select(DistrictContact)
        .where(
            DistrictContact.district_id.in_(district_ids),
            DistrictContact.active.is_(True),
            DistrictContact.email.isnot(None),
        )
        .order_by(DistrictContact.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def deactivate_contact(
    session: AsyncSession,
    contact_id: int,
) -> DistrictContact:
    """Deactivate a contact (lossless — no hard delete).

    Sets active=False and updates updated_at. Raises ValueError if not found.
    """
    contact = await session.get(DistrictContact, contact_id)
    if contact is None:
        raise ValueError(f"contact_id={contact_id} does not exist")
    contact.active = False
    contact.updated_at = datetime.now(tz=UTC)
    await session.flush()
    logger.debug("deactivate_contact: contact_id=%s", contact_id)
    return contact


# ── CONTACTS-1: Argus-sourced contacts ──────────────────────────────────────


async def create_argus_contact(
    session: AsyncSession,
    *,
    district_id: int,
    name: str,
    title: str | None,
    source_observation_id: int,
) -> tuple[DistrictContact, bool]:
    """Create, or refresh, an Argus-derived contact.

    Idempotent on (district_id, source='argus', lower(name)) — a real DB
    partial unique index backs this too (uq_district_contacts_argus_person,
    migration 0118), so a concurrent double-run cannot slip a duplicate past
    this check. This is deliberately a NAME match, not an
    (district_id, source_observation_id) match: the same real person is
    sometimes named again in a LATER observation (a follow-up research pass),
    and re-deriving a second row for the same person from newer evidence
    would look like a duplicate contact to anyone reading the table, not
    like an update. Re-asserting the same person from a new observation
    refreshes title/source_observation_id on the existing row instead.

    Never sets email or phone — Argus extraction
    (artemis.argus.contacts.extract_person) has no pattern for either, by
    design: the CONTACTS-1 brief is explicit that guessing a contact detail
    from a naming convention is worse than leaving the field empty. If a
    future finding's text ever contains a real email/phone, extending the
    extractor is the correct place to add that — not this function.

    Raises ValueError for an empty name or a non-existent district_id.
    Returns (contact, created) — created=False means an existing row for
    this exact person was found and refreshed rather than duplicated.
    """
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")

    district = await session.get(District, district_id)
    if district is None:
        raise ValueError(f"district_id={district_id} does not exist")

    stmt = select(DistrictContact).where(
        DistrictContact.district_id == district_id,
        DistrictContact.source == ARGUS_CONTACT_SOURCE,
        func.lower(DistrictContact.name) == name.lower(),
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        existing.title = title or existing.title
        existing.source_observation_id = source_observation_id
        existing.active = True
        existing.updated_at = datetime.now(tz=UTC)
        await session.flush()
        logger.debug(
            "create_argus_contact: refreshed existing contact_id=%s district_id=%s",
            existing.id,
            district_id,
        )
        return existing, False

    contact = DistrictContact(
        district_id=district_id,
        name=name,
        title=title,
        email=None,
        phone=None,
        source=ARGUS_CONTACT_SOURCE,
        source_observation_id=source_observation_id,
        active=True,
    )
    session.add(contact)
    await session.flush()
    logger.debug(
        "create_argus_contact: created contact_id=%s district_id=%s", contact.id, district_id
    )
    return contact, True


# ── SFDC-1: Salesforce-sourced contact enrichment ────────────────────────────


async def upsert_salesforce_contact(
    session: AsyncSession,
    *,
    district_id: int,
    email: str,
    name: str,
    title: str | None = None,
    phone: str | None = None,
    external_id: str | None = None,
) -> tuple[DistrictContact, bool]:
    """Create, or refresh, a Salesforce-sourced contact record.

    Matched on (district_id, lower(email)) regardless of the existing row's
    ``source`` -- a contact already hand-entered as 'manual' with the same
    email gets enriched in place rather than duplicated; its ``source`` is
    left exactly as-is (this function only ever creates NEW rows as
    'salesforce', it never reclassifies an existing one). This mirrors
    create_argus_contact's "refresh in place, don't duplicate" contract, and
    the same reasoning as create_contact's manual-source upsert path (which
    this function does not call, because that path's insert branch is
    manual-only and would otherwise insert a duplicate row on every repeated
    Salesforce sync of the same person).

    Non-destructive by construction -- this is the required behaviour, not
    an incidental one (see the SFDC-1 brief's "Contact enrichment never
    overwrites a real email with a null" test):
      - email is the match key and is always the value already on file;
        this function never sets it to anything else, let alone null.
      - title/phone: a blank/None incoming value NEVER clears an existing
        populated field. Salesforce is treated as MORE authoritative than a
        hand-entered value only when it actually has something to say --
        an empty Salesforce field is silence, not a correction.
      - external_id (the Salesforce Contact Id) is only set when provided.

    Raises ValueError for an empty name/email or a non-existent district_id.
    Returns (contact, created) -- created=False means an existing row was
    found and refreshed rather than duplicated.
    """
    email_norm = email.strip().lower()
    if not email_norm:
        raise ValueError("email must not be empty")
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")

    district = await session.get(District, district_id)
    if district is None:
        raise ValueError(f"district_id={district_id} does not exist")

    stmt = select(DistrictContact).where(
        DistrictContact.district_id == district_id,
        func.lower(DistrictContact.email) == email_norm,
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        existing.name = name or existing.name
        if title:
            existing.title = title
        if phone:
            existing.phone = phone
        if external_id:
            existing.external_id = external_id
        existing.active = True
        existing.updated_at = datetime.now(tz=UTC)
        await session.flush()
        logger.debug(
            "upsert_salesforce_contact: refreshed existing contact_id=%s district_id=%s",
            existing.id,
            district_id,
        )
        return existing, False

    contact = DistrictContact(
        district_id=district_id,
        name=name,
        title=title,
        email=email_norm,
        phone=phone,
        source="salesforce",
        external_id=external_id,
        active=True,
    )
    session.add(contact)
    await session.flush()
    logger.debug(
        "upsert_salesforce_contact: created contact_id=%s district_id=%s", contact.id, district_id
    )
    return contact, True


async def get_contact(session: AsyncSession, contact_id: int) -> DistrictContact | None:
    """Fetch a single contact by id, or None if it does not exist (e.g. already wiped)."""
    return await session.get(DistrictContact, contact_id)


async def delete_contact(session: AsyncSession, contact_id: int) -> None:
    """Permanently delete one contact row. Real deletion — NOT active=False.

    This is intentionally a different operation from deactivate_contact():
    a removal request for a real person's PII (CONTACTS-1) means the row
    stops existing, not that it becomes invisible while still sitting in the
    table. Safe regardless of source ('manual', 'salesforce', or 'argus') —
    nothing else in the schema holds a foreign key INTO district_contacts.id
    (campaign_sends.recipients is a denormalized JSONB snapshot taken at
    queue time, not a live reference, so a later delete here cannot corrupt
    a past send record).

    What happens to the observation that named this person (for 'argus'
    rows): NOTHING. It is untouched, keeps its original prose exactly as
    written, is still findable by district key, and still contributes to
    Argus's research the same as before. The reference only ever existed in
    this direction (district_contacts.source_observation_id pointing AT the
    observation) — the observation itself never held, and still does not
    hold, any pointer back to this contact. Deleting this row breaks that
    one pointer and nothing else.

    One honest consequence, not a bug: if the source observation is still
    there and someone re-runs Argus's extraction pass over it, it can
    recreate an equivalent contact from the same evidence. Hard deletion
    here removes today's derived record; it is not (and cannot be, without
    editing or deleting the observation, which CLAUDE.md rule 3 forbids) a
    promise that the underlying fact becomes un-learnable.

    Raises ValueError if the contact does not exist.
    """
    contact = await session.get(DistrictContact, contact_id)
    if contact is None:
        raise ValueError(f"contact_id={contact_id} does not exist")
    await session.delete(contact)
    await session.flush()
    logger.info("delete_contact: permanently deleted contact_id=%s", contact_id)


async def delete_contacts_for_district(session: AsyncSession, district_id: int) -> int:
    """Permanently delete every contact row for a district. Returns the count deleted.

    Same real-deletion contract as delete_contact() — see its docstring for
    what does (and does not) happen to the observations that named these
    people. Deleting zero rows (a district with no contacts) is not an
    error; it returns 0.
    """
    stmt = select(DistrictContact).where(DistrictContact.district_id == district_id)
    rows = (await session.execute(stmt)).scalars().all()
    count = 0
    for row in rows:
        await session.delete(row)
        count += 1
    await session.flush()
    logger.info(
        "delete_contacts_for_district: permanently deleted %d contact(s) for district_id=%s",
        count,
        district_id,
    )
    return count
