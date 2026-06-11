from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from artemis.routes.integrations_slack_events import route_inbound
from artemis.writing_rules.agent_lint import lint_agent_text


class _SessionContext:
    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _SessionFactory:
    def __init__(self, *sessions: AsyncMock) -> None:
        self._sessions = list(sessions)

    def __call__(self) -> _SessionContext:
        return _SessionContext(self._sessions.pop(0))


def test_lint_replaces_em_dash_and_en_dash_variants() -> None:
    text = "Alpha — beta\nGamma—delta – epsilon\nZeta –"
    linted = lint_agent_text(text)
    assert linted == "Alpha, beta\nGamma, delta, epsilon\nZeta"
    assert "—" not in linted
    assert "–" not in linted


def test_lint_strips_emoji_and_tidies_whitespace() -> None:
    text = "Ready ✅ family 👨‍👩‍👧‍👦 hold ⏸️. Ship it."
    assert lint_agent_text(text) == "Ready family hold. Ship it."


def test_lint_preserves_code_blocks_inline_code_and_urls() -> None:
    text = (
        "Use `snake_case—fine`.\n"
        "```python\n"
        "value = 'keep—this'\n"
        "```\n"
        "See https://example.com/a-b ✅"
    )
    linted = lint_agent_text(text)
    assert "`snake_case—fine`" in linted
    assert "value = 'keep—this'" in linted
    assert "https://example.com/a-b" in linted
    assert linted.endswith("See https://example.com/a-b")


def test_lint_is_idempotent() -> None:
    text = "Status — complete ✅\nUse `foo—bar`"
    once = lint_agent_text(text)
    twice = lint_agent_text(once)
    assert twice == once


@pytest.mark.asyncio
async def test_route_inbound_lints_outbound_slack_post_only() -> None:
    first_session = AsyncMock()
    second_session = AsyncMock()
    turn_result = SimpleNamespace(
        response_text="Status — complete ✅. See `foo—bar` and https://example.com/a-b"
    )
    slack_client = AsyncMock()

    with (
        patch("artemis.db.SessionLocal", new=_SessionFactory(first_session, second_session)),
        patch(
            "artemis.routes.integrations_slack_events.repo.get_slack_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(session_id="slack-T1-C1-_"),
        ),
        patch(
            "artemis.floating_artemis.chat.handle_turn",
            new_callable=AsyncMock,
            return_value=turn_result,
        ),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                agent_id="artemis",
                signing_secret="",
                access_token="xoxb-test",
                bot_user_id="UBOT",
                authed_user_id="",
                allowed_user_ids=(),
                allowed_channel_ids=(),
                listen_channel_messages=False,
            ),
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=slack_client),
    ):
        await route_inbound(
            {
                "team_id": "T1",
                "channel": "C1",
                "text": "hi",
                "user": "U1",
            }
        )

    slack_client.post_message.assert_awaited_once_with(
        channel="C1",
        text="Status, complete. See `foo—bar` and https://example.com/a-b",
        thread_ts=None,
    )
    assert (
        turn_result.response_text
        == "Status — complete ✅. See `foo—bar` and https://example.com/a-b"
    )
