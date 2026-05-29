"""CC18 — POST /api/builder/sessions with target_id persists it.

The UI flow (agent profile → "Edit with Builder") now passes the selected
agent's int PK as `target_id` in the create-session body. The backend has
always accepted it, but no UI test or integration test asserted the
round-trip. This test locks in the wire contract so a future schema/type
drift can't silently break read_recent_runs() seeding.

The generic "New session" flow (no target_id) must still produce a row
with target_id IS NULL — protects the creation-style use case.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from artemis.db import SessionLocal, engine

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def clean_builder_sessions() -> AsyncIterator[None]:
    await engine.dispose()
    async with SessionLocal() as session:
        await session.execute(
            text("TRUNCATE builder_sessions, definition_proposals RESTART IDENTITY CASCADE")
        )
        await session.commit()
    yield
    await engine.dispose()


async def test_create_session_with_target_id_persists_it(client: AsyncClient) -> None:
    response = await client.post(
        "/api/builder/sessions",
        json={"builder_kind": "agent", "target_id": 42},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target_id"] == 42
    assert body["builder_kind"] == "agent"

    # Round-trip through the DB to prove persistence, not just echo.
    async with SessionLocal() as db:
        row = (
            await db.execute(
                text("SELECT target_id FROM builder_sessions WHERE id = :id"),
                {"id": body["id"]},
            )
        ).first()
    assert row is not None
    assert row[0] == 42


async def test_create_session_without_target_id_keeps_null(client: AsyncClient) -> None:
    response = await client.post(
        "/api/builder/sessions",
        json={"builder_kind": "agent"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target_id"] is None

    async with SessionLocal() as db:
        row = (
            await db.execute(
                text("SELECT target_id FROM builder_sessions WHERE id = :id"),
                {"id": body["id"]},
            )
        ).first()
    assert row is not None
    assert row[0] is None
