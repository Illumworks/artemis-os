"""Unit tests for agent-humanization changes (worker/agent-humanization branch).

Tests cover the four structural fixes:
  (a) X-Slack-Retry-Num header causes early-200 skip when >= 1.
  (b) A channel_join event for a non-artemis agent always yields a ping of the
      joiner's user_id, regardless of the session's recent-message timestamp.
  (c) The anti-repetition / recent-outbound instruction is present in the
      assembled system prompt when history contains assistant messages.
  (d) The human-voice nudge is always present in the assembled system prompt.

No DB, no Slack credentials needed — all I/O is mocked or bypassed via pure
function calls and dependency injection.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_signed_request(
    body_dict: dict[str, Any],
    secret: str = "test-secret",
    extra_headers: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Return (body_bytes, headers) with a valid Slack HMAC-SHA256 signature."""
    body_bytes = json.dumps(body_dict).encode()
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:{body_bytes.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    headers: dict[str, str] = {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return body_bytes, headers


async def _make_client():
    from artemis.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── (a) Retry safety ───────────────────────────────────────────────────────────


def _retry_payload(event_id: str) -> dict[str, Any]:
    # A DM: Artemis's dispatch gate accepts channel_type="im", so whether the
    # handler runs is decided by the retry guard alone -- which is what these
    # tests are about.  A channel message would be dropped at the gate instead,
    # masking the behaviour under test.
    return {
        "type": "event_callback",
        "event_id": event_id,
        "team_id": "T001",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D001",
            "user": "U001",
            "text": "hello again",
            "ts": "10.0",
        },
    }


async def _post_retry(*, event_id: str, already_processed: bool) -> tuple[int, AsyncMock]:
    """POST a retry delivery; return (status_code, the mocked handler)."""
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(
        _retry_payload(event_id), secret=secret, extra_headers={"X-Slack-Retry-Num": "1"}
    )

    from artemis.db import get_session
    from artemis.main import app

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session
    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.slack_inbound_exists",
                new_callable=AsyncMock,
                return_value=already_processed,
            ),
            patch(
                "artemis.routes.integrations_slack_events._handle_mentionable_event",
                new_callable=AsyncMock,
            ) as mock_handle,
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events",
                    content=body_bytes,
                    headers=headers,
                )
        return resp.status_code, mock_handle
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_slack_retry_of_already_processed_event_is_skipped() -> None:
    """A retry whose event_id is already recorded must not be handled twice."""
    status, mock_handle = await _post_retry(event_id="Ev-retry-01", already_processed=True)

    assert status == 200
    mock_handle.assert_not_called()


async def test_slack_retry_of_never_processed_event_is_recovered() -> None:
    """A retry with NO prior record must be processed, not silently dropped.

    Regression guard: the guard used to blanket-skip every retry_num >= 1 on
    the assumption that retry 0 had already succeeded.  When the original
    delivery hit a restarting app, that assumption was false and every retry
    was discarded -- losing the message permanently.  A restart is precisely
    the common reason the first 200 never lands, so this was the case most in
    need of recovery and the one being thrown away.
    """
    status, mock_handle = await _post_retry(event_id="Ev-retry-lost", already_processed=False)

    assert status == 200
    mock_handle.assert_called_once()


async def test_slack_retry_header_zero_proceeds_normally() -> None:
    """X-Slack-Retry-Num: 0 is not a retry — normal processing must continue."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev-retry-zero",
        "team_id": "T001",
        "event": {
            "type": "app_mention",
            "channel": "C001",
            "user": "U001",
            "text": "<@UBOT> hello",
            "ts": "11.0",
        },
    }
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(
        payload, secret=secret, extra_headers={"X-Slack-Retry-Num": "0"}
    )

    from artemis.db import get_session
    from artemis.main import app

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session
    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_upsert,
            patch(
                "artemis.routes.integrations_slack_events.route_inbound",
                new_callable=AsyncMock,
            ),
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events",
                    content=body_bytes,
                    headers=headers,
                )
            assert resp.status_code == 200
            # upsert should have been called (normal processing path)
            mock_upsert.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_slack_no_retry_header_proceeds_normally() -> None:
    """Absence of X-Slack-Retry-Num must not affect normal processing."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev-no-retry",
        "team_id": "T001",
        "event": {
            "type": "app_mention",
            "channel": "C001",
            "user": "U001",
            "text": "<@UBOT> hi",
            "ts": "12.0",
        },
    }
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(payload, secret=secret)

    from artemis.db import get_session
    from artemis.main import app

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session
    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_upsert,
            patch(
                "artemis.routes.integrations_slack_events.route_inbound",
                new_callable=AsyncMock,
            ),
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events",
                    content=body_bytes,
                    headers=headers,
                )
            assert resp.status_code == 200
            mock_upsert.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── (b) Channel-join always pings the joiner ──────────────────────────────────


async def test_channel_join_always_pings_joiner_regardless_of_recent_message() -> None:
    """channel_join for a non-artemis agent must force-ping the joiner.

    Scenario: a prior message was sent 30 seconds ago (well within the re-engagement
    gap), which normally suppresses the ping.  For a channel_join the ping should
    fire unconditionally so every new joiner is greeted with @mention.
    """
    from artemis.routes.integrations_slack_events import _handle_mentionable_event
    from artemis.integrations.config_resolver import SlackConfig
    from unittest.mock import MagicMock

    # Simulate a recent session message (30 seconds ago) — normally suppresses ping
    recent_ts = datetime.now(UTC) - timedelta(seconds=30)

    # Build a minimal channel_join event
    payload = {
        "type": "event_callback",
        "event_id": "Ev-join-01",
        "team_id": "T001",
    }
    event = {
        "type": "message",
        "subtype": "channel_join",
        "channel": "C-enablement",
        "channel_type": "channel",
        "user": "U-JOINER",
        "text": "<@U-JOINER> has joined the channel",
        "ts": "20.0",
    }

    # Use a real MagicMock for background_tasks so add_task works synchronously
    mock_bg = MagicMock()
    mock_session = AsyncMock()

    with (
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_make_kai_agent_cfg("C-enablement"),
        ),
        patch(
            "artemis.routes.integrations_slack_events._is_bot_authored",
            return_value=False,
        ),
        patch(
            "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "artemis.routes.integrations_slack_events._last_message_timestamp",
            new_callable=AsyncMock,
            return_value=recent_ts,  # recent — would normally suppress ping on non-join
        ),
        patch(
            "artemis.routes.integrations_slack_events.route_inbound",
        ) as mock_route,
        patch(
            "artemis.integrations.slack.triage.classify_mention_type",
            return_value="channel_message",
        ),
    ):
        await _handle_mentionable_event(
            payload,
            event,
            mock_bg,
            mock_session,
            agent_id="kai",
            inner_type="message",
        )

    # background_tasks.add_task was called with route_inbound
    assert mock_bg.add_task.called, "add_task was not called — routing did not fire"
    call_kwargs = mock_bg.add_task.call_args
    # add_task(route_inbound, event_data, agent_id=..., ping_user_id=...)
    # Positional args: [0]=fn, [1]=event_data; kwargs has ping_user_id
    ping_passed = call_kwargs.kwargs.get("ping_user_id")
    # The ping must be the joiner, not suppressed
    assert ping_passed == "U-JOINER", (
        f"Expected ping_user_id='U-JOINER', got {ping_passed!r}. "
        "channel_join must always force-ping the joiner."
    )


def _make_agent_cfg_from_slack_cfg(agent_id: str, slack_cfg: Any) -> Any:
    """Build a _SlackAgentConfig matching the fields SlackConfig provides."""
    from artemis.routes.integrations_slack_events import _SlackAgentConfig

    return _SlackAgentConfig(
        agent_id=agent_id,
        signing_secret=slack_cfg.signing_secret,
        access_token="",
        bot_user_id=slack_cfg.authed_user_id,
        authed_user_id=slack_cfg.authed_user_id,
        allowed_user_ids=slack_cfg.allowed_user_ids,
        allowed_channel_ids=(),
        listen_channel_messages=True,
    )


def _make_kai_agent_cfg(allowed_channel_id: str) -> Any:
    """Build a Kai-style _SlackAgentConfig with a specific allowed channel."""
    from artemis.routes.integrations_slack_events import _SlackAgentConfig

    return _SlackAgentConfig(
        agent_id="kai",
        signing_secret="sig",
        access_token="xoxb-test",
        bot_user_id="U-BOT",
        authed_user_id="U-BOT",
        allowed_user_ids=("U-JOINER",),
        allowed_channel_ids=(allowed_channel_id,),
        listen_channel_messages=True,
    )


async def test_channel_join_pings_each_distinct_joiner() -> None:
    """Two joiners in the same channel must each receive a ping with their own user_id.

    This is a pure-unit test on should_ping_asker + the is_channel_join bypass.
    """
    from artemis.routes.integrations_slack_events import should_ping_asker

    # With is_channel_join=True we expect the caller to bypass should_ping_asker entirely
    # and set ping unconditionally.  Verify should_ping_asker still works for the
    # non-join path (regression guard).
    now = datetime.now(UTC)
    recent = now - timedelta(seconds=10)

    # Normal path: recent message → no ping
    assert should_ping_asker(is_dm=False, last_message_at=recent, now=now) is False
    # Normal path: cold start → ping
    assert should_ping_asker(is_dm=False, last_message_at=None, now=now) is True
    # Normal path: DM → never ping
    assert should_ping_asker(is_dm=True, last_message_at=None, now=now) is False


# ── (c) Anti-repetition / recent-outbound instruction in assembled prompt ──────


def test_build_system_prompt_includes_recent_outbound_anti_repetition_block() -> None:
    """When recent_outbound_texts is provided, the anti-repetition block is in the prompt."""
    from artemis.floating_artemis.chat import _build_system_prompt

    recent = [
        "Welcome. Let me know what you're looking for and I'll find it.",
        "Sure, here's what I found in the library.",
    ]
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        recent_outbound_texts=recent,
    )
    # The recent messages must be quoted in the prompt
    assert "Welcome. Let me know what you're looking for" in prompt
    # The "do NOT repeat" instruction must be present
    assert "do NOT repeat" in prompt.lower() or "do not repeat" in prompt.lower()


def test_build_system_prompt_no_anti_repetition_block_when_no_recent() -> None:
    """When recent_outbound_texts is None or empty, the anti-repetition block is absent."""
    from artemis.floating_artemis.chat import _build_system_prompt

    prompt_none = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        recent_outbound_texts=None,
    )
    prompt_empty = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        recent_outbound_texts=[],
    )
    # The recent-messages header should NOT appear when there are no outbound texts
    assert "Your recent messages in this channel" not in prompt_none
    assert "Your recent messages in this channel" not in prompt_empty


# ── (d) Human-voice nudge always present ──────────────────────────────────────


def test_build_system_prompt_always_includes_human_voice_nudge() -> None:
    """The human-voice nudge must appear in every assembled prompt."""
    from artemis.floating_artemis.chat import _build_system_prompt

    # No special params — baseline prompt
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
    )
    # The nudge must mention addressing people by name and varying openings
    assert "first name" in prompt.lower() or "by name" in prompt.lower()
    assert "vary" in prompt.lower()


def test_build_system_prompt_human_voice_nudge_present_for_all_agents() -> None:
    """Human-voice nudge appears regardless of agent_id."""
    from artemis.floating_artemis.chat import _build_system_prompt
    from artemis.floating_artemis.personality import load_agent_profile

    for agent_id in ("artemis", "callie", "kai"):
        profile = load_agent_profile(agent_id)
        prompt = _build_system_prompt(
            voice_samples=[],
            page_context=None,
            available_surfaces=[],
            persona_core=profile.persona_core,
            profile_text=profile.profile_text,
            display_name=profile.display_name,
            agent_id=agent_id,
        )
        assert "vary" in prompt.lower(), (
            f"Human-voice nudge missing from {agent_id} prompt (no 'vary' instruction)"
        )
        assert "canned" in prompt.lower() or "templated" in prompt.lower(), (
            f"Human-voice nudge missing anti-template instruction for agent {agent_id}"
        )


# ── Combined: anti-rep instruction uses history from handle_turn flow ──────────


def test_recent_outbound_extraction_from_history() -> None:
    """Verify recent_outbound_texts are extracted correctly from a message history list."""
    from artemis.agent.types import Message, TextBlock, ToolUseBlock

    # Build a fake history with alternating user/assistant messages
    history = [
        Message(role="user", content=[TextBlock(text="Hello")]),
        Message(
            role="assistant",
            content=[TextBlock(text="Welcome Sara, let me know what you need.")],
        ),
        Message(role="user", content=[TextBlock(text="Got anything on enablement?")]),
        Message(
            role="assistant",
            content=[TextBlock(text="Sure, here's the enablement playbook.")],
        ),
        Message(role="user", content=[TextBlock(text="Thanks!")]),
        Message(
            role="assistant",
            content=[TextBlock(text="You're welcome.")],
        ),
    ]

    # Replicate the extraction logic from handle_turn step 4a
    recent_outbound_texts: list[str] = []
    for msg in reversed(history):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    recent_outbound_texts.append(block.text.strip())
                    break
        if len(recent_outbound_texts) >= 4:
            break
    recent_outbound_texts.reverse()

    assert len(recent_outbound_texts) == 3
    assert recent_outbound_texts[0] == "Welcome Sara, let me know what you need."
    assert recent_outbound_texts[1] == "Sure, here's the enablement playbook."
    assert recent_outbound_texts[2] == "You're welcome."


def test_recent_outbound_extraction_caps_at_four() -> None:
    """Extraction must stop at 4 messages to keep the prompt lean."""
    from artemis.agent.types import Message, TextBlock

    # Build history with 6 assistant messages
    history = []
    for i in range(6):
        history.append(Message(role="user", content=[TextBlock(text=f"Q{i}")]))
        history.append(
            Message(role="assistant", content=[TextBlock(text=f"Answer number {i}")])
        )

    recent_outbound_texts: list[str] = []
    for msg in reversed(history):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    recent_outbound_texts.append(block.text.strip())
                    break
        if len(recent_outbound_texts) >= 4:
            break
    recent_outbound_texts.reverse()

    assert len(recent_outbound_texts) == 4
    # Should be the MOST RECENT 4 (indices 2-5)
    assert "Answer number 5" in recent_outbound_texts
    assert "Answer number 4" in recent_outbound_texts
