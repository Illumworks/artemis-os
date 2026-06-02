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
            category="writing",
            instructions="Step by step...",
            tools=["bash"],
            kind="user",
            status="proposed",
        )
    assert skill.id is not None
    assert skill.slug == "my-skill"
    assert skill.status == "proposed"
    assert skill.category == "writing"

    fetched = await repo.get_skill(db_session, "my-skill")
    assert fetched.name == "My Skill"
    assert fetched.kind == "user"
    assert fetched.status == "proposed"


@pytest.mark.asyncio
async def test_list_skills(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_skill(
            db_session,
            slug="sk1",
            name="Skill One",
            kind="user",
            status="approved",
            category="ops",
        )
        await repo.create_skill(
            db_session,
            slug="sk2",
            name="Skill Two",
            kind="builtin",
            status="proposed",
            category="ops",
        )
    all_skills = await repo.list_skills(db_session)
    assert len(all_skills) == 2
    user_skills = await repo.list_skills(db_session, kind="user")
    assert len(user_skills) == 1
    proposed_skills = await repo.list_skills(db_session, status="proposed")
    assert [s.slug for s in proposed_skills] == ["sk2"]
    ops_skills = await repo.list_skills(db_session, category="ops")
    assert len(ops_skills) == 2


@pytest.mark.asyncio
async def test_set_skill_status_and_list_categories(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_skill(db_session, slug="approved", name="Approved", category="ops")
        await repo.create_skill(
            db_session,
            slug="proposed",
            name="Proposed",
            category="ops",
            status="proposed",
        )
        await repo.create_skill(
            db_session,
            slug="archived",
            name="Archived",
            category="research",
            status="archived",
        )
    async with db_session.begin():
        updated = await repo.set_skill_status(db_session, "proposed", "approved")
    assert updated.status == "approved"
    categories = await repo.list_skill_categories(db_session)
    assert categories == [{"category": "ops", "count": 2}]


@pytest.mark.asyncio
async def test_update_skill(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_skill(db_session, slug="upd-skill", name="Old")
    async with db_session.begin():
        updated = await repo.update_skill(
            db_session,
            "upd-skill",
            name="New",
            category="ops",
            status="proposed",
            tools=["read"],
        )
    assert updated.name == "New"
    assert updated.category == "ops"
    assert updated.status == "proposed"
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
        "category": "writing",
        "tools": ["bash", "read"],
        "kind": "user",
        "status": "proposed",
    }
    resp = await client.post("/api/skills/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "http-skill"
    assert data["kind"] == "user"
    assert data["status"] == "proposed"
    assert data["category"] == "writing"


@pytest.mark.asyncio
async def test_create_skill_http_no_slash_defaults_to_approved(client: AsyncClient) -> None:
    resp = await client.post("/api/skills", json={"slug": "no-slash", "name": "No Slash"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_get_skill_http(client: AsyncClient) -> None:
    await client.post("/api/skills/", json={"slug": "get-skill", "name": "Get"})
    resp = await client.get("/api/skills/get-skill")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "get-skill"


@pytest.mark.asyncio
async def test_get_skill_by_slug_node_compat_http(client: AsyncClient) -> None:
    await client.post("/api/skills/", json={"slug": "slug-skill", "name": "Slug"})
    resp = await client.get("/api/skills/slug/slug-skill")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "slug-skill"


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
    resp = await client.patch(
        "/api/skills/patch-skill",
        json={"name": "New", "category": "ops", "status": "proposed"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"
    assert resp.json()["category"] == "ops"
    assert resp.json()["status"] == "proposed"


@pytest.mark.asyncio
async def test_filter_skills_by_status_category_and_kind_compat(client: AsyncClient) -> None:
    await client.post(
        "/api/skills/",
        json={
            "slug": "approved-ops",
            "name": "Approved Ops",
            "status": "approved",
            "category": "filter-ops",
        },
    )
    await client.post(
        "/api/skills/",
        json={
            "slug": "proposed-ops",
            "name": "Proposed Ops",
            "status": "proposed",
            "category": "filter-ops",
        },
    )
    await client.post(
        "/api/skills/",
        json={
            "slug": "approved-writing",
            "name": "Approved Writing",
            "status": "approved",
            "category": "filter-writing",
        },
    )

    status_resp = await client.get("/api/skills?status=proposed&category=filter-ops")
    assert [s["slug"] for s in status_resp.json()["skills"]] == ["proposed-ops"]

    category_resp = await client.get("/api/skills?category=filter-writing")
    assert [s["slug"] for s in category_resp.json()["skills"]] == ["approved-writing"]

    kind_status_resp = await client.get("/api/skills?kind=approved&category=filter-ops")
    assert {s["slug"] for s in kind_status_resp.json()["skills"]} == {
        "approved-ops",
    }


@pytest.mark.asyncio
async def test_categories_endpoint_excludes_archived(client: AsyncClient) -> None:
    await client.post(
        "/api/skills/",
        json={"slug": "cats-one", "name": "Cats One", "category": "cats-active"},
    )
    await client.post(
        "/api/skills/",
        json={
            "slug": "cats-two",
            "name": "Cats Two",
            "category": "cats-active",
            "status": "proposed",
        },
    )
    await client.post(
        "/api/skills/",
        json={
            "slug": "cats-old",
            "name": "Cats Old",
            "category": "cats-archived",
            "status": "archived",
        },
    )
    resp = await client.get("/api/skills/categories")
    assert resp.status_code == 200
    by_category = {row["category"]: row["count"] for row in resp.json()}
    assert by_category["cats-active"] == 2
    assert "cats-archived" not in by_category


@pytest.mark.asyncio
async def test_approve_and_archive_skill_http(client: AsyncClient) -> None:
    await client.post(
        "/api/skills/",
        json={"slug": "life-skill", "name": "Life", "status": "proposed"},
    )

    approve_resp = await client.post("/api/skills/life-skill/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["ok"] is True
    approved = await client.get("/api/skills/life-skill")
    assert approved.json()["status"] == "approved"

    archive_resp = await client.post("/api/skills/life-skill/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["ok"] is True
    archived = await client.get("/api/skills/life-skill")
    assert archived.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_approve_skill_not_found_http(client: AsyncClient) -> None:
    resp = await client.post("/api/skills/ghost/approve")
    assert resp.status_code == 404
    assert resp.json()["code"] == "skill_not_found"


@pytest.mark.asyncio
async def test_assign_and_unassign_skill_http(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        agent = await repo.create_agent(db_session, agent_id="agent-one", name="Agent One")
        await repo.create_skill(db_session, slug="assignable", name="Assignable")

    assign_resp = await client.post("/api/skills/assignable/assign", json={"agent_id": agent.id})
    assert assign_resp.status_code == 200
    assert assign_resp.json()["ok"] is True

    skills_resp = await client.get("/api/agents/agent-one/skills")
    assert [s["slug"] for s in skills_resp.json()["skills"]] == ["assignable"]

    unassign_resp = await client.post(
        "/api/skills/assignable/unassign",
        json={"agent_id": agent.id},
    )
    assert unassign_resp.status_code == 200
    assert unassign_resp.json()["ok"] is True
    skills_resp = await client.get("/api/agents/agent-one/skills")
    assert skills_resp.json()["skills"] == []


@pytest.mark.asyncio
async def test_assign_skill_requires_agent_id(client: AsyncClient) -> None:
    await client.post("/api/skills/", json={"slug": "needs-agent", "name": "Needs Agent"})
    resp = await client.post("/api/skills/needs-agent/assign", json={"agentId": 1})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_agent_id"


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
