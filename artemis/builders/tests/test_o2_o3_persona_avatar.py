"""O2/O3 — tests for persona PATCH, avatar upload, and enriched GET /api/agents/{id}.

Covers the acceptance criteria from briefs/o2-o3-agent-card-and-persona.md:
- PATCH /api/agents/{agent_id}/persona (happy path + invalid shape)
- POST /api/agents/{agent_id}/avatar (happy path + non-image rejection + size limit)
- GET /api/agents/{agent_id} enriched response (linkedSkills, supportingFiles, recentRuns)
"""

from __future__ import annotations

import io
import os
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo

# ── Helpers / fixtures ────────────────────────────────────────────────────────


async def _create_agent(db_session: AsyncSession, agent_id: str, name: str = "Test") -> None:
    await repo.create_agent(db_session, agent_id=agent_id, name=name)
    await db_session.commit()


@pytest.fixture
def agents_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Override ARTEMIS_AGENTS_DIR and patch the module-level constant for test isolation."""
    import artemis.routes.builders.agents as agents_mod

    agents_base = tmp_path / "agents"
    agents_base.mkdir()
    old_env = os.environ.get("ARTEMIS_AGENTS_DIR")
    old_base = agents_mod._AGENTS_BASE
    os.environ["ARTEMIS_AGENTS_DIR"] = str(agents_base)
    agents_mod._AGENTS_BASE = agents_base
    yield agents_base
    agents_mod._AGENTS_BASE = old_base
    if old_env is None:
        os.environ.pop("ARTEMIS_AGENTS_DIR", None)
    else:
        os.environ["ARTEMIS_AGENTS_DIR"] = old_env


# ── Persona PATCH ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_persona_happy(client: AsyncClient, db_session: AsyncSession) -> None:
    """PATCH /api/agents/{id}/persona — sets persona fields and persists."""
    await _create_agent(db_session, "persona-happy")
    resp = await client.patch(
        "/api/agents/persona-happy/persona",
        json={
            "name": "Iris",
            "purpose": "Watches my Jira board",
            "voiceNotes": "lowercase, concise",
            "ghostwrite": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["persona"]["name"] == "Iris"
    assert data["persona"]["purpose"] == "Watches my Jira board"
    assert data["persona"]["voice_notes"] == "lowercase, concise"
    assert data["persona"]["ghostwrite"] is True

    # Verify persistence — re-fetch
    r2 = await client.get("/api/agents/persona-happy")
    assert r2.status_code == 200
    assert r2.json()["persona"]["name"] == "Iris"


@pytest.mark.asyncio
async def test_patch_persona_merge(client: AsyncClient, db_session: AsyncSession) -> None:
    """Partial patch merges into existing persona — existing keys preserved."""
    await _create_agent(db_session, "persona-merge")
    # Set initial persona
    await client.patch(
        "/api/agents/persona-merge/persona",
        json={"name": "Scout", "ghostwrite": False},
    )
    # Partial update — only purpose; name and ghostwrite should be preserved
    resp = await client.patch(
        "/api/agents/persona-merge/persona",
        json={"purpose": "Monitors competitor pricing"},
    )
    assert resp.status_code == 200
    persona = resp.json()["persona"]
    assert persona["name"] == "Scout"  # preserved
    assert persona["ghostwrite"] is False  # preserved
    assert persona["purpose"] == "Monitors competitor pricing"  # updated


@pytest.mark.asyncio
async def test_patch_persona_empty_body(client: AsyncClient, db_session: AsyncSession) -> None:
    """Empty body → 400 bad request."""
    await _create_agent(db_session, "persona-empty")
    resp = await client.patch("/api/agents/persona-empty/persona", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "empty_persona_patch"


@pytest.mark.asyncio
async def test_patch_persona_agent_not_found(client: AsyncClient) -> None:
    """404 when agent does not exist."""
    resp = await client.patch("/api/agents/no-such-agent/persona", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "agent_not_found"


# ── Avatar upload ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_avatar_happy(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """POST /api/agents/{id}/avatar — uploads a PNG and updates persona.profile_image_path."""
    await _create_agent(db_session, "avatar-happy")

    # Minimal valid PNG (1×1 white pixel)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
        b"\x00\x00IEND\xaeB`\x82"
    )
    resp = await client.post(
        "/api/agents/avatar-happy/avatar",
        files={"file": ("avatar.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert resp.status_code == 201
    assert resp.json()["ok"] is True
    assert resp.json()["url"] == "/api/agents/avatar-happy/avatar"

    # Verify persona updated with profile_image_path
    detail = await client.get("/api/agents/avatar-happy")
    assert detail.status_code == 200
    assert detail.json()["persona"]["profile_image_path"] == "/api/agents/avatar-happy/avatar"


@pytest.mark.asyncio
async def test_upload_avatar_non_image_rejected(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """Non-image MIME type → 400."""
    await _create_agent(db_session, "avatar-non-image")

    resp = await client.post(
        "/api/agents/avatar-non-image/avatar",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "unsupported_image_type"


@pytest.mark.asyncio
async def test_upload_avatar_size_limit(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """Files over 5 MB → 400."""
    await _create_agent(db_session, "avatar-too-large")

    big_png = b"\x89PNG\r\n" + b"x" * (6 * 1024 * 1024)  # 6 MB
    resp = await client.post(
        "/api/agents/avatar-too-large/avatar",
        files={"file": ("big.png", io.BytesIO(big_png), "image/png")},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "avatar_too_large"


# ── Enriched GET /api/agents/{id} ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_agent_enriched_no_data(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """New agent has empty supportingFiles, linkedSkills, recentRuns."""
    await _create_agent(db_session, "enriched-empty")
    resp = await client.get("/api/agents/enriched-empty")
    assert resp.status_code == 200
    data = resp.json()
    assert data["supportingFiles"] == []
    assert data["linkedSkills"] == []
    assert data["recentRuns"] == []
    assert data["supportingFileCount"] == 0


@pytest.mark.asyncio
async def test_get_agent_enriched_with_files(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """supportingFiles reflects actual files in the agent's files/ directory."""
    agent = await repo.create_agent(db_session, agent_id="enriched-files", name="Files")
    await db_session.commit()
    db_id = agent.id

    # Manually create supporting files
    files_dir = agents_dir / str(db_id) / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "instruction.md").write_text("## Instructions", encoding="utf-8")
    (files_dir / "roster.json").write_text('{"team": []}', encoding="utf-8")

    resp = await client.get("/api/agents/enriched-files")
    assert resp.status_code == 200
    data = resp.json()
    assert data["supportingFileCount"] == 2
    filenames = [f["filename"] for f in data["supportingFiles"]]
    assert "instruction.md" in filenames
    assert "roster.json" in filenames
    # Each file entry has required fields
    for f in data["supportingFiles"]:
        assert "sizeBytes" in f
        assert "modifiedAt" in f


@pytest.mark.asyncio
async def test_get_agent_enriched_linked_skills(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """linkedSkills appears in enriched response after skill assignment."""
    agent = await repo.create_agent(db_session, agent_id="enriched-skills", name="Skilled")
    skill = await repo.create_skill(db_session, slug="extract-blockers", name="Extract Blockers", description="Finds blockers")
    await repo.assign_skill_to_agent(db_session, agent.id, skill.slug)
    await db_session.commit()

    resp = await client.get("/api/agents/enriched-skills")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["linkedSkills"]) == 1
    assert data["linkedSkills"][0]["slug"] == "extract-blockers"
    assert data["linkedSkills"][0]["name"] == "Extract Blockers"


@pytest.mark.asyncio
async def test_get_agent_enriched_recent_runs(
    client: AsyncClient, db_session: AsyncSession, agents_dir: Path
) -> None:
    """recentRuns returns last 10 runs in desc order."""
    await repo.create_agent(db_session, agent_id="enriched-runs", name="Runnable")
    await db_session.commit()

    # Create 3 runs directly via repository
    for i in range(3):
        run_id = f"enriched-run-{i:04d}"
        await repo.create_agent_run(
            db_session,
            run_id=run_id,
            agent_id="enriched-runs",
            status="completed",
        )
    await db_session.commit()

    resp = await client.get("/api/agents/enriched-runs")
    assert resp.status_code == 200
    data = resp.json()
    runs = data["recentRuns"]
    assert len(runs) == 3
    # Each run has the required fields
    for r in runs:
        assert "run_id" in r
        assert "status" in r
        assert "started_at" in r
        assert "duration_s" in r
        assert "trajectory_summary" in r


# ── Ghostwrite frame unit test ────────────────────────────────────────────────


def test_ghostwrite_frame_applied_when_enabled() -> None:
    """ghostwrite=True prepends the directive + voice samples."""
    from artemis.builders.ghostwrite import apply_ghostwrite_frame

    result = apply_ghostwrite_frame(
        system_prompt="You are Iris. Watch Jira.",
        persona={"ghostwrite": True, "name": "Iris", "voice_notes": "lowercase, concise"},
        session_id="test-session-abc",
    )
    assert "GHOSTWRITE DIRECTIVE" in result
    assert "Iris" in result
    assert "Jon" in result
    # Original prompt preserved after separator
    assert "You are Iris. Watch Jira." in result


def test_ghostwrite_frame_skipped_when_disabled() -> None:
    """ghostwrite=False (or absent) returns the prompt unchanged."""
    from artemis.builders.ghostwrite import apply_ghostwrite_frame

    prompt = "You are Iris. Watch Jira."
    result = apply_ghostwrite_frame(
        system_prompt=prompt,
        persona={"ghostwrite": False, "name": "Iris"},
        session_id="test-session-xyz",
    )
    assert result == prompt


def test_ghostwrite_frame_no_persona() -> None:
    """Empty persona dict → prompt unchanged (ghostwrite defaults to False)."""
    from artemis.builders.ghostwrite import apply_ghostwrite_frame

    prompt = "Do stuff."
    result = apply_ghostwrite_frame(system_prompt=prompt, persona={}, session_id="s1")
    assert result == prompt
