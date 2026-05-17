"""Tests for core Floating Artemis tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.core import (
    _REPO_ROOT,
    _propose_edit,
    _query_memory,
    _read_file,
    _safe_repo_path,
    _set_pref,
    _surface_status,
    register_core_tools,
)

pytestmark = pytest.mark.asyncio


# ── _safe_repo_path ───────────────────────────────────────────────────────────


def test_safe_repo_path_within_root() -> None:
    path = _safe_repo_path("artemis/config.py")
    assert path is not None
    assert path.is_relative_to(_REPO_ROOT)


def test_safe_repo_path_traversal_blocked() -> None:
    path = _safe_repo_path("../../etc/passwd")
    assert path is None


def test_safe_repo_path_absolute_style_blocked() -> None:
    path = _safe_repo_path("/etc/passwd")
    # Should resolve to /etc/passwd which is not inside _REPO_ROOT
    assert path is None


# ── read_file ─────────────────────────────────────────────────────────────────


async def test_read_file_valid_path() -> None:
    # Read pyproject.toml which we know exists in the repo root
    result = await _read_file({"path": "pyproject.toml"})
    assert "[project]" in result
    assert "artemis-os" in result


async def test_read_file_nonexistent() -> None:
    result = await _read_file({"path": "nonexistent_file_xyz.txt"})
    assert "does not exist" in result.lower() or "Error" in result


async def test_read_file_path_traversal_blocked() -> None:
    result = await _read_file({"path": "../../etc/passwd"})
    assert "access denied" in result.lower() or "outside" in result.lower()


async def test_read_file_empty_path() -> None:
    result = await _read_file({})
    assert "required" in result.lower() or "Error" in result


async def test_read_file_respects_max_chars() -> None:
    # pyproject.toml should be longer than 10 chars
    result = await _read_file({"path": "pyproject.toml", "max_chars": 10})
    assert "truncated" in result


# ── propose_edit ──────────────────────────────────────────────────────────────


async def test_propose_edit_creates_proposal() -> None:
    result = await _propose_edit(
        {
            "path": "artemis/config.py",
            "description": "Add new config field",
            "diff": "--- a/artemis/config.py\n+++ b/artemis/config.py",
        }
    )
    assert "file_edit_proposal" in result
    assert "artemis/config.py" in result


async def test_propose_edit_missing_path() -> None:
    result = await _propose_edit({"description": "something"})
    assert "required" in result.lower() or "Error" in result


async def test_propose_edit_missing_description() -> None:
    result = await _propose_edit({"path": "x.py"})
    assert "required" in result.lower() or "Error" in result


# ── surface_status ────────────────────────────────────────────────────────────


async def test_surface_status_returns_json() -> None:
    import json

    with patch("artemis.routes.status.get_status") as mock_status:
        mock_status.return_value = {
            "available_surfaces": ["okr", "agents"],
            "unavailable_surfaces": ["chat"],
        }
        result = await _surface_status({})

    data = json.loads(result)
    assert "available" in data
    assert "okr" in data["available"]


async def test_surface_status_handles_error() -> None:
    # get_status is imported lazily inside _surface_status; patch its source module
    with patch("artemis.routes.status.get_status", side_effect=Exception("db down")):
        result = await _surface_status({})
    assert "failed" in result.lower() or "Error" in result


# ── query_memory (mocked DB) ──────────────────────────────────────────────────


async def test_query_memory_returns_no_results() -> None:
    # _db is imported lazily inside _query_memory; trigger a graceful error via SessionLocal
    with patch("artemis.db.SessionLocal", side_effect=Exception("no mem")):
        result = await _query_memory({"query": "test"})
    # Should handle error gracefully (returns error string, not raise)
    assert isinstance(result, str)
    assert "failed" in result.lower() or "Memory" in result


# ── set_pref ──────────────────────────────────────────────────────────────────


async def test_set_pref_requires_key() -> None:
    result = await _set_pref({"value": "x"})
    assert "required" in result.lower() or "Error" in result


async def test_set_pref_calls_write_memory() -> None:
    with patch("artemis.floating_artemis.tools.core._write_memory", return_value="Memory written."):
        result = await _set_pref({"key": "theme", "value": "dark"})
    assert "Memory written" in result or result == "Memory written."


# ── register_core_tools ───────────────────────────────────────────────────────


def test_register_core_tools_all_registered() -> None:
    reg = AuthorizedToolRegistry()
    register_core_tools(reg)
    expected = {
        "query_memory",
        "write_memory",
        "list_scopes",
        "surface_status",
        "list_routes",
        "read_file",
        "propose_edit",
        "set_pref",
        "spawn_subagent",
    }
    registered = {e.tool.name for e in reg.all_entries()}
    assert expected == registered


def test_register_core_tools_layers() -> None:
    reg = AuthorizedToolRegistry()
    register_core_tools(reg)
    # Layer 1 tools
    for name in ["query_memory", "list_scopes", "surface_status", "list_routes", "read_file"]:
        entry = reg.get(name)
        assert entry is not None, f"{name} not found"
        assert entry.layer == 1, f"{name} should be layer 1, got {entry.layer}"
    # Layer 2 tools
    for name in ["write_memory", "propose_edit", "set_pref"]:
        entry = reg.get(name)
        assert entry is not None, f"{name} not found"
        assert entry.layer == 2, f"{name} should be layer 2, got {entry.layer}"
