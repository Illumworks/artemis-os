from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import Message, TextBlock
from artemis.floating_artemis.chat import _build_system_prompt, _build_tool_registry, handle_turn
from artemis.floating_artemis.session_scope import (
    _MARKETING_SURFACES,
    is_personal_slack_dm_session,
    parse_history_cutover_at,
    personal_surfaces,
    resolve_surface_scope,
)


def test_personal_surfaces_remove_marketing_surfaces() -> None:
    all_surfaces = {
        "okr",
        "marketing-os",
        "signal-queue",
        "campaign-ops",
        "floating-artemis",
        "integrations",
    }
    assert personal_surfaces(all_surfaces) == {"okr", "floating-artemis", "integrations"}


def test_is_personal_slack_dm_session_requires_dm_channel() -> None:
    assert (
        is_personal_slack_dm_session(
            "slack-artemis-T1-D123-_",
            {"surface": "slack", "agent_id": "artemis", "channel_id": "D123"},
        )
        is True
    )
    assert (
        is_personal_slack_dm_session(
            "slack-artemis-T1-C123-_",
            {"surface": "slack", "agent_id": "artemis", "channel_id": "C123"},
        )
        is False
    )


def test_is_personal_slack_dm_session_excludes_callie_dm() -> None:
    assert (
        is_personal_slack_dm_session(
            "slack-callie-T1-D123-_",
            {"surface": "slack", "agent_id": "callie", "channel_id": "D123"},
        )
        is False
    )


def test_resolve_surface_scope_personal_slack_dm_filters_marketing() -> None:
    all_surfaces = {"okr", "marketing-os", "signal-queue", "floating-artemis"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-artemis-T1-D123-_",
        metadata={"surface": "slack", "agent_id": "artemis", "channel_id": "D123"},
    )
    assert resolved == {"okr", "floating-artemis"}


def test_resolve_surface_scope_callie_dm_restricted_to_marketing() -> None:
    """Callie's DM resolves to only marketing surfaces (the allowlist intersection)."""
    all_surfaces = {"okr", "marketing-os", "signal-queue", "floating-artemis"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-callie-T1-D123-_",
        metadata={"surface": "slack", "agent_id": "callie", "channel_id": "D123"},
    )
    assert resolved == {"marketing-os", "signal-queue"}


def test_resolve_surface_scope_callie_excludes_non_marketing_surfaces() -> None:
    """Callie cannot see jira-board, calendar, okr, or dev-projects even if present."""
    extra_surfaces = {"jira-board", "calendar", "okr", "dev-projects"}
    all_surfaces = extra_surfaces | {"scouts", "signal-queue", "campaign-ops"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-callie-T123-C456-_",
        metadata={"surface": "slack", "agent_id": "callie", "channel_id": "C456"},
    )
    # None of the non-marketing extras appear
    assert resolved.isdisjoint(extra_surfaces)
    # Marketing surfaces that were present are included
    assert {"scouts", "signal-queue", "campaign-ops"}.issubset(resolved)


def test_resolve_surface_scope_callie_includes_all_marketing_surfaces_present() -> None:
    """Callie's resolved set contains every marketing surface present in all_surfaces."""
    all_surfaces = set(_MARKETING_SURFACES) | {"okr", "floating-artemis"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-callie-T1-C999-_",
        metadata={"surface": "slack", "agent_id": "callie", "channel_id": "C999"},
    )
    assert resolved == set(_MARKETING_SURFACES)


def test_resolve_surface_scope_callie_intersection_does_not_invent_surfaces() -> None:
    """If all_surfaces lacks a marketing surface, Callie's result doesn't invent it."""
    # all_surfaces has only a subset of marketing surfaces
    all_surfaces = {"scouts", "signal-queue"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-callie-T1-C999-_",
        metadata={"agent_id": "callie"},
    )
    assert resolved == {"scouts", "signal-queue"}


def test_resolve_surface_scope_unknown_agent_gets_full_surfaces() -> None:
    """An agent with no allowlist entry receives the full surface set unchanged."""
    all_surfaces = {"okr", "marketing-os", "jira-board", "dev-projects", "floating-artemis"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-unknown-T1-C123-_",
        metadata={"surface": "slack", "agent_id": "unknown-bot", "channel_id": "C123"},
    )
    assert resolved == all_surfaces


def test_resolve_surface_scope_artemis_non_dm_gets_full_surfaces() -> None:
    """Artemis in a non-DM channel (public/private channel) receives all surfaces."""
    all_surfaces = {"okr", "marketing-os", "signal-queue", "jira-board", "floating-artemis"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-artemis-T1-C123-_",
        metadata={"surface": "slack", "agent_id": "artemis", "channel_id": "C123"},
    )
    assert resolved == all_surfaces


def test_resolve_surface_scope_callie_channel_same_as_callie_dm() -> None:
    """Callie's allowlist applies regardless of channel type (DM or channel)."""
    all_surfaces = {"okr", "marketing-os", "scouts", "dev-projects"}
    for channel_id in ("D999", "C999"):
        resolved = resolve_surface_scope(
            all_surfaces=all_surfaces,
            session_id=f"slack-callie-T1-{channel_id}-_",
            metadata={"surface": "slack", "agent_id": "callie", "channel_id": channel_id},
        )
        assert "okr" not in resolved
        assert "dev-projects" not in resolved
        assert "marketing-os" in resolved
        assert "scouts" in resolved


def test_build_tool_registry_dm_scope_excludes_marketing_tools() -> None:
    reg = _build_tool_registry(available_surfaces={"okr", "floating-artemis"})
    assert "list_okr_objectives" in reg
    assert "list_signals" not in reg
    assert "approve_signal" not in reg
    assert "get_message_compass" not in reg
    assert "search_claims_register" not in reg
    assert "get_campaign_performance" not in reg
    assert "post_analyst_message" not in reg


def test_build_system_prompt_personal_slack_dm_has_no_unprompted_marketing_frame() -> None:
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=["okr"],
        session_id="slack-artemis-T1-D123-_",
        is_personal_slack_dm=True,
    )
    assert "This 1:1 Slack DM is for personal support, app/ops issues, and upgrades." in prompt
    assert "Do not volunteer marketing in this DM." in prompt
    assert "Callie's lane" in prompt


def test_parse_history_cutover_at_invalid_returns_none() -> None:
    assert parse_history_cutover_at({"history_cutover_at": "not-a-date"}) is None


@pytest.mark.asyncio
async def test_handle_turn_personal_slack_dm_scopes_prompt_tools_and_history() -> None:
    adapter = FakeAdapter([ScriptedReply(text="All good.")])
    captured_requests: list[object] = []

    original_complete = adapter.complete

    async def capturing_complete(req: object) -> object:
        captured_requests.append(req)
        return await original_complete(req)

    now = datetime(2026, 6, 10, 15, 0, 0, tzinfo=UTC)
    cutover = now - timedelta(minutes=1)
    new_row = SimpleNamespace(
        role="user",
        content=[{"type": "text", "text": "Hey Artemis"}],
    )

    async def fake_list_messages_for_context(
        _session: object,
        session_id: str,
        *,
        limit: int = 40,
        created_at_gte: datetime | None = None,
    ) -> list[object]:
        assert session_id == "slack-artemis-T1-D123-_"
        assert limit == 40
        assert created_at_gte == cutover
        return [new_row]

    session_row = MagicMock()
    session_row.metadata_ = {
        "surface": "slack",
        "agent_id": "artemis",
        "channel_id": "D123",
        "history_cutover_at": cutover.isoformat(),
        "retired_history_owner": "callie",
    }

    with (
        patch.object(adapter, "complete", side_effect=capturing_complete),
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=[]),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._get_recent_meeting_context", return_value=None),
        patch("artemis.floating_artemis.chat._persist_messages", new_callable=AsyncMock),
        patch("artemis.floating_artemis.chat._broadcast"),
        patch(
            "artemis.floating_artemis.chat.inject_memory_context", side_effect=lambda *a, **kw: a[0]
        ),
        patch("artemis.floating_artemis.chat.write_turn_drawer"),
        patch(
            "artemis.floating_artemis.chat.get_status",
            return_value={"available_surfaces": ["okr", "marketing-os", "signal-queue"]},
        ),
        patch("artemis.floating_artemis.repository.get_session_by_id", return_value=session_row),
        patch(
            "artemis.floating_artemis.repository.list_messages_for_context",
            side_effect=fake_list_messages_for_context,
        ),
    ):
        result = await handle_turn(
            session_id="slack-artemis-T1-D123-_",
            user_text="Hey Artemis",
            adapter=adapter,
        )

    assert result.response_text == "All good."
    assert len(captured_requests) == 1
    request = captured_requests[0]
    system = getattr(request, "system", "")
    tools = {tool.name for tool in getattr(request, "tools", [])}
    messages = getattr(request, "messages", [])

    assert "Do not volunteer marketing in this DM." in system
    assert "marketing-os" not in system
    assert "signal-queue" not in system
    assert "list_signals" not in tools
    assert any(
        isinstance(msg, Message)
        and any(
            isinstance(block, TextBlock) and block.text == "Hey Artemis" for block in msg.content
        )
        for msg in messages
    )
    assert not any(
        isinstance(msg, Message)
        and any(
            isinstance(block, TextBlock) and "Gate 1 marketing history" in block.text
            for block in msg.content
        )
        for msg in messages
    )
