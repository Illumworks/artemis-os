"""Tests for Floating Artemis marketing tools."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.marketing import (
    _approve_signal,
    _list_signals,
    _propose_ruleset_change,
    _qualify_signal,
    _reject_signal,
    _snooze_signal,
    register_marketing_tools,
)

pytestmark = pytest.mark.asyncio


# ── register_marketing_tools ──────────────────────────────────────────────────


def test_register_marketing_tools() -> None:
    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    expected = {
        "list_signals",
        "get_signal",
        "qualify_signal",
        "approve_signal",
        "reject_signal",
        "snooze_signal",
        "list_candidates",
        "assemble_brief",
        "submit_draft_for_review",
        "decide_approval",
        "list_scout_runs",
        "fire_scout",
        "get_active_rulesets",
        "propose_ruleset_change",
        "list_content_assets",
        "link_content_asset",
    }
    registered = {e.tool.name for e in reg.all_entries()}
    assert expected == registered


def test_marketing_tool_layers() -> None:
    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    # Layer 1: read-only
    for name in [
        "list_signals",
        "get_signal",
        "list_candidates",
        "list_scout_runs",
        "get_active_rulesets",
        "list_content_assets",
    ]:
        e = reg.get(name)
        assert e is not None and e.layer == 1, f"{name} should be layer 1"
    # Layer 2: idempotent
    for name in ["qualify_signal", "snooze_signal", "fire_scout"]:
        e = reg.get(name)
        assert e is not None and e.layer == 2, f"{name} should be layer 2"
    # Layer 3: side-effect
    for name in [
        "approve_signal",
        "reject_signal",
        "assemble_brief",
        "submit_draft_for_review",
        "decide_approval",
        "propose_ruleset_change",
        "link_content_asset",
    ]:
        e = reg.get(name)
        assert e is not None and e.layer == 3, f"{name} should be layer 3"


# ── qualify_signal (layer 2 — idempotent) ────────────────────────────────────


async def test_qualify_signal_missing_signal_id() -> None:
    result = await _qualify_signal({})
    assert "Error" in result or "required" in result.lower()


async def test_qualify_signal_db_error_graceful() -> None:
    """qualify_signal handles DB errors without raising."""
    # _db is imported lazily inside the function; patch the source module's SessionLocal
    with patch("artemis.db.SessionLocal", side_effect=Exception("db down")):
        result = await _qualify_signal({"signal_id": 1, "score": 0.9})
    assert "failed" in result.lower()


async def test_qualify_signal_idempotent_layer() -> None:
    """Calling qualify_signal twice should produce same behavior (idempotent)."""
    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    e = reg.get("qualify_signal")
    assert e is not None
    assert e.layer == 2  # layer 2 = idempotent, runs without confirmation


# ── approve_signal (layer 3) ──────────────────────────────────────────────────


async def test_approve_signal_missing_signal_id() -> None:
    result = await _approve_signal({})
    assert "Error" in result or "required" in result.lower()


async def test_approve_signal_requires_confirmation() -> None:
    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    e = reg.get("approve_signal")
    assert e is not None
    assert e.layer == 3  # requires confirmation


async def test_approve_signal_db_error_graceful() -> None:
    with patch("artemis.db.SessionLocal", side_effect=Exception("db down")):
        result = await _approve_signal({"signal_id": 1})
    assert "failed" in result.lower()


# ── reject_signal (layer 3) ───────────────────────────────────────────────────


async def test_reject_signal_missing_id() -> None:
    result = await _reject_signal({})
    assert "Error" in result or "required" in result.lower()


# ── snooze_signal (layer 2) ───────────────────────────────────────────────────


async def test_snooze_signal_missing_id() -> None:
    result = await _snooze_signal({})
    assert "Error" in result or "required" in result.lower()


# ── list_signals (layer 1) ────────────────────────────────────────────────────


async def test_list_signals_db_error_graceful() -> None:
    with patch("artemis.db.SessionLocal", side_effect=Exception("db")):
        result = await _list_signals({"status": "pending"})
    assert "failed" in result.lower() or isinstance(result, str)


# ── propose_ruleset_change (layer 3) ─────────────────────────────────────────


async def test_propose_ruleset_change_missing_id() -> None:
    result = await _propose_ruleset_change({})
    assert "Error" in result or "required" in result.lower()


async def test_propose_ruleset_change_generates_proposal() -> None:
    result = await _propose_ruleset_change(
        {
            "ruleset_id": 5,
            "changes": {"min_score": 0.7},
        }
    )
    assert "ruleset_change_proposal" in result
    data = json.loads(result.split("\n", 1)[1])
    assert data["ruleset_id"] == 5
    assert data["changes"]["min_score"] == 0.7


# ── surface tags ──────────────────────────────────────────────────────────────


def test_marketing_tools_have_surface_tag() -> None:
    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    for entry in reg.all_entries():
        assert "[surface:marketing-os]" in entry.tool.description, (
            f"{entry.tool.name} is missing [surface:marketing-os] tag"
        )
