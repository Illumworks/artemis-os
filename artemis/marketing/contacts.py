"""Repository functions for district_contacts.

Lossless invariant: contacts are never hard-deleted.
Deactivate via deactivate_contact(); re-add re-activates the existing row.

All functions call ``await session.flush()``; the caller owns commit.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import District, DistrictContact

logger = logging.getLogger(__name__)


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
    """Bulk-fetch active contacts for multiple districts.

    Used by SEND2-B recipient resolution. Returns all active contacts for
    each district in district_ids, sorted by id.
    """
    if not district_ids:
        return []
    stmt = (
        select(DistrictContact)
        .where(
            DistrictContact.district_id.in_(district_ids),
            DistrictContact.active.is_(True),
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
