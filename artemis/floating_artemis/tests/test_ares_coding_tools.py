"""Tests for Ares Forge coding tools (chunk 2.1) — no DB required.

All tools are filesystem/subprocess only.  Tests use a tmp_path fixture so
they are fully isolated from the real repo tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artemis.floating_artemis.tool_registry import _build_ares_tool_registry
from artemis.floating_artemis.tools.core import (
    _make_git_diff,
    _make_git_status,
    _make_list_dir,
    _make_read_file,
    _safe_path_under,
)

pytestmark = pytest.mark.asyncio


# ── _safe_path_under ──────────────────────────────────────────────────────────


def test_safe_path_under_within_root(tmp_path: Path) -> None:
    result = _safe_path_under(tmp_path, "subdir/file.txt")
    assert result is not None
    assert result.is_relative_to(tmp_path)


def test_safe_path_under_traversal_blocked(tmp_path: Path) -> None:
    assert _safe_path_under(tmp_path, "../etc/passwd") is None


def test_safe_path_under_absolute_outside_blocked(tmp_path: Path) -> None:
    assert _safe_path_under(tmp_path, "/etc/passwd") is None


def test_safe_path_under_root_itself(tmp_path: Path) -> None:
    # "." resolves to root — allowed
    result = _safe_path_under(tmp_path, ".")
    assert result is not None
    assert result == tmp_path


# ── read_project_file ─────────────────────────────────────────────────────────


async def test_read_project_file_reads_contents(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    impl = _make_read_file(tmp_path)
    result = await impl({"path": "hello.txt"})
    assert result == "hello world"


async def test_read_project_file_nonexistent(tmp_path: Path) -> None:
    impl = _make_read_file(tmp_path)
    result = await impl({"path": "missing.txt"})
    assert "does not exist" in result


async def test_read_project_file_traversal_denied(tmp_path: Path) -> None:
    impl = _make_read_file(tmp_path)
    result = await impl({"path": "../etc/passwd"})
    assert "access denied" in result.lower()


async def test_read_project_file_absolute_outside_denied(tmp_path: Path) -> None:
    impl = _make_read_file(tmp_path)
    result = await impl({"path": "/etc/passwd"})
    assert "access denied" in result.lower()


async def test_read_project_file_truncates(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_text("x" * 200, encoding="utf-8")
    impl = _make_read_file(tmp_path)
    result = await impl({"path": "big.txt", "max_chars": 10})
    assert "truncated" in result
    assert result.startswith("x" * 10)


async def test_read_project_file_not_a_file(tmp_path: Path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    impl = _make_read_file(tmp_path)
    result = await impl({"path": "adir"})
    assert "not a file" in result


async def test_read_project_file_empty_path(tmp_path: Path) -> None:
    impl = _make_read_file(tmp_path)
    result = await impl({})
    assert "required" in result.lower()


# ── list_project_dir ──────────────────────────────────────────────────────────


async def test_list_project_dir_dirs_first_trailing_slash(tmp_path: Path) -> None:
    (tmp_path / "beta_dir").mkdir()
    (tmp_path / "alpha_dir").mkdir()
    (tmp_path / "z_file.txt").write_text("z")
    (tmp_path / "a_file.txt").write_text("a")
    impl = _make_list_dir(tmp_path)
    result = await impl({"path": "."})
    lines = result.splitlines()
    # All dirs come before all files
    dir_lines = [ln for ln in lines if ln.endswith("/")]
    file_lines = [ln for ln in lines if not ln.endswith("/")]
    assert dir_lines == ["alpha_dir/", "beta_dir/"]
    assert file_lines == ["a_file.txt", "z_file.txt"]
    # dirs appear before files in the full listing
    last_dir_idx = max(lines.index(d) for d in dir_lines)
    first_file_idx = min(lines.index(f) for f in file_lines)
    assert last_dir_idx < first_file_idx


async def test_list_project_dir_traversal_denied(tmp_path: Path) -> None:
    impl = _make_list_dir(tmp_path)
    result = await impl({"path": "../"})
    assert "access denied" in result.lower()


async def test_list_project_dir_nonexistent(tmp_path: Path) -> None:
    impl = _make_list_dir(tmp_path)
    result = await impl({"path": "no_such_dir"})
    assert "does not exist" in result


async def test_list_project_dir_subdirectory(tmp_path: Path) -> None:
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "file.py").write_text("pass")
    impl = _make_list_dir(tmp_path)
    result = await impl({"path": "subdir"})
    assert "file.py" in result


async def test_list_project_dir_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    impl = _make_list_dir(tmp_path)
    result = await impl({"path": "empty"})
    assert "empty" in result.lower()


# ── git_status ────────────────────────────────────────────────────────────────


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Initialise a minimal git repo in tmp_path."""
    subprocess.check_call(["git", "init", str(tmp_path)])
    subprocess.check_call(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
    )
    subprocess.check_call(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
    )
    # Initial commit so HEAD exists
    init_file = tmp_path / "init.txt"
    init_file.write_text("init")
    subprocess.check_call(["git", "add", "init.txt"], cwd=str(tmp_path))
    subprocess.check_call(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
    )
    return tmp_path


async def test_git_status_returns_branch_info(git_repo: Path) -> None:
    impl = _make_git_status(git_repo)
    result = await impl({})
    # --branch output starts with ##
    assert "##" in result


async def test_git_status_shows_untracked(git_repo: Path) -> None:
    (git_repo / "new_file.py").write_text("x = 1")
    impl = _make_git_status(git_repo)
    result = await impl({})
    assert "new_file.py" in result


async def test_git_status_clean_repo(git_repo: Path) -> None:
    impl = _make_git_status(git_repo)
    result = await impl({})
    # Should not raise; result is a string
    assert isinstance(result, str)


# ── git_diff ──────────────────────────────────────────────────────────────────


async def test_git_diff_empty_on_clean_repo(git_repo: Path) -> None:
    impl = _make_git_diff(git_repo)
    result = await impl({})
    # No uncommitted changes
    assert "clean" in result.lower() or result.strip() == ""


async def test_git_diff_shows_modified_content(git_repo: Path) -> None:
    f = git_repo / "init.txt"
    f.write_text("modified content here")
    impl = _make_git_diff(git_repo)
    result = await impl({})
    assert "modified content here" in result or "init.txt" in result


async def test_git_diff_path_arg_valid(git_repo: Path) -> None:
    f = git_repo / "init.txt"
    f.write_text("changed")
    impl = _make_git_diff(git_repo)
    result = await impl({"path": "init.txt"})
    assert isinstance(result, str)
    assert "changed" in result or "init.txt" in result


async def test_git_diff_path_traversal_denied(git_repo: Path) -> None:
    impl = _make_git_diff(git_repo)
    result = await impl({"path": "../etc/passwd"})
    assert "access denied" in result.lower()


# ── registry wiring ───────────────────────────────────────────────────────────

_BRIEF1_TOOLS = {"query_memory", "list_scopes", "surface_status", "read_file"}
_CODING_TOOLS = {"read_project_file", "list_project_dir", "git_status", "git_diff"}


def test_ares_registry_without_project_path_has_only_brief1_tools() -> None:
    """Brief-1 invariant: no project_path => exactly the 4 base tools."""
    registry = _build_ares_tool_registry("ares")
    names = {e.tool.name for e in registry.all_entries()}
    assert names == _BRIEF1_TOOLS


def test_ares_registry_with_project_path_has_8_tools(tmp_path: Path) -> None:
    """Brief-2: project_path present => base 4 + coding 4 = 8 tools total."""
    registry = _build_ares_tool_registry("ares", project_path=str(tmp_path))
    names = {e.tool.name for e in registry.all_entries()}
    assert names == _BRIEF1_TOOLS | _CODING_TOOLS


def test_ares_coding_tools_are_layer_1(tmp_path: Path) -> None:
    registry = _build_ares_tool_registry("ares", project_path=str(tmp_path))
    for name in _CODING_TOOLS:
        entry = registry.get(name)
        assert entry is not None, f"{name} not registered"
        assert entry.layer == 1, f"{name} should be layer 1"


def test_build_authorized_tool_registry_threads_project_path(tmp_path: Path) -> None:
    """build_authorized_tool_registry passes project_path to Ares's registry."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    registry = build_authorized_tool_registry(
        {"dev-projects"},
        agent_id="ares",
        project_path=str(tmp_path),
    )
    names = {e.tool.name for e in registry.all_entries()}
    assert names == _BRIEF1_TOOLS | _CODING_TOOLS


def test_build_authorized_tool_registry_no_project_path_brief1_invariant() -> None:
    """Omitting project_path from build_authorized_tool_registry keeps Brief-1 set."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    registry = build_authorized_tool_registry({"dev-projects"}, agent_id="ares")
    names = {e.tool.name for e in registry.all_entries()}
    assert names == _BRIEF1_TOOLS


def test_coding_tool_descriptions_have_layer_tag(tmp_path: Path) -> None:
    registry = _build_ares_tool_registry("ares", project_path=str(tmp_path))
    for name in _CODING_TOOLS:
        entry = registry.get(name)
        assert entry is not None
        assert "[layer:1]" in entry.tool.description, f"{name} missing [layer:1] tag"
