"""CRUD round-trip tests for Pipelines (PIPE1).

Tests:
- Create + get round-trip
- List embeds latest_run (null when no run, populated after run)
- Archive is soft delete; archived excluded from default list
- Archived included when status=archived filter is used
- Enable/disable toggle
- Latest-run shows most recent run
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo

_SIMPLE_NODE = {
    "id": "node-1",
    "type": "agent_invocation",
    "label": "Email Agent",
    "config": {"agent_id": "email-agent"},
    "position": {"x": 0.0, "y": 0.0},
}


@pytest.mark.asyncio
async def test_create_and_get_pipeline(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(
            db_session,
            name="My Pipeline",
            description="A test pipeline",
            nodes=[_SIMPLE_NODE],
            edges=[],
        )
    assert p.id is not None
    fetched = await repo.get_pipeline(db_session, p.id)
    assert fetched.name == "My Pipeline"
    assert fetched.status == "active"
    assert len(fetched.nodes) == 1


@pytest.mark.asyncio
async def test_list_embeds_latest_run_null_when_no_run(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_pipeline(db_session, name="No Runs", nodes=[], edges=[])
    rows = await repo.list_pipelines(db_session)
    assert len(rows) == 1
    p, run = rows[0]
    assert p.name == "No Runs"
    assert run is None


@pytest.mark.asyncio
async def test_list_embeds_latest_run_populated(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="Has Runs", nodes=[], edges=[])
    async with db_session.begin():
        await repo.create_pipeline_run(
            db_session, pipeline_id=p.id, trigger="manual", triggered_by="user"
        )
    async with db_session.begin():
        r2 = await repo.create_pipeline_run(
            db_session, pipeline_id=p.id, trigger="manual", triggered_by="user"
        )
    rows = await repo.list_pipelines(db_session)
    assert len(rows) == 1
    _, latest = rows[0]
    assert latest is not None
    assert latest.id == r2.id


@pytest.mark.asyncio
async def test_archive_soft_delete(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="To Archive", nodes=[], edges=[])
    async with db_session.begin():
        archived = await repo.archive_pipeline(db_session, p.id)
    assert archived.status == "archived"
    # Row still exists
    fetched = await repo.get_pipeline(db_session, p.id)
    assert fetched.status == "archived"


@pytest.mark.asyncio
async def test_archived_excluded_from_default_list(db_session: AsyncSession) -> None:
    async with db_session.begin():
        a1 = await repo.create_pipeline(db_session, name="Active", nodes=[], edges=[])
        a2 = await repo.create_pipeline(db_session, name="Archived", nodes=[], edges=[])
        await repo.archive_pipeline(db_session, a2.id)

    rows = await repo.list_pipelines(db_session)
    ids = [p.id for p, _ in rows]
    assert a1.id in ids
    assert a2.id not in ids


@pytest.mark.asyncio
async def test_archived_included_with_status_filter(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="Archived Pipeline", nodes=[], edges=[])
        await repo.archive_pipeline(db_session, p.id)

    rows = await repo.list_pipelines(db_session, status="archived")
    ids = [pp.id for pp, _ in rows]
    assert p.id in ids


@pytest.mark.asyncio
async def test_update_pipeline_name_and_nodes(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="Old Name", nodes=[], edges=[])
    async with db_session.begin():
        updated = await repo.update_pipeline(
            db_session, p.id, name="New Name", nodes=[_SIMPLE_NODE]
        )
    assert updated.name == "New Name"
    assert len(updated.nodes) == 1


@pytest.mark.asyncio
async def test_enable_disable_toggle(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="Toggle Test", nodes=[], edges=[])
    async with db_session.begin():
        paused = await repo.update_pipeline(db_session, p.id, status="paused")
    assert paused.status == "paused"
    async with db_session.begin():
        active = await repo.update_pipeline(db_session, p.id, status="active")
    assert active.status == "active"


@pytest.mark.asyncio
async def test_get_pipeline_with_latest_run(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="With Run", nodes=[], edges=[])
    async with db_session.begin():
        run = await repo.create_pipeline_run(
            db_session, pipeline_id=p.id, trigger="manual", triggered_by="tester"
        )
    p2, latest = await repo.get_pipeline_with_latest_run(db_session, p.id)
    assert p2.id == p.id
    assert latest is not None
    assert latest.id == run.id
