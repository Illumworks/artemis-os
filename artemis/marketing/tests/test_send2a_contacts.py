"""SEND2-A district contacts substrate tests.

Covers:
- create + list_contacts_for_district
- reactivation of inactive row (upsert on email match)
- list_active_contacts_for_districts (bulk fetch)
- deactivate_contact (active_only filtering)
- contact_db_stub.has_contact with real DB (numeric districtId)
- FK cascade: deleting a district removes contacts
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.contacts import (
    create_contact,
    deactivate_contact,
    list_active_contacts_for_districts,
    list_contacts_for_district,
)
from artemis.marketing.models import District, DistrictContact
from artemis.tools.contact_db import _factory as _contact_factory
from artemis.tools.context import ToolContext

pytestmark = pytest.mark.asyncio


# ── helpers ───────────────────────────────────────────────────────────────────


async def _make_district(session: AsyncSession, name: str = "Test District") -> District:
    district = District(
        name=name,
        state="CA",
        classification_source="manual",
    )
    session.add(district)
    await session.flush()
    return district


def _ctx(session: AsyncSession) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id="marketing.scout.regional_news",
        agent_db_id=1,
        agent_run_id="run-send2a-test",
        pipeline_run_id=None,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_create_and_list(db_session: AsyncSession) -> None:
    """Create a district + contact; list_contacts_for_district returns it."""
    async with db_session.begin():
        district = await _make_district(db_session)
        contact = await create_contact(
            db_session,
            district_id=district.id,
            name="Alex Johnson",
            email="alex.johnson@example.example",
            title="Curriculum Director",
        )

    async with db_session.begin():
        contacts = await list_contacts_for_district(db_session, district.id)
    assert len(contacts) == 1
    assert contacts[0].id == contact.id
    assert contacts[0].email == "alex.johnson@example.example"
    assert contacts[0].title == "Curriculum Director"
    assert contacts[0].active is True


async def test_reactivate_inactive_row(db_session: AsyncSession) -> None:
    """Re-creating with same email when inactive reactivates instead of duplicating."""
    async with db_session.begin():
        district = await _make_district(db_session)
        contact = await create_contact(
            db_session,
            district_id=district.id,
            name="Morgan Smith",
            email="morgan.smith@test.example",
        )
        original_id = contact.id
        await deactivate_contact(db_session, contact.id)

    # Verify it's deactivated
    async with db_session.begin():
        contacts = await list_contacts_for_district(db_session, district.id, active_only=True)
    assert len(contacts) == 0

    # Re-create with same email → should reactivate the original row
    async with db_session.begin():
        reactivated = await create_contact(
            db_session,
            district_id=district.id,
            name="Morgan Smith Updated",
            email="morgan.smith@test.example",
        )

    assert reactivated.id == original_id
    assert reactivated.active is True
    assert reactivated.name == "Morgan Smith Updated"

    # Confirm only one row exists
    async with db_session.begin():
        contacts = await list_contacts_for_district(db_session, district.id)
    assert len(contacts) == 1


async def test_list_active_contacts_for_districts_bulk(db_session: AsyncSession) -> None:
    """Two districts, each with one contact; bulk fetch returns both."""
    async with db_session.begin():
        d1 = await _make_district(db_session, "District One")
        d2 = await _make_district(db_session, "District Two")
        c1 = await create_contact(
            db_session, district_id=d1.id, name="Alice A", email="alice@d1.example"
        )
        c2 = await create_contact(
            db_session, district_id=d2.id, name="Bob B", email="bob@d2.example"
        )

    async with db_session.begin():
        contacts = await list_active_contacts_for_districts(db_session, [d1.id, d2.id])
    ids = {c.id for c in contacts}
    assert c1.id in ids
    assert c2.id in ids
    assert len(contacts) == 2


async def test_deactivate_contact_filtering(db_session: AsyncSession) -> None:
    """deactivate_contact flips active=False; active_only=True excludes it, False includes it."""
    async with db_session.begin():
        district = await _make_district(db_session)
        contact = await create_contact(
            db_session, district_id=district.id, name="Chris C", email="chris@example.example"
        )
        await deactivate_contact(db_session, contact.id)

    async with db_session.begin():
        active_only = await list_contacts_for_district(db_session, district.id, active_only=True)
        all_contacts = await list_contacts_for_district(db_session, district.id, active_only=False)

    assert len(active_only) == 0
    assert len(all_contacts) == 1
    assert all_contacts[0].active is False


async def test_has_contact_true_numeric_district_id(db_session: AsyncSession) -> None:
    """has_contact returns 'true' for numeric districtId with active contact."""
    async with db_session.begin():
        district = await _make_district(db_session)
        await create_contact(
            db_session, district_id=district.id, name="Dana D", email="dana@example.example"
        )

    ctx = _ctx(db_session)
    _, impl = _contact_factory(ctx)
    result = await impl({"districtId": str(district.id)})
    assert result == "true"


async def test_has_contact_false_no_contacts(db_session: AsyncSession) -> None:
    """has_contact returns 'false' when no active contact exists for the district."""
    async with db_session.begin():
        district = await _make_district(db_session)

    ctx = _ctx(db_session)
    _, impl = _contact_factory(ctx)
    result = await impl({"districtId": str(district.id)})
    assert result == "false"


async def test_has_contact_false_all_deactivated(db_session: AsyncSession) -> None:
    """has_contact returns 'false' when all contacts are deactivated."""
    async with db_session.begin():
        district = await _make_district(db_session)
        contact = await create_contact(
            db_session, district_id=district.id, name="Eve E", email="eve@example.example"
        )
        await deactivate_contact(db_session, contact.id)

    ctx = _ctx(db_session)
    _, impl = _contact_factory(ctx)
    result = await impl({"districtId": str(district.id)})
    assert result == "false"


async def test_district_cascade_deletes_contacts(db_session: AsyncSession) -> None:
    """Deleting a district via raw SQL cascades to district_contacts."""
    async with db_session.begin():
        district = await _make_district(db_session)
        contact = await create_contact(
            db_session, district_id=district.id, name="Frank F", email="frank@example.example"
        )
        contact_id = contact.id
        district_id = district.id

    # Use raw SQL DELETE to simulate schema-level cascade
    # (the model API forbids hard-delete on contacts, but District cascade IS legal)
    async with db_session.begin():
        await db_session.execute(
            text("DELETE FROM districts WHERE id = :did"),
            {"did": district_id},
        )

    # Expire the session identity map so the next get hits the DB, not the cache
    db_session.expire_all()

    # Contact should be gone due to ON DELETE CASCADE
    async with db_session.begin():
        gone = await db_session.get(DistrictContact, contact_id)
    assert gone is None


async def test_create_contact_validates_unknown_district(db_session: AsyncSession) -> None:
    """create_contact raises ValueError for a non-existent district_id."""
    async with db_session.begin():
        with pytest.raises(ValueError, match="district_id=99999 does not exist"):
            await create_contact(
                db_session, district_id=99999, name="Ghost", email="ghost@example.example"
            )


async def test_create_contact_normalizes_email(db_session: AsyncSession) -> None:
    """Email is normalized to lower-case stripped before storage."""
    async with db_session.begin():
        district = await _make_district(db_session)
        contact = await create_contact(
            db_session,
            district_id=district.id,
            name="Jane",
            email="  Jane.Doe@EXAMPLE.EXAMPLE  ",
        )
    assert contact.email == "jane.doe@example.example"
