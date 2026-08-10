"""Route tests for Forge worktree review/merge endpoints (Phase 3, chunk 3.5).

Endpoints under test:
  GET  /api/dev-projects/sessions/{session_id}/worktree/status
  GET  /api/dev-projects/sessions/{session_id}/worktree/diff
  POST /api/dev-projects/sessions/{session_id}/worktree/merge
  DEL  /api/dev-projects/sessions/{session_id}/worktree

Test strategy:
  - Real tmp git repo as the DevProject.path (no mocking of git).
  - Test DB via ARTEMIS_TEST_DB_URL (mirrors test_active_run_route.py).
  - Engine override before importing artemis.main, ASGITransport client.
  - A session+worktree fixture sets up a DB row, an ensure_worktree call,
    and a commit on the forge branch so there is real work to review.
  - Key assertions: merge lands the branch commit in the main tree;
    conflict and dirty-tree paths return 409, not 500.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_worktree_routes: db_url={_DB_URL!r} is not a test database."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

# Import after engine override.
from artemis.main import app  # noqa: E402

_TRUNCATE = text(
    "TRUNCATE forge_run_log, forge_runs, dev_sessions, dev_projects "
    "RESTART IDENTITY CASCADE"
)


# ---------------------------------------------------------------------------
# Sync git helpers (used in fixtures only)
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_nofail(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _make_repo(base: Path) -> Path:
    """Create a minimal git repo with one commit; return its path."""
    repo = base / "project"
    repo.mkdir()
    _git("init", cwd=str(repo))
    _git("config", "user.email", "test@example.com", cwd=str(repo))
    _git("config", "user.name", "Test User", cwd=str(repo))
    (repo / "README.md").write_text("hello\n")
    _git("add", ".", cwd=str(repo))
    _git("commit", "-m", "init", cwd=str(repo))
    return repo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_base_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point worktree base dir at tmp so tests never touch ~/.artemis."""
    base = tmp_path / "forge-worktrees"
    monkeypatch.setenv("ARTEMIS_FORGE_WORKTREE_BASE_DIR", str(base))


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with table reset before and after."""
    async with AsyncSession(_test_engine, expire_on_commit=False) as s:
        await s.execute(_TRUNCATE)
        await s.commit()
        yield s
        await s.execute(_TRUNCATE)
        await s.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def project_and_session(
    tmp_path: Path, db_session: AsyncSession
) -> dict[str, Any]:
    """Insert a DevProject pointing at a real tmp git repo + a forge-mode session.

    Returns a dict with keys: project_id, session_id, repo_path.
    The worktree is NOT created here; individual tests call ensure_worktree
    (or the endpoint) themselves.
    """
    repo = _make_repo(tmp_path)

    proj = await db_session.execute(
        text(
            "INSERT INTO dev_projects (name, path) VALUES (:n, :p) RETURNING id"
        ),
        {"n": "wt-test-project", "p": str(repo)},
    )
    project_id = proj.scalar_one()

    sess = await db_session.execute(
        text(
            "INSERT INTO dev_sessions (project_id, provider, forge_mode) "
            "VALUES (:pid, 'claude-code', 'write') RETURNING id"
        ),
        {"pid": project_id},
    )
    session_id = sess.scalar_one()
    await db_session.commit()

    return {"project_id": project_id, "session_id": session_id, "repo_path": repo}


# ---------------------------------------------------------------------------
# Helper: set up a worktree with at least one commit on the forge branch
# ---------------------------------------------------------------------------


async def _setup_worktree_with_commit(
    repo_path: Path, session_id: int
) -> None:
    """Create the forge worktree and add a commit to it."""
    from artemis.dev_projects.worktree import ensure_worktree

    wt_str = await ensure_worktree(str(repo_path), session_id)
    wt = Path(wt_str)

    # Write a file and commit it inside the worktree.
    (wt / "feature.txt").write_text("new feature\n")
    _git("add", ".", cwd=str(wt))
    _git("commit", "-m", "add feature", cwd=str(wt))


# ---------------------------------------------------------------------------
# Tests: GET /worktree/status
# ---------------------------------------------------------------------------


async def test_status_exists_false_when_no_worktree(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """When the worktree directory does not exist, exists=false with zeros."""
    sid = project_and_session["session_id"]
    resp = await client.get(f"/api/dev-projects/sessions/{sid}/worktree/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is False
    assert data["ahead"] == 0
    assert data["dirty_files"] == 0
    assert data["commits"] == []
    assert data["branch"] == f"forge/session-{sid}"


async def test_status_ahead_after_commit(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """After a commit on the forge branch, status shows ahead>=1 and the commit."""
    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)

    resp = await client.get(f"/api/dev-projects/sessions/{sid}/worktree/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["ahead"] >= 1
    assert len(data["commits"]) >= 1
    commit = data["commits"][0]
    assert "sha" in commit
    assert "subject" in commit
    assert "add feature" in commit["subject"]


async def test_status_dirty_files(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """Uncommitted file in the worktree shows up in dirty_files."""
    from artemis.dev_projects.worktree import ensure_worktree

    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    wt_str = await ensure_worktree(str(repo), sid)
    wt = Path(wt_str)

    # Write a file but do NOT commit it.
    (wt / "dirty.txt").write_text("uncommitted\n")
    _git("add", "dirty.txt", cwd=str(wt))  # staged but not committed

    resp = await client.get(f"/api/dev-projects/sessions/{sid}/worktree/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["dirty_files"] >= 1


async def test_status_unknown_session_returns_404(client: AsyncClient) -> None:
    """Unknown session_id -> 404."""
    resp = await client.get("/api/dev-projects/sessions/99999/worktree/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /worktree/diff
# ---------------------------------------------------------------------------


async def test_diff_empty_when_no_worktree(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """No worktree -> empty diff, truncated=false, 200."""
    sid = project_and_session["session_id"]
    resp = await client.get(f"/api/dev-projects/sessions/{sid}/worktree/diff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["diff"] == ""
    assert data["truncated"] is False
    assert data["branch"] == f"forge/session-{sid}"


async def test_diff_non_empty_after_commit(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """After a commit on the forge branch, diff is non-empty."""
    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)

    resp = await client.get(f"/api/dev-projects/sessions/{sid}/worktree/diff")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["diff"]) > 0
    assert "feature.txt" in data["diff"]
    assert data["truncated"] is False


async def test_diff_unknown_session_returns_404(client: AsyncClient) -> None:
    """Unknown session_id -> 404."""
    resp = await client.get("/api/dev-projects/sessions/99999/worktree/diff")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /worktree/merge
# ---------------------------------------------------------------------------


async def test_merge_lands_commit_in_main_tree(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """Merge applies the forge-branch commit to the main tree."""
    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)

    resp = await client.post(f"/api/dev-projects/sessions/{sid}/worktree/merge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["merged"] is True
    assert data["branch"] == f"forge/session-{sid}"
    assert "into" in data

    # The commit must now appear in the main tree's log.
    log = _git("log", "--oneline", cwd=str(repo))
    assert "add feature" in log


async def test_merge_cleans_up_worktree(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """After a successful merge, the worktree directory is removed."""
    from artemis.dev_projects.worktree import worktree_path_for

    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)
    wt_path = worktree_path_for(str(repo), sid)
    assert wt_path.exists()

    resp = await client.post(f"/api/dev-projects/sessions/{sid}/worktree/merge")
    assert resp.status_code == 200
    assert not wt_path.exists()


async def test_merge_dirty_main_tree_returns_409(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """A dirty main tree triggers 409 before the merge is attempted."""
    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)

    # Dirty the main tree.
    (repo / "dirty.txt").write_text("dirty\n")
    _git("add", "dirty.txt", cwd=str(repo))

    resp = await client.post(f"/api/dev-projects/sessions/{sid}/worktree/merge")
    assert resp.status_code == 409
    body = resp.json()
    # The app exception handler returns the dict directly (no "detail" wrapper).
    assert body["merged"] is False
    assert "dirty" in body["detail"].lower()


async def test_merge_missing_branch_returns_409(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """When the forge branch does not exist, returns 409 not 500."""
    sid = project_and_session["session_id"]
    # No worktree / branch created.
    resp = await client.post(f"/api/dev-projects/sessions/{sid}/worktree/merge")
    assert resp.status_code == 409


async def test_merge_squash(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """squash=true produces a single squash commit in the main tree."""
    from artemis.dev_projects.worktree import ensure_worktree

    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    wt_str = await ensure_worktree(str(repo), sid)
    wt = Path(wt_str)

    # Two commits on the forge branch.
    (wt / "a.txt").write_text("a\n")
    _git("add", ".", cwd=str(wt))
    _git("commit", "-m", "first", cwd=str(wt))
    (wt / "b.txt").write_text("b\n")
    _git("add", ".", cwd=str(wt))
    _git("commit", "-m", "second", cwd=str(wt))

    resp = await client.post(
        f"/api/dev-projects/sessions/{sid}/worktree/merge",
        json={"squash": True, "message": "squash both"},
    )
    assert resp.status_code == 200
    assert resp.json()["merged"] is True

    log = _git("log", "--oneline", cwd=str(repo))
    assert "squash both" in log


async def test_merge_unknown_session_returns_404(client: AsyncClient) -> None:
    """Unknown session_id -> 404."""
    resp = await client.post("/api/dev-projects/sessions/99999/worktree/merge")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: DELETE /worktree (discard)
# ---------------------------------------------------------------------------


async def test_discard_removes_worktree_and_branch(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """Discard removes the worktree dir and the forge branch."""
    from artemis.dev_projects.worktree import worktree_path_for

    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]
    branch = f"forge/session-{sid}"

    await _setup_worktree_with_commit(repo, sid)
    wt_path = worktree_path_for(str(repo), sid)
    assert wt_path.exists()

    resp = await client.delete(f"/api/dev-projects/sessions/{sid}/worktree")
    assert resp.status_code == 200
    assert resp.json()["discarded"] is True
    assert not wt_path.exists()

    # Branch should be gone.
    branch_list = _git_nofail("branch", "--list", branch, cwd=str(repo))
    assert branch not in branch_list


async def test_discard_safe_when_no_worktree(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """Discard when nothing exists returns 200 (idempotent / safe)."""
    sid = project_and_session["session_id"]
    resp = await client.delete(f"/api/dev-projects/sessions/{sid}/worktree")
    assert resp.status_code == 200
    assert resp.json()["discarded"] is True


async def test_discard_unknown_session_returns_404(client: AsyncClient) -> None:
    """Unknown session_id -> 404."""
    resp = await client.delete("/api/dev-projects/sessions/99999/worktree")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: second merge/discard after first succeed (idempotency)
# ---------------------------------------------------------------------------


async def test_second_merge_after_successful_merge_returns_409(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """A second merge call when the branch is already gone returns 409."""
    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)

    r1 = await client.post(f"/api/dev-projects/sessions/{sid}/worktree/merge")
    assert r1.status_code == 200

    r2 = await client.post(f"/api/dev-projects/sessions/{sid}/worktree/merge")
    assert r2.status_code == 409


async def test_second_discard_is_safe(
    client: AsyncClient,
    project_and_session: dict[str, Any],
) -> None:
    """Two discard calls in a row both return 200."""
    sid = project_and_session["session_id"]
    repo = project_and_session["repo_path"]

    await _setup_worktree_with_commit(repo, sid)

    r1 = await client.delete(f"/api/dev-projects/sessions/{sid}/worktree")
    assert r1.status_code == 200

    r2 = await client.delete(f"/api/dev-projects/sessions/{sid}/worktree")
    assert r2.status_code == 200
