"""CRUD round-trip tests for Automations (OP1).

Tests:
- Create + get round-trip
- List embeds latest_run (null when no run, populated after run)
- Archive is soft delete; archived excluded from default list
- Archived included when status=archived filter is used
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.automations import repository as repo


@pytest.mark.asyncio
async def test_create_and_get_automation(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Test Automation",
            description="A test",
            trigger_type="manual",
            target_type="agent",
            target_id="test-agent",
        )
    assert auto.id is not None
    fetched = await repo.get_automation(db_session, auto.id)
    assert fetched.name == "Test Automation"
    assert fetched.status == "active"


@pytest.mark.asyncio
async def test_list_embeds_latest_run_null_when_no_run(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_automation(
            db_session,
            name="No Runs",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-1",
        )
    rows = await repo.list_automations(db_session)
    assert len(rows) == 1
    auto, run = rows[0]
    assert auto.name == "No Runs"
    assert run is None


@pytest.mark.asyncio
async def test_list_embeds_latest_run_populated(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Has Runs",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-x",
        )
        # Create two runs; latest should be the second
        await repo.create_automation_run(
            db_session, automation_id=auto.id, trigger="manual", triggered_by="user"
        )
        r2 = await repo.create_automation_run(
            db_session, automation_id=auto.id, trigger="manual", triggered_by="user"
        )
    rows = await repo.list_automations(db_session)
    assert len(rows) == 1
    _, latest = rows[0]
    assert latest is not None
    assert latest.id == r2.id


@pytest.mark.asyncio
async def test_archive_soft_delete(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="To Archive",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-1",
        )
    async with db_session.begin():
        archived = await repo.archive_automation(db_session, auto.id)
    assert archived.status == "archived"
    assert archived.archived_at is not None
    # Row still exists
    fetched = await repo.get_automation(db_session, auto.id)
    assert fetched.status == "archived"


@pytest.mark.asyncio
async def test_archived_excluded_from_default_list(db_session: AsyncSession) -> None:
    async with db_session.begin():
        a1 = await repo.create_automation(
            db_session,
            name="Active",
            trigger_type="manual",
            target_type="agent",
            target_id="a1",
        )
        a2 = await repo.create_automation(
            db_session,
            name="Archived",
            trigger_type="manual",
            target_type="agent",
            target_id="a2",
        )
        await repo.archive_automation(db_session, a2.id)

    rows = await repo.list_automations(db_session)
    ids = [a.id for a, _ in rows]
    assert a1.id in ids
    assert a2.id not in ids


@pytest.mark.asyncio
async def test_archived_included_with_status_filter(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Archived Auto",
            trigger_type="manual",
            target_type="agent",
            target_id="a1",
        )
        await repo.archive_automation(db_session, auto.id)

    rows = await repo.list_automations(db_session, status="archived")
    ids = [a.id for a, _ in rows]
    assert auto.id in ids


@pytest.mark.asyncio
async def test_update_automation(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Old Name",
            trigger_type="manual",
            target_type="agent",
            target_id="a1",
        )
    async with db_session.begin():
        updated = await repo.update_automation(db_session, auto.id, name="New Name")
    assert updated.name == "New Name"
