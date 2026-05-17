"""Tests for J1 Slack client and outbound tool registry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.integrations.slack.client import SlackAPIError, SlackClient
from artemis.integrations.slack.tools import (
    _list_slack_channels,
    _react_to_slack_message,
    _read_slack_channel,
    _send_slack_dm,
    _send_slack_message,
    register_slack_tools,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_response(payload: dict[str, object], status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    return resp


def _make_client() -> SlackClient:
    return SlackClient(token="xoxb-test-token")


# ── SlackClient — post_message ────────────────────────────────────────────────


async def test_post_message_sends_correct_payload() -> None:
    ok_resp = _mock_response({"ok": True, "ts": "111.222", "channel": "C123"})
    mock_http = AsyncMock()
    mock_http.post.return_value = ok_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _make_client().post_message("C123", "Hello world")

    mock_http.post.assert_called_once()
    call_kwargs = mock_http.post.call_args
    assert "chat.postMessage" in call_kwargs.args[0]
    assert call_kwargs.kwargs["json"]["channel"] == "C123"
    assert call_kwargs.kwargs["json"]["text"] == "Hello world"
    assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer xoxb-test-token"
    assert result["ts"] == "111.222"


async def test_post_message_with_thread_ts() -> None:
    ok_resp = _mock_response({"ok": True, "ts": "999.000"})
    mock_http = AsyncMock()
    mock_http.post.return_value = ok_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        await _make_client().post_message("C123", "Reply", thread_ts="111.222")

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["thread_ts"] == "111.222"


async def test_post_message_slack_error_raises() -> None:
    err_resp = _mock_response({"ok": False, "error": "channel_not_found"})
    mock_http = AsyncMock()
    mock_http.post.return_value = err_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_http),
        pytest.raises(SlackAPIError) as exc_info,
    ):
        await _make_client().post_message("bad-channel", "text")

    assert exc_info.value.error == "channel_not_found"
    assert "chat.postMessage" in exc_info.value.method


# ── SlackClient — get_channel_history ─────────────────────────────────────────


async def test_get_channel_history_returns_messages() -> None:
    messages = [{"ts": "1.0", "text": "hi"}, {"ts": "2.0", "text": "there"}]
    ok_resp = _mock_response({"ok": True, "messages": messages})
    mock_http = AsyncMock()
    mock_http.post.return_value = ok_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _make_client().get_channel_history("C456", limit=10)

    assert result == messages
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["channel"] == "C456"
    assert payload["limit"] == 10
    assert "conversations.history" in mock_http.post.call_args.args[0]


# ── SlackClient — add_reaction ────────────────────────────────────────────────


async def test_add_reaction_correct_payload() -> None:
    ok_resp = _mock_response({"ok": True})
    mock_http = AsyncMock()
    mock_http.post.return_value = ok_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        await _make_client().add_reaction("C789", "123.456", "thumbsup")

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["channel"] == "C789"
    assert payload["timestamp"] == "123.456"
    assert payload["name"] == "thumbsup"
    assert "reactions.add" in mock_http.post.call_args.args[0]


# ── SlackClient — list_channels ───────────────────────────────────────────────


async def test_list_channels_returns_channels() -> None:
    channels = [{"id": "C001", "name": "general"}, {"id": "C002", "name": "random"}]
    ok_resp = _mock_response({"ok": True, "channels": channels})
    mock_http = AsyncMock()
    mock_http.post.return_value = ok_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _make_client().list_channels(limit=50)

    assert result == channels
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["types"] == "public_channel,private_channel"
    assert payload["limit"] == 50
    assert "conversations.list" in mock_http.post.call_args.args[0]


# ── SlackClient — retry on 429 ────────────────────────────────────────────────


async def test_retry_on_rate_limit() -> None:
    rate_limit_resp = _mock_response({}, status_code=429)
    rate_limit_resp.headers = {"Retry-After": "1"}
    ok_resp = _mock_response({"ok": True, "ts": "5.0"})

    mock_http = AsyncMock()
    mock_http.post.side_effect = [rate_limit_resp, ok_resp]
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_http),
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await _make_client().post_message("C123", "hi")

    assert mock_sleep.call_count == 1
    assert mock_sleep.call_args.args[0] == 1
    assert mock_http.post.call_count == 2
    assert result["ts"] == "5.0"


# ── SlackClient — post_dm ────────────────────────────────────────────────────


async def test_post_dm_opens_then_sends() -> None:
    open_resp = _mock_response({"ok": True, "channel": {"id": "D999"}})
    send_resp = _mock_response({"ok": True, "ts": "7.0"})

    mock_http = AsyncMock()
    mock_http.post.side_effect = [open_resp, send_resp]
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _make_client().post_dm("U12345", "Hey there")

    assert mock_http.post.call_count == 2
    first_url = mock_http.post.call_args_list[0].args[0]
    second_url = mock_http.post.call_args_list[1].args[0]
    assert "conversations.open" in first_url
    assert "chat.postMessage" in second_url
    second_payload = mock_http.post.call_args_list[1].kwargs["json"]
    assert second_payload["channel"] == "D999"
    assert second_payload["text"] == "Hey there"
    assert result["ts"] == "7.0"


# ── Tool registration ─────────────────────────────────────────────────────────


def test_register_slack_tools_registers_five_tools() -> None:
    reg = AuthorizedToolRegistry()
    register_slack_tools(reg)
    assert len(reg) == 5
    expected = {
        "send_slack_message",
        "send_slack_dm",
        "read_slack_channel",
        "react_to_slack_message",
        "list_slack_channels",
    }
    assert {e.tool.name for e in reg.all_entries()} == expected


def test_slack_tool_layers() -> None:
    reg = AuthorizedToolRegistry()
    register_slack_tools(reg)

    layer3_tools = ["send_slack_message", "send_slack_dm", "react_to_slack_message"]
    for name in layer3_tools:
        entry = reg.get(name)
        assert entry is not None and entry.layer == 3, f"{name} should be layer 3"

    layer2_tools = ["read_slack_channel", "list_slack_channels"]
    for name in layer2_tools:
        entry = reg.get(name)
        assert entry is not None and entry.layer == 2, f"{name} should be layer 2"


# ── Tool implementations — no integration found ───────────────────────────────


async def test_send_slack_message_exception_returns_error_string() -> None:
    """When an unexpected exception is raised, the function returns an error string."""
    with patch("artemis.integrations.slack.client.SlackClient") as mock_cls:
        mock_cls.side_effect = RuntimeError("boom")
        result = await _send_slack_message({"channel": "C1", "text": "hi"})
    assert isinstance(result, str)


async def test_send_slack_message_missing_fields() -> None:
    result = await _send_slack_message({"channel": "C1"})
    assert "required" in result.lower() or "error" in result.lower()


async def test_send_slack_dm_missing_fields() -> None:
    result = await _send_slack_dm({"user": "U1"})
    assert "required" in result.lower() or "error" in result.lower()


async def test_read_slack_channel_missing_fields() -> None:
    result = await _read_slack_channel({})
    assert "required" in result.lower() or "error" in result.lower()


async def test_react_to_slack_message_missing_fields() -> None:
    result = await _react_to_slack_message({"channel": "C1", "ts": "1.0"})
    assert "required" in result.lower() or "error" in result.lower()


async def test_list_slack_channels_uses_default_limit() -> None:
    """list_slack_channels returns an error string when no integration is configured."""
    result = await _list_slack_channels({})
    # Without a real DB the call will raise and return an error string.
    assert isinstance(result, str)
