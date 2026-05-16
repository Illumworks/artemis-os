"""Tests for /api/skills endpoints and Skill repository helpers."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo

# ─────────────────────────────────────────────────────────────────────────────
# Repository tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_skill(db_session: AsyncSession) -> None:
    async with db_session.begin():
        skill = await repo.create_skill(
            db_session,
            slug="my-skill",
            name="My Skill",
            description="Does things",
            instructions="Step by step...",
            tools=["bash"],
            kind="user",
        )
    assert skill.id is not None
    assert skill.slug == "my-skill"

    fetched = await repo.get_skill(db_session, "my-skill")
    assert fetched.name == "My Skill"
    assert fetched.kind == "user"


@pytest.mark.asyncio
async def test_list_skills(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_skill(db_session, slug="sk1", name="Skill One", kind="user")
        await repo.create_skill(db_session, slug="sk2", name="Skill Two", kind="builtin")
    all_skills = await repo.list_skills(db_session)
    assert len(all_skills) == 2
    user_skills = await repo.list_skills(db_session, kind="user")
    assert len(user_skills) == 1


@pytest.mark.asyncio
async def test_update_skill(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_skill(db_session, slug="upd-skill", name="Old")
    async with db_session.begin():
        updated = await repo.update_skill(db_session, "upd-skill", name="New", tools=["read"])
    assert updated.name == "New"
    assert updated.tools == ["read"]


@pytest.mark.asyncio
async def test_delete_skill(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_skill(db_session, slug="del-skill", name="Delete")
    async with db_session.begin():
        await repo.delete_skill(db_session, "del-skill")
    with pytest.raises(ValueError, match="not found"):
        await repo.get_skill(db_session, "del-skill")


@pytest.mark.asyncio
async def test_get_skill_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not found"):
        await repo.get_skill(db_session, "ghost")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_skills_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/skills/")
    assert resp.status_code == 200
    assert resp.json()["skills"] == []


@pytest.mark.asyncio
async def test_create_skill_http(client: AsyncClient) -> None:
    payload = {
        "slug": "http-skill",
        "name": "HTTP Skill",
        "description": "Via HTTP",
        "tools": ["bash", "read"],
        "kind": "user",
    }
    resp = await client.post("/api/skills/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "http-skill"
    assert data["kind"] == "user"


@pytest.mark.asyncio
async def test_get_skill_http(client: AsyncClient) -> None:
    await client.post("/api/skills/", json={"slug": "get-skill", "name": "Get"})
    resp = await client.get("/api/skills/get-skill")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "get-skill"


@pytest.mark.asyncio
async def test_get_skill_not_found_http(client: AsyncClient) -> None:
    resp = await client.get("/api/skills/no-such")
    assert resp.status_code == 404
    assert resp.json()["code"] == "skill_not_found"


@pytest.mark.asyncio
async def test_create_skill_duplicate_http(client: AsyncClient) -> None:
    payload = {"slug": "dup-skill", "name": "Dup"}
    await client.post("/api/skills/", json=payload)
    resp = await client.post("/api/skills/", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == "skill_exists"


@pytest.mark.asyncio
async def test_patch_skill_http(client: AsyncClient) -> None:
    await client.post("/api/skills/", json={"slug": "patch-skill", "name": "Old"})
    resp = await client.patch("/api/skills/patch-skill", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_patch_skill_not_found_http(client: AsyncClient) -> None:
    resp = await client.patch("/api/skills/no-such", json={"name": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_skill_http(client: AsyncClient) -> None:
    await client.post("/api/skills/", json={"slug": "rm-skill", "name": "Remove"})
    resp = await client.delete("/api/skills/rm-skill")
    assert resp.status_code == 204
    resp2 = await client.get("/api/skills/rm-skill")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_skill_not_found_http(client: AsyncClient) -> None:
    resp = await client.delete("/api/skills/ghost")
    assert resp.status_code == 404
