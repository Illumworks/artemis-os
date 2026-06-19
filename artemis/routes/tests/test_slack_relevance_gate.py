"""Unit tests for _needs_relevance_gate — pure helper, no DB required."""

from __future__ import annotations

from artemis.routes.integrations_slack_events import (
    _needs_relevance_gate,
    _SlackAgentConfig,
)


def _make_cfg(
    *,
    agent_id: str = "ares",
    listen_channel_messages: bool = True,
    always_respond_in_channels: bool = False,
    allowed_channel_ids: tuple[str, ...] = ("C0BBZCZA4EQ",),
) -> _SlackAgentConfig:
    return _SlackAgentConfig(
        agent_id=agent_id,
        signing_secret="secret",
        access_token="xoxb-token",
        bot_user_id="UBOT",
        authed_user_id="UAUTH",
        allowed_user_ids=(),
        allowed_channel_ids=allowed_channel_ids,
        listen_channel_messages=listen_channel_messages,
        always_respond_in_channels=always_respond_in_channels,
    )


def _call(
    cfg: _SlackAgentConfig,
    *,
    inner_type: str = "message",
    is_dm: bool = False,
    is_channel_join: bool = False,
    is_direct_mention: bool = False,
) -> bool:
    return _needs_relevance_gate(
        agent_cfg=cfg,
        inner_type=inner_type,
        is_dm=is_dm,
        is_channel_join=is_channel_join,
        is_direct_mention=is_direct_mention,
    )


def test_app_mention_bypasses_gate() -> None:
    """app_mention (is_direct_mention=True) never needs the gate."""
    cfg = _make_cfg(listen_channel_messages=True, always_respond_in_channels=False)
    assert _call(cfg, inner_type="app_mention", is_direct_mention=True) is False


def test_dm_bypasses_gate() -> None:
    """DMs never need the gate."""
    cfg = _make_cfg(listen_channel_messages=True, always_respond_in_channels=False)
    assert _call(cfg, is_dm=True) is False


def test_channel_join_bypasses_gate() -> None:
    """channel_join events bypass the gate regardless of other flags."""
    cfg = _make_cfg(listen_channel_messages=True, always_respond_in_channels=False)
    assert _call(cfg, is_channel_join=True) is False


def test_plain_channel_message_needs_gate() -> None:
    """A plain channel message with listen_channel_messages=True and
    always_respond_in_channels=False must pass the gate."""
    cfg = _make_cfg(listen_channel_messages=True, always_respond_in_channels=False)
    assert _call(cfg) is True


def test_always_respond_skips_gate() -> None:
    """always_respond_in_channels=True means no gate even for plain channel messages."""
    cfg = _make_cfg(listen_channel_messages=True, always_respond_in_channels=True)
    assert _call(cfg) is False


def test_listen_false_skips_gate() -> None:
    """When listen_channel_messages=False, gate is not applied (agent wouldn't
    see channel messages at all via _should_handle_event)."""
    cfg = _make_cfg(listen_channel_messages=False, always_respond_in_channels=False)
    assert _call(cfg) is False
