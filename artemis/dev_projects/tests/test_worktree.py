"""Tests for artemis.dev_projects.worktree (Forge Phase 3, chunk 3.2).

Uses real git in a tmp directory -- NO app DB required.

Test matrix:
  - ensure_worktree creates a worktree dir on branch forge/session-1
  - the worktree is on the right branch; the main repo HEAD is unchanged
  - editing + committing IN the worktree does not affect the main tree
  - ensure_worktree is idempotent (second call returns same path, no error)
  - remove_worktree(delete_branch=True) removes the dir AND the branch
  - worktree_path_for is deterministic
  - session_lock returns the same Lock object for the same id
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from artemis.dev_projects.worktree import (
    WorktreeError,
    ensure_worktree,
    remove_worktree,
    session_lock,
    worktree_path_for,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: str | None = None) -> str:
    """Run a git command synchronously and return stdout (stripped).

    Raises subprocess.CalledProcessError on non-zero exit.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
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


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A fresh git repo with one commit."""
    return _make_repo(tmp_path)


@pytest.fixture(autouse=True)
def _set_base_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ARTEMIS_FORGE_WORKTREE_BASE_DIR at a temp location so tests never
    touch ~/.artemis/forge-worktrees."""
    base = tmp_path / "forge-worktrees"
    monkeypatch.setenv("ARTEMIS_FORGE_WORKTREE_BASE_DIR", str(base))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_ensure_worktree_creates_dir_on_correct_branch(
    tmp_repo: Path,
) -> None:
    """ensure_worktree creates the worktree dir on branch forge/session-1."""
    project_path = str(tmp_repo)
    session_id = 1

    wt_str = await ensure_worktree(project_path, session_id)
    wt_path = Path(wt_str)

    assert wt_path.exists(), "worktree directory must exist after ensure_worktree"

    # The worktree must be on the correct branch.
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_str)
    assert branch == "forge/session-1", f"expected forge/session-1, got {branch!r}"


async def test_worktree_does_not_affect_main_tree(
    tmp_repo: Path,
) -> None:
    """Committing in the worktree must leave the main tree's HEAD unchanged."""
    project_path = str(tmp_repo)
    main_head_before = _git("rev-parse", "HEAD", cwd=project_path)

    wt_str = await ensure_worktree(project_path, 2)

    # Write and commit a file inside the worktree.
    (Path(wt_str) / "forge_output.txt").write_text("change from forge\n")
    _git("add", ".", cwd=wt_str)
    _git("config", "user.email", "forge@example.com", cwd=wt_str)
    _git("config", "user.name", "Forge Agent", cwd=wt_str)
    _git("commit", "-m", "forge edit", cwd=wt_str)

    # Main tree status must be clean.
    status = _git("status", "--porcelain", cwd=project_path)
    assert status == "", f"main tree must be clean but got: {status!r}"

    # Main HEAD must be unchanged.
    main_head_after = _git("rev-parse", "HEAD", cwd=project_path)
    assert main_head_before == main_head_after, "main HEAD changed after a commit in the worktree"


async def test_ensure_worktree_is_idempotent(
    tmp_repo: Path,
) -> None:
    """Calling ensure_worktree twice for the same session must succeed and
    return the same path without error."""
    project_path = str(tmp_repo)
    session_id = 3

    first = await ensure_worktree(project_path, session_id)
    second = await ensure_worktree(project_path, session_id)

    assert first == second, "idempotent calls must return the same path"
    assert Path(first).exists()


async def test_remove_worktree_deletes_dir_and_branch(
    tmp_repo: Path,
) -> None:
    """remove_worktree(delete_branch=True) must remove the dir and the branch."""
    project_path = str(tmp_repo)
    session_id = 4

    wt_str = await ensure_worktree(project_path, session_id)
    wt_path = Path(wt_str)
    assert wt_path.exists()

    await remove_worktree(project_path, session_id, delete_branch=True)

    assert not wt_path.exists(), "worktree dir must be gone after remove_worktree"

    # Branch must be gone.
    branches = _git("branch", "--list", "forge/session-4", cwd=project_path)
    assert branches == "", f"branch must be deleted but got: {branches!r}"


async def test_remove_worktree_without_delete_branch(
    tmp_repo: Path,
) -> None:
    """remove_worktree(delete_branch=False) removes the dir but keeps the branch."""
    project_path = str(tmp_repo)
    session_id = 5

    await ensure_worktree(project_path, session_id)
    await remove_worktree(project_path, session_id, delete_branch=False)

    wt_path = worktree_path_for(project_path, session_id)
    assert not wt_path.exists(), "worktree dir must be gone"

    # Branch must still exist.
    branches = _git("branch", "--list", "forge/session-5", cwd=project_path)
    assert "forge/session-5" in branches, "branch must still exist when delete_branch=False"


async def test_worktree_path_for_is_deterministic(
    tmp_repo: Path,
) -> None:
    """worktree_path_for must return the same Path across calls."""
    project_path = str(tmp_repo)

    p1 = worktree_path_for(project_path, 7)
    p2 = worktree_path_for(project_path, 7)
    p3 = worktree_path_for(project_path, 8)

    assert p1 == p2, "same inputs must produce same path"
    assert p1 != p3, "different session_ids must produce different paths"

    # The path must contain the session_id and a slug derived from the project name.
    assert "7" in str(p1)
    assert "project" in str(p1)  # tmp_repo is called "project" by _make_repo


async def test_ensure_worktree_raises_on_non_git_dir(
    tmp_path: Path,
) -> None:
    """ensure_worktree must raise WorktreeError when project_path is not a git repo."""
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()

    with pytest.raises(WorktreeError, match="not inside a git repository"):
        await ensure_worktree(str(not_a_repo), 99)


async def test_session_lock_returns_same_lock_for_same_id() -> None:
    """session_lock must return the identical Lock object for repeated calls."""
    lock_a = session_lock(100)
    lock_b = session_lock(100)
    lock_c = session_lock(101)

    assert lock_a is lock_b, "same session_id must return the same Lock object"
    assert lock_a is not lock_c, "different session_ids must return different Locks"
    assert isinstance(lock_a, asyncio.Lock)


async def test_session_lock_serializes_concurrent_turns() -> None:
    """Two concurrent coroutines holding the same session lock must not overlap."""
    results: list[str] = []

    async def turn(name: str) -> None:
        async with session_lock(200):
            results.append(f"{name}:enter")
            await asyncio.sleep(0)  # yield to let the other coroutine attempt entry
            results.append(f"{name}:exit")

    await asyncio.gather(turn("A"), turn("B"))

    # Whichever turn acquired the lock first must fully complete before the other enters.
    enter_a = results.index("A:enter")
    exit_a = results.index("A:exit")
    enter_b = results.index("B:enter")
    exit_b = results.index("B:exit")

    # A must exit before B enters (or B exits before A enters).
    non_overlapping = (exit_a < enter_b) or (exit_b < enter_a)
    assert non_overlapping, f"turns overlapped unexpectedly: {results}"


async def test_branch_reuse_when_branch_already_exists(
    tmp_repo: Path,
) -> None:
    """If forge/session-N branch exists but the worktree dir was removed,
    ensure_worktree must reattach without -b (no 'branch already exists' error)."""
    project_path = str(tmp_repo)
    session_id = 6

    # Create and then remove without deleting the branch.
    await ensure_worktree(project_path, session_id)
    await remove_worktree(project_path, session_id, delete_branch=False)

    # Branch still exists; worktree dir is gone.
    branches = _git("branch", "--list", "forge/session-6", cwd=project_path)
    assert "forge/session-6" in branches

    wt_path = worktree_path_for(project_path, session_id)
    assert not wt_path.exists()

    # Second ensure must succeed by attaching to the existing branch.
    wt_str = await ensure_worktree(project_path, session_id)
    assert Path(wt_str).exists()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_str)
    assert branch == "forge/session-6"


async def test_push_block_not_applied_without_origin(
    tmp_repo: Path,
) -> None:
    """The push-block is silently skipped when the repo has no 'origin' remote.

    This is the expected behaviour for test repos and on-disk-only projects.
    ensure_worktree must not raise.
    """
    project_path = str(tmp_repo)
    # Confirm no remotes.
    remotes = _git("remote", cwd=project_path)
    assert remotes == "", "test repo must have no remotes"

    # Should complete without error.
    wt_str = await ensure_worktree(project_path, 42)
    assert Path(wt_str).exists()

    # No pushurl config entry should exist (would error if it did try to set it
    # without extensions.worktreeConfig being available first, but mainly we
    # verify no exception was raised and the worktree is functional).
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_str)
    assert branch == "forge/session-42"


async def test_startup_sweep_removes_orphan(
    tmp_repo: Path,
    tmp_path: Path,
) -> None:
    """startup_sweep must remove dirs whose session_ids are not in the live set."""
    # Use an explicit base dir that we control (autouse fixture already set it).
    project_path = str(tmp_repo)

    # Create two worktrees.
    await ensure_worktree(project_path, 10)
    await ensure_worktree(project_path, 11)

    wt10 = worktree_path_for(project_path, 10)
    wt11 = worktree_path_for(project_path, 11)
    assert wt10.exists()
    assert wt11.exists()

    # Session 10 is live; session 11 is an orphan.
    async def live_ids() -> set[int]:
        return {10}

    from artemis.dev_projects.worktree import startup_sweep

    count = await startup_sweep(live_ids)

    assert count == 1, f"expected 1 pruned, got {count}"
    assert wt10.exists(), "live worktree must not be removed"
    assert not wt11.exists(), "orphan worktree must be removed"


async def test_startup_sweep_no_resolver_leaves_dirs(
    tmp_repo: Path,
) -> None:
    """startup_sweep with no resolver must not remove anything."""
    project_path = str(tmp_repo)
    await ensure_worktree(project_path, 20)

    wt = worktree_path_for(project_path, 20)
    assert wt.exists()

    from artemis.dev_projects.worktree import startup_sweep

    count = await startup_sweep(None)
    assert count == 0
    assert wt.exists(), "dirs must be untouched when no resolver is provided"
