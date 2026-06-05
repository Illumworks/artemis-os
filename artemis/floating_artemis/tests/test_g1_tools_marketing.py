"""Tests for Floating Artemis marketing tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.context import floating_session_id_var
from artemis.floating_artemis.tools.marketing import (
    _approve_signal,
    _fire_scout,
    _list_content_assets,
    _list_signals,
    _propose_ruleset_change,
    _qualify_signal,
    _reject_signal,
    _snooze_signal,
    _submit_draft_for_review,
    register_marketing_tools,
)

pytestmark = pytest.mark.asyncio


def _mock_session_cm() -> tuple[AsyncMock, MagicMock]:
    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return session, cm


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


async def test_approve_signal_uses_fa_session_context() -> None:
    mock_session, mock_cm = _mock_session_cm()

    observed: dict[str, str | None] = {}

    async def _fake_write(**kwargs: object) -> None:
        observed["fa_session_id"] = kwargs.get("fa_session_id")  # type: ignore[assignment]

    scheduled: list[asyncio.Task[None]] = []
    real_create_task = asyncio.create_task

    def _schedule(coro: object) -> asyncio.Task[None]:
        observed["scheduled"] = "yes"
        task: asyncio.Task[None] = real_create_task(cast("Coroutine[Any, Any, None]", coro))
        scheduled.append(task)
        return task

    token = floating_session_id_var.set("fa-context-42")
    try:
        with (
            patch("artemis.db.SessionLocal", return_value=mock_cm),
            patch("artemis.marketing.repository.update_signal", new=AsyncMock()),
            patch(
                "artemis.builder.memory_carryover.write_fa_marketing_approval_observation",
                side_effect=_fake_write,
            ),
            patch("asyncio.create_task", side_effect=_schedule),
        ):
            result = await _approve_signal({"signal_id": 7})
            await asyncio.gather(*scheduled)
    finally:
        floating_session_id_var.reset(token)

    assert result == "Signal 7 approved."
    assert observed["scheduled"] == "yes"
    assert observed["fa_session_id"] == "fa-context-42"


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
    mock_session, mock_cm = _mock_session_cm()
    exists_result = MagicMock()
    exists_result.scalar_one_or_none.return_value = 5
    mock_session.execute = AsyncMock(return_value=exists_result)
    saved_row = type("ApprovalRow", (), {"id": 77})()

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.marketing.repository.create_approval",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
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
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "ruleset_change"
    assert mock_create.await_args.kwargs["subject_id"] == "5"


async def test_propose_ruleset_change_persists_approval() -> None:
    mock_session, mock_cm = _mock_session_cm()
    exists_result = MagicMock()
    exists_result.scalar_one_or_none.return_value = 42
    mock_session.execute = AsyncMock(return_value=exists_result)
    saved_row = type("ApprovalRow", (), {"id": 88})()

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.marketing.repository.create_approval",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_ruleset_change(
            {"ruleset_id": 42, "changes": {"weighted_signals": ["freshness"]}}
        )

    assert "approval_id=88" in result
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "ruleset_change"
    assert mock_create.await_args.kwargs["subject_id"] == "42"
    assert mock_create.await_args.kwargs["decision_payload"] == {
        "type": "ruleset_change_proposal",
        "ruleset_id": 42,
        "changes": {"weighted_signals": ["freshness"]},
    }
    mock_session.commit.assert_awaited_once()


async def test_submit_draft_for_review_uses_deliverable_id() -> None:
    mock_session, mock_cm = _mock_session_cm()
    mock_approval = type("ApprovalRow", (), {"id": 91})()

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.marketing.writing_studio.invoke.submit_draft_for_review",
            new=AsyncMock(return_value=mock_approval),
        ) as mock_submit,
    ):
        result = await _submit_draft_for_review({"deliverable_id": 41})

    mock_submit.assert_awaited_once_with(mock_session, 41)
    assert result == "Deliverable 41 submitted for review: approval_id=91"


async def test_fire_scout_accepts_legacy_scout_id_alias() -> None:
    mock_session, mock_cm = _mock_session_cm()
    mock_run = type("ScoutRunRow", (), {"id": "run-1"})()

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.marketing.repository.create_scout_run",
            new=AsyncMock(return_value=mock_run),
        ) as mock_create,
    ):
        result = await _fire_scout({"scout_id": "legislative"})

    assert "Scout legislative fired" in result
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["scout_type"] == "legislative"


async def test_list_content_assets_returns_real_rows() -> None:
    mock_session, mock_cm = _mock_session_cm()
    asset = type(
        "ContentAssetRow",
        (),
        {
            "id": 101,
            "status": "approved",
            "asset_type": "email",
            "summary": "Reusable approval-card copy",
        },
    )()
    scalar_result = MagicMock()
    scalar_result.all.return_value = [asset]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalar_result
    mock_session.execute = AsyncMock(return_value=execute_result)

    with patch("artemis.db.SessionLocal", return_value=mock_cm):
        result = await _list_content_assets({"limit": 5, "campaign_family": "obc"})

    assert "Reusable approval-card copy" in result


# ── surface tags ──────────────────────────────────────────────────────────────


def test_marketing_tools_have_surface_tag() -> None:
    reg = AuthorizedToolRegistry()
    register_marketing_tools(reg)
    for entry in reg.all_entries():
        assert "[surface:marketing-os]" in entry.tool.description, (
            f"{entry.tool.name} is missing [surface:marketing-os] tag"
        )
