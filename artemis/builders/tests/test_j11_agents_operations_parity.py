"""Tests for J11 — Agents Operations parity.

Covers:
  Slice A: subresource routes (instruction GET/PUT/DELETE, files GET, skills GET)
  Slice B: run-observability aliases (active, recent, search, run by ID, context)
  Slice C: enriched detail on GET /api/agents/{id}
  Slice D: POST /api/skills/{slug}/assign and /unassign

Each endpoint group: happy path + at least one failure mode.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _create_agent(session: AsyncSession, agent_id: str = "test-agent") -> Any:
    """Create an agent; commit if no transaction is active, otherwise just flush."""
    result = await repo.create_agent(session, agent_id=agent_id, name=f"Agent {agent_id}")
    await session.commit()
    return result


async def _create_skill(session: AsyncSession, slug: str = "my-skill") -> Any:
    """Create a skill; commit."""
    result = await repo.create_skill(session, slug=slug, name=f"Skill {slug}")
    await session.commit()
    return result


async def _create_run(
    session: AsyncSession, agent_id: str = "test-agent", status: str = "queued"
) -> str:
    run_id = str(uuid.uuid4())
    await repo.create_agent_run(session, run_id=run_id, agent_id=agent_id, status=status)
    await session.commit()
    return run_id


# ─────────────────────────────────────────────────────────────────────────────
# Slice C — enriched GET /api/agents/{agent_id}
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_agent_enriched_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/agents/{id} returns enrichment fields even when empty."""
    await _create_agent(db_session, "enrich-agent")
    resp = await client.get("/api/agents/enrich-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agentId"] == "enrich-agent"
    assert "instructionFileExists" in data
    assert "supportingFileCount" in data
    assert "linkedSkills" in data
    assert data["instructionFileExists"] is False
    assert data["supportingFileCount"] == 0
    assert data["linkedSkills"] == []


@pytest.mark.asyncio
async def test_get_agent_policy_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/agents/{id} exposes policy fields with defaults."""
    await _create_agent(db_session, "policy-agent")
    resp = await client.get("/api/agents/policy-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["memoryPolicy"] == "session_scoped"
    assert data["permissionMode"] == "ask"
    assert data["fallbackProvider"] is None
    assert data["fallbackModel"] is None
    assert data["outputContract"] is None


@pytest.mark.asyncio
async def test_create_agent_with_policy_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /api/agents accepts and persists policy fields."""
    payload = {
        "agentId": "policy-create-agent",
        "name": "Policy Agent",
        "provider": "claude-code",
        "memoryPolicy": "agent_scoped",
        "permissionMode": "auto_approve",
        "fallbackProvider": "openai",
        "fallbackModel": "gpt-4o",
        "outputContract": {"type": "object"},
    }
    resp = await client.post("/api/agents/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["memoryPolicy"] == "agent_scoped"
    assert data["permissionMode"] == "auto_approve"
    assert data["fallbackProvider"] == "openai"
    assert data["fallbackModel"] == "gpt-4o"
    assert data["outputContract"] == {"type": "object"}


@pytest.mark.asyncio
async def test_create_agent_invalid_memory_policy(client: AsyncClient) -> None:
    """POST /api/agents rejects unknown memoryPolicy values."""
    payload = {
        "agentId": "bad-policy-agent",
        "name": "Bad Policy",
        "memoryPolicy": "not_a_valid_policy",
    }
    resp = await client.post("/api/agents/", json=payload)
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Slice A — Instruction file routes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def agents_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Override ARTEMIS_AGENTS_DIR env var for test isolation."""
    agents_base = tmp_path / "agents"
    agents_base.mkdir()
    old = os.environ.get("ARTEMIS_AGENTS_DIR")
    os.environ["ARTEMIS_AGENTS_DIR"] = str(agents_base)
    # Patch the module-level constant too
    import artemis.routes.builders.agents as agents_mod

    old_base = agents_mod._AGENTS_BASE
    agents_mod._AGENTS_BASE = agents_base
    yield agents_base
    # Restore
    agents_mod._AGENTS_BASE = old_base
    if old is None:
        os.environ.pop("ARTEMIS_AGENTS_DIR", None)
    else:
        os.environ["ARTEMIS_AGENTS_DIR"] = old


@pytest.mark.asyncio
async def test_get_instruction_no_file(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """GET /api/agents/{id}/instruction returns exists=False when no file."""
    await _create_agent(db_session, "instr-agent")
    resp = await client.get("/api/agents/instr-agent/instruction")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is False
    assert data["content"] == ""


@pytest.mark.asyncio
async def test_put_and_get_instruction(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """PUT /api/agents/{id}/instruction writes, GET reads it back."""
    await _create_agent(db_session, "write-instr-agent")
    put_resp = await client.put(
        "/api/agents/write-instr-agent/instruction",
        json={"content": "# Hello\nThis is the instruction."},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["ok"] is True

    get_resp = await client.get("/api/agents/write-instr-agent/instruction")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["exists"] is True
    assert "Hello" in data["content"]


@pytest.mark.asyncio
async def test_delete_instruction(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """DELETE /api/agents/{id}/instruction removes the file."""
    await _create_agent(db_session, "del-instr-agent")
    await client.put("/api/agents/del-instr-agent/instruction", json={"content": "to be deleted"})
    del_resp = await client.delete("/api/agents/del-instr-agent/instruction")
    assert del_resp.status_code == 204

    get_resp = await client.get("/api/agents/del-instr-agent/instruction")
    assert get_resp.json()["exists"] is False


@pytest.mark.asyncio
async def test_instruction_agent_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """Instruction routes return 404 for non-existent agent.

    Note: use trailing-slash form to bypass TrailingSlashCompatMiddleware retry
    (GET /api/agents/x/instruction without slash → middleware intercepts 404 and
    retries with slash → static handler 404, losing our custom body).
    """
    resp = await client.get("/api/agents/ghost-agent/instruction/")
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Slice A — Files list route
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_files_empty(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """GET /api/agents/{id}/files returns empty list when dir doesn't exist."""
    await _create_agent(db_session, "files-agent")
    resp = await client.get("/api/agents/files-agent/files")
    assert resp.status_code == 200
    assert resp.json()["files"] == []


@pytest.mark.asyncio
async def test_list_files_with_files(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """GET /api/agents/{id}/files lists existing files."""
    await _create_agent(db_session, "has-files-agent")
    # Get the db id via HTTP to avoid session state entanglement
    agent_resp = await client.get("/api/agents/has-files-agent")
    agent_db_id = agent_resp.json()["id"]
    files_dir = agents_dir / str(agent_db_id) / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "doc.pdf").write_bytes(b"x" * 100)
    (files_dir / "notes.txt").write_bytes(b"hello")

    resp = await client.get("/api/agents/has-files-agent/files")
    assert resp.status_code == 200
    data = resp.json()
    names = [f["name"] for f in data["files"]]
    assert "doc.pdf" in names
    assert "notes.txt" in names
    # sizes present
    for f in data["files"]:
        assert "size" in f
        assert "modifiedAt" in f


@pytest.mark.asyncio
async def test_files_agent_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.get("/api/agents/no-such-agent/files")
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Slice A — Skills list route
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_agent_skills_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    await _create_agent(db_session, "skills-agent")
    resp = await client.get("/api/agents/skills-agent/skills")
    assert resp.status_code == 200
    assert resp.json()["skills"] == []


@pytest.mark.asyncio
async def test_list_agent_skills_after_assign(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_agent(db_session, "assigned-skills-agent")
    await _create_skill(db_session, "cool-skill")
    # Fetch the agent PK via the HTTP API to avoid transaction nesting issues
    agent_resp = await client.get("/api/agents/assigned-skills-agent")
    agent_db_id = agent_resp.json()["id"]

    assign_resp = await client.post("/api/skills/cool-skill/assign", json={"agent_id": agent_db_id})
    assert assign_resp.status_code == 200

    resp = await client.get("/api/agents/assigned-skills-agent/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["skills"]) == 1
    assert data["skills"][0]["slug"] == "cool-skill"


@pytest.mark.asyncio
async def test_agent_skills_agent_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/agents/ghost/skills")
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Slice B — Run-observability aliases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_runs_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.get("/api/agents/runs/active")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


@pytest.mark.asyncio
async def test_active_runs_returns_running_and_pending(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_agent(db_session, "active-agent")
    await _create_run(db_session, "active-agent", status="running")
    await _create_run(db_session, "active-agent", status="pending")
    await _create_run(db_session, "active-agent", status="completed")

    resp = await client.get("/api/agents/runs/active")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    statuses = {r["status"] for r in runs}
    assert "running" in statuses
    assert "pending" in statuses
    assert "completed" not in statuses


@pytest.mark.asyncio
async def test_recent_runs_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.get("/api/agents/runs/recent")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


@pytest.mark.asyncio
async def test_recent_runs_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    await _create_agent(db_session, "recent-agent")
    for _ in range(5):
        await _create_run(db_session, "recent-agent")

    resp = await client.get("/api/agents/runs/recent?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 3


@pytest.mark.asyncio
async def test_search_runs_match(client: AsyncClient, db_session: AsyncSession) -> None:
    await _create_agent(db_session, "search-agent")
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        await repo.create_agent_run(
            db_session,
            run_id=run_id,
            agent_id="search-agent",
            user_message="please analyze the quarterly report",
        )
    resp = await client.get("/api/agents/runs/search?q=quarterly")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) >= 1
    assert any("quarterly" in (r.get("userMessage") or "") for r in runs)


@pytest.mark.asyncio
async def test_search_runs_no_match(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.get("/api/agents/runs/search?q=xyznotfound999")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


@pytest.mark.asyncio
async def test_get_run_by_id_alias(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/agents/runs/{run_id} returns same payload as /api/agent-runs/{run_id}."""
    await _create_agent(db_session, "alias-agent")
    run_id = await _create_run(db_session, "alias-agent")

    resp_alias = await client.get(f"/api/agents/runs/{run_id}")
    resp_canonical = await client.get(f"/api/agent-runs/{run_id}")
    assert resp_alias.status_code == 200
    assert resp_canonical.status_code == 200
    assert resp_alias.json() == resp_canonical.json()


@pytest.mark.asyncio
async def test_get_run_by_id_not_found(client: AsyncClient) -> None:
    # Use trailing slash to bypass middleware retry that would override our custom 404 body.
    resp = await client.get("/api/agents/runs/nonexistent-run-id/")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_context_alias(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/agents/context/{run_id} returns same payload as /api/agent-runs/{run_id}/context."""
    await _create_agent(db_session, "ctx-alias-agent")
    run_id = await _create_run(db_session, "ctx-alias-agent")
    await repo.set_agent_context(db_session, run_id, "foo", "bar")
    await db_session.commit()

    resp_alias = await client.get(f"/api/agents/context/{run_id}")
    resp_canonical = await client.get(f"/api/agent-runs/{run_id}/context")
    assert resp_alias.status_code == 200
    assert resp_canonical.status_code == 200
    assert resp_alias.json() == resp_canonical.json()


@pytest.mark.asyncio
async def test_context_alias_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/agents/context/no-such-run")
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Slice D — Skill assignment routes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_and_unassign_skill(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /api/skills/{slug}/assign + /unassign round-trip."""
    await _create_agent(db_session, "assign-agent")
    await _create_skill(db_session, "assign-skill")
    # Use HTTP to get the agent PK
    agent_db_id = (await client.get("/api/agents/assign-agent")).json()["id"]

    assign_resp = await client.post(
        "/api/skills/assign-skill/assign", json={"agent_id": agent_db_id}
    )
    assert assign_resp.status_code == 200
    assert assign_resp.json()["ok"] is True

    # Skills list should have it
    skills_resp = await client.get("/api/agents/assign-agent/skills")
    slugs = [s["slug"] for s in skills_resp.json()["skills"]]
    assert "assign-skill" in slugs

    # Unassign
    unassign_resp = await client.post(
        "/api/skills/assign-skill/unassign", json={"agent_id": agent_db_id}
    )
    assert unassign_resp.status_code == 200

    # Skills list should be empty now
    skills_resp2 = await client.get("/api/agents/assign-agent/skills")
    assert skills_resp2.json()["skills"] == []


@pytest.mark.asyncio
async def test_assign_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    """Assigning the same skill twice is idempotent (no error)."""
    await _create_agent(db_session, "idem-agent")
    await _create_skill(db_session, "idem-skill")
    agent_db_id = (await client.get("/api/agents/idem-agent")).json()["id"]

    r1 = await client.post("/api/skills/idem-skill/assign", json={"agent_id": agent_db_id})
    r2 = await client.post("/api/skills/idem-skill/assign", json={"agent_id": agent_db_id})
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_assign_skill_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """Assigning a non-existent skill returns 404."""
    await _create_agent(db_session, "sk-404-agent")
    agent_db_id = (await client.get("/api/agents/sk-404-agent")).json()["id"]
    resp = await client.post("/api/skills/no-such-skill/assign", json={"agent_id": agent_db_id})
    assert resp.status_code == 404
    assert resp.json()["code"] == "skill_not_found"


@pytest.mark.asyncio
async def test_assign_agent_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """Assigning to a non-existent agent id returns 404."""
    await _create_skill(db_session, "orphan-skill")
    resp = await client.post("/api/skills/orphan-skill/assign", json={"agent_id": 999999})
    assert resp.status_code == 404
    assert resp.json()["code"] == "agent_not_found"


@pytest.mark.asyncio
async def test_unassign_noop(client: AsyncClient, db_session: AsyncSession) -> None:
    """Unassigning a skill that was never assigned is a no-op (200)."""
    await _create_agent(db_session, "noop-agent")
    await _create_skill(db_session, "noop-skill")
    agent_db_id = (await client.get("/api/agents/noop-agent")).json()["id"]
    resp = await client.post("/api/skills/noop-skill/unassign", json={"agent_id": agent_db_id})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_enriched_detail_shows_linked_skills(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /api/agents/{id} linkedSkills reflects assignments."""
    await _create_agent(db_session, "linked-agent")
    await _create_skill(db_session, "linked-skill")
    agent_db_id = (await client.get("/api/agents/linked-agent")).json()["id"]
    await client.post("/api/skills/linked-skill/assign", json={"agent_id": agent_db_id})

    resp = await client.get("/api/agents/linked-agent")
    assert resp.status_code == 200
    linked = resp.json()["linkedSkills"]
    assert len(linked) == 1
    assert linked[0]["slug"] == "linked-skill"


# ─────────────────────────────────────────────────────────────────────────────
# Migration round-trip test (lightweight — just verifies alembic chain runs)
# ─────────────────────────────────────────────────────────────────────────────


def test_migration_0023_revision_strings() -> None:
    """Verify the migration file has the correct revision chain wired."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(cfg)
    rev = scripts.get_revision("0023")
    assert rev is not None
    assert rev.revision == "0023"
    assert rev.down_revision == "0022"
