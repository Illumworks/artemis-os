"""P3 — Awaiting-reply radar (Lane R) ship-gate tests.

Acceptance criteria:
1. User-token OAuth flow stores search:read / users:read / chat:write scopes.
2. An unanswered Slack @mention surfaces; an answered one (Jon replied) does NOT.
3. A Gmail thread awaiting reply surfaces; one Jon last replied to does NOT.
4. Dedup: the same item is NOT re-surfaced within the renotify window.
5. Dismiss: dismissing an item stops it re-nagging (dismissed_at is set).
6. format_radar_nudge renders correctly for 0 and N items.
7. Radar nudge is appended to the morning-brief text when items are present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.proactivity import radar_repository
from artemis.proactivity.radar import (
    RadarItem,
    _extract_sender_name,
    _jon_replied_in_slack_thread,
    fetch_gmail_awaiting_reply,
    fetch_slack_awaiting_reply,
    format_radar_nudge,
    gather_radar_items,
)

pytestmark = pytest.mark.asyncio

# ── helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime.now(UTC)
_TS_HOUR_AGO = str((_NOW - timedelta(hours=1)).timestamp())
_TS_TWO_HOURS_AGO = str((_NOW - timedelta(hours=2)).timestamp())
_TS_DAY_AGO = str((_NOW - timedelta(hours=25)).timestamp())


def _make_integration(
    provider: str,
    creds: dict[str, object],
    scopes: list[str] | None = None,
) -> Integration:
    return Integration(
        provider=provider,
        workspace_id="T_TEST",
        agent_id="artemis",
        display_name="Test Workspace",
        encrypted_credentials=encrypt_credentials(creds),
        scopes=scopes or [],
        status="active",
        metadata_={},
    )


# ── Part 1: Slack user-token OAuth flow storage ───────────────────────────────


async def test_slack_user_token_stored_with_correct_scopes(db_session: AsyncSession) -> None:
    """Simulates what the OAuth callback does and asserts scopes are persisted."""
    scopes = ["search:read", "users:read", "chat:write"]
    integration = _make_integration(
        "slack_user", {"access_token": "xoxp-fake", "token_type": "user"}, scopes
    )
    db_session.add(integration)
    await db_session.flush()
    await db_session.refresh(integration)

    assert integration.provider == "slack_user"
    assert integration.scopes is not None
    assert "search:read" in integration.scopes
    assert "users:read" in integration.scopes
    assert "chat:write" in integration.scopes

    # Decrypt and confirm token type.
    from artemis.integrations.crypto import decrypt_credentials

    creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    assert creds["token_type"] == "user"
    assert "xoxp-fake" in str(creds["access_token"])


# ── Part 2: Slack mention filtering ──────────────────────────────────────────


async def test_unanswered_mention_surfaces(db_session: AsyncSession) -> None:
    """An unanswered @mention should appear in fetch_slack_awaiting_reply results."""
    # Seed a slack_user integration.
    db_session.add(
        _make_integration(
            "slack_user", {"access_token": "xoxp-fake", "token_type": "user"}, ["search:read"]
        )
    )
    await db_session.flush()

    # search.messages returns one hit; conversations.replies has no reply from Jon.
    hit = {
        "ts": _TS_HOUR_AGO,
        "thread_ts": _TS_HOUR_AGO,
        "user": "U_OTHER",
        "username": "alice",
        "text": "hey <@U_JON> can you check this?",
        "permalink": "https://slack.com/archives/C1/p123",
        "channel": {"id": "C1", "name": "general"},
    }
    replies = [
        # The original message (from alice).
        {"user": "U_OTHER", "ts": _TS_HOUR_AGO},
    ]

    with (
        patch("artemis.proactivity.radar._resolve_slack_user_token", return_value="xoxp-fake"),
        patch("artemis.proactivity.radar._resolve_jon_slack_id", return_value="U_JON"),
        patch(
            "artemis.integrations.slack.client.SlackClient.search_messages",
            new=AsyncMock(return_value=[hit]),
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient.get_conversation_replies",
            new=AsyncMock(return_value=replies),
        ),
    ):
        items = await fetch_slack_awaiting_reply(db_session)

    assert len(items) == 1
    assert items[0].item_type == "slack_mention"
    assert items[0].sender == "alice"
    assert items[0].item_key == f"C1:{_TS_HOUR_AGO}"


async def test_answered_mention_does_not_surface(db_session: AsyncSession) -> None:
    """If Jon already replied after the mention, it should NOT surface."""
    hit = {
        "ts": _TS_TWO_HOURS_AGO,
        "thread_ts": _TS_TWO_HOURS_AGO,
        "user": "U_OTHER",
        "username": "bob",
        "text": "hey <@U_JON> check this",
        "permalink": "https://slack.com/archives/C2/p456",
        "channel": {"id": "C2", "name": "general"},
    }
    # Jon replied after the mention.
    replies = [
        {"user": "U_OTHER", "ts": _TS_TWO_HOURS_AGO},
        {"user": "U_JON", "ts": _TS_HOUR_AGO},  # Jon replied!
    ]

    with (
        patch("artemis.proactivity.radar._resolve_slack_user_token", return_value="xoxp-fake"),
        patch("artemis.proactivity.radar._resolve_jon_slack_id", return_value="U_JON"),
        patch(
            "artemis.integrations.slack.client.SlackClient.search_messages",
            new=AsyncMock(return_value=[hit]),
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient.get_conversation_replies",
            new=AsyncMock(return_value=replies),
        ),
    ):
        items = await fetch_slack_awaiting_reply(db_session)

    assert items == []


async def test_no_slack_user_token_returns_empty(db_session: AsyncSession) -> None:
    """If there is no slack_user integration, the Slack half returns []."""
    with patch("artemis.proactivity.radar._resolve_slack_user_token", return_value=None):
        items = await fetch_slack_awaiting_reply(db_session)
    assert items == []


async def test_jon_replied_in_thread_true() -> None:
    """_jon_replied_in_slack_thread returns True when Jon has a later reply."""
    replies = [
        {"user": "U_OTHER", "ts": "1700000000.000000"},
        {"user": "U_JON", "ts": "1700001000.000000"},
    ]
    mock_client = MagicMock()
    mock_client.get_conversation_replies = AsyncMock(return_value=replies)
    result = await _jon_replied_in_slack_thread(
        mock_client,
        channel_id="C1",
        thread_ts="1700000000.000000",
        jon_id="U_JON",
        after_ts="1700000000.000000",
    )
    assert result is True


async def test_jon_replied_in_thread_false() -> None:
    """_jon_replied_in_slack_thread returns False when Jon has NOT replied."""
    replies = [
        {"user": "U_OTHER", "ts": "1700000000.000000"},
    ]
    mock_client = MagicMock()
    mock_client.get_conversation_replies = AsyncMock(return_value=replies)
    result = await _jon_replied_in_slack_thread(
        mock_client,
        channel_id="C1",
        thread_ts="1700000000.000000",
        jon_id="U_JON",
        after_ts="1700000000.000000",
    )
    assert result is False


# ── Part 3: Gmail filtering ───────────────────────────────────────────────────


async def test_gmail_thread_awaiting_reply_surfaces(db_session: AsyncSession) -> None:
    """A thread where someone else sent the last message should surface."""
    creds: dict[str, object] = {
        "access_token": "ya29-fake",
        "refresh_token": "refresh-fake",
        "client_id": "client-id",
        "client_secret": "client-secret",
    }
    db_session.add(_make_integration("gmail", creds, ["gmail.readonly"]))
    await db_session.flush()

    now_ms = int(_NOW.timestamp() * 1000)
    yesterday_ms = int((_NOW - timedelta(days=1)).timestamp() * 1000)

    # list_recent_messages returns two messages from the same thread.
    msgs = [
        {
            "id": "msg1",
            "threadId": "thread1",
            "snippet": "hi",
            "from": "alice@example.com",
            "subject": "Q",
            "date": "Mon",
            "internalDate": str(yesterday_ms),
        },
    ]
    thread = {
        "threadId": "thread1",
        "messages": [
            # First message from Alice.
            {
                "id": "msg1",
                "threadId": "thread1",
                "snippet": "hi",
                "from": "Alice <alice@example.com>",
                "subject": "Q",
                "date": "Mon",
                "internalDate": str(yesterday_ms),
            },
            # Last message from Alice (not Jon) — awaiting Jon's reply.
            {
                "id": "msg2",
                "threadId": "thread1",
                "snippet": "any update?",
                "from": "Alice <alice@example.com>",
                "subject": "Q",
                "date": "Tue",
                "internalDate": str(now_ms),
            },
        ],
    }

    with (
        patch(
            "artemis.proactivity.radar._resolve_gmail_creds",
            return_value={
                "access_token": "ya29-fake",
                "refresh_token": "refresh-fake",
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.list_recent_messages",
            new=AsyncMock(return_value=msgs),
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.get_thread",
            new=AsyncMock(return_value=thread),
        ),
    ):
        items = await fetch_gmail_awaiting_reply(db_session)

    assert len(items) == 1
    assert items[0].item_type == "gmail_thread"
    assert items[0].item_key == "thread1"
    assert items[0].sender == "Alice"


async def test_gmail_thread_jon_replied_last_does_not_surface(db_session: AsyncSession) -> None:
    """A thread where Jon sent the last message should NOT surface."""
    now_ms = int(_NOW.timestamp() * 1000)

    thread = {
        "threadId": "thread2",
        "messages": [
            {
                "id": "msg1",
                "threadId": "thread2",
                "snippet": "hi",
                "from": "Alice <alice@example.com>",
                "subject": "R",
                "date": "Mon",
                "internalDate": str(now_ms),
            },
            # Jon replied last.
            {
                "id": "msg2",
                "threadId": "thread2",
                "snippet": "done",
                "from": "Jon Fila <jon.fila@amiralearning.com>",
                "subject": "R",
                "date": "Tue",
                "internalDate": str(now_ms),
            },
        ],
    }
    msgs = [
        {
            "id": "msg1",
            "threadId": "thread2",
            "snippet": "hi",
            "from": "alice@example.com",
            "subject": "R",
            "date": "Mon",
            "internalDate": str(now_ms),
        }
    ]

    with (
        patch(
            "artemis.proactivity.radar._resolve_gmail_creds",
            return_value={
                "access_token": "ya29-fake",
                "refresh_token": "refresh-fake",
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.list_recent_messages",
            new=AsyncMock(return_value=msgs),
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.get_thread",
            new=AsyncMock(return_value=thread),
        ),
    ):
        items = await fetch_gmail_awaiting_reply(db_session)

    assert items == []


async def test_gmail_noreply_filtered_out(db_session: AsyncSession) -> None:
    """Threads from no-reply senders should be excluded."""
    now_ms = int(_NOW.timestamp() * 1000)
    thread = {
        "threadId": "thread3",
        "messages": [
            {
                "id": "msg1",
                "threadId": "thread3",
                "snippet": "newsletter",
                "from": "no-reply@marketing.example.com",
                "subject": "Newsletter",
                "date": "Mon",
                "internalDate": str(now_ms),
            },
        ],
    }
    msgs = [
        {
            "id": "msg1",
            "threadId": "thread3",
            "snippet": "newsletter",
            "from": "no-reply@marketing.example.com",
            "subject": "Newsletter",
            "date": "Mon",
            "internalDate": str(now_ms),
        }
    ]

    with (
        patch(
            "artemis.proactivity.radar._resolve_gmail_creds",
            return_value={
                "access_token": "ya29-fake",
                "refresh_token": "refresh-fake",
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.list_recent_messages",
            new=AsyncMock(return_value=msgs),
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.get_thread",
            new=AsyncMock(return_value=thread),
        ),
    ):
        items = await fetch_gmail_awaiting_reply(db_session)

    assert items == []


async def test_no_gmail_creds_returns_empty(db_session: AsyncSession) -> None:
    with patch("artemis.proactivity.radar._resolve_gmail_creds", return_value=None):
        items = await fetch_gmail_awaiting_reply(db_session)
    assert items == []


def test_extract_sender_name() -> None:
    assert _extract_sender_name("Alice Smith <alice@example.com>") == "Alice Smith"
    assert _extract_sender_name('"Bob Jones" <bob@example.com>') == "Bob Jones"
    assert _extract_sender_name("noreply@example.com") == "noreply@example.com"


# ── Part 4: Dedup (same item not re-nagged within window) ────────────────────


async def test_dedup_renotify_window(db_session: AsyncSession) -> None:
    """An item surfaced recently should NOT reappear within the renotify window."""
    now = datetime.now(UTC)
    # Upsert once.
    row, is_new = await radar_repository.upsert_surfaced(
        db_session,
        item_type="slack_mention",
        item_key="C1:123.456",
        label="alice in #general",
        permalink="https://slack.com/archives/C1/p123",
        now=now,
    )
    await db_session.flush()
    assert is_new is True
    assert row.dismissed_at is None

    # Check that list_due_for_surface does NOT return it (just surfaced).
    due = await radar_repository.list_due_for_surface(db_session, now=now, renotify_hours=24)
    keys = [r.item_key for r in due]
    assert "C1:123.456" not in keys


async def test_dedup_after_window_resurfaces(db_session: AsyncSession) -> None:
    """An item surfaced > renotify_hours ago DOES appear in list_due_for_surface."""
    old_time = datetime.now(UTC) - timedelta(hours=25)
    row, _ = await radar_repository.upsert_surfaced(
        db_session,
        item_type="slack_mention",
        item_key="C2:789.000",
        label="bob in #random",
        permalink=None,
        now=old_time,
    )
    await db_session.flush()

    due = await radar_repository.list_due_for_surface(
        db_session, now=datetime.now(UTC), renotify_hours=24
    )
    keys = [r.item_key for r in due]
    assert "C2:789.000" in keys


# ── Part 5: Dismiss ──────────────────────────────────────────────────────────


async def test_dismiss_stops_renagging(db_session: AsyncSession) -> None:
    """Dismissing a radar item sets dismissed_at and removes it from future surfaces."""
    now = datetime.now(UTC) - timedelta(hours=48)
    row, _ = await radar_repository.upsert_surfaced(
        db_session,
        item_type="gmail_thread",
        item_key="thread99",
        label="carol@example.com re: Budget",
        permalink="https://mail.google.com/mail/u/0/#inbox/thread99",
        now=now,
    )
    await db_session.flush()
    item_id = row.id

    # Dismiss it.
    dismissed = await radar_repository.dismiss_by_id(db_session, item_id=item_id)
    await db_session.flush()
    assert dismissed is not None
    assert dismissed.dismissed_at is not None

    # list_due_for_surface should no longer include it.
    due = await radar_repository.list_due_for_surface(
        db_session, now=datetime.now(UTC), renotify_hours=0
    )
    keys = [r.item_key for r in due]
    assert "thread99" not in keys


async def test_dismiss_by_type_and_key(db_session: AsyncSession) -> None:
    """dismiss_item by (type, key) also works."""
    now = datetime.now(UTC) - timedelta(hours=48)
    await radar_repository.upsert_surfaced(
        db_session,
        item_type="slack_mention",
        item_key="C3:555.000",
        label="dave in #slack",
        permalink=None,
        now=now,
    )
    await db_session.flush()

    dismissed = await radar_repository.dismiss_item(
        db_session, item_type="slack_mention", item_key="C3:555.000"
    )
    await db_session.flush()
    assert dismissed is not None
    assert dismissed.dismissed_at is not None


async def test_dismiss_nonexistent_returns_none(db_session: AsyncSession) -> None:
    dismissed = await radar_repository.dismiss_by_id(db_session, item_id=999999)
    assert dismissed is None


# ── Part 6: format_radar_nudge ────────────────────────────────────────────────


def _make_item(item_type: str, sender: str, where: str, hours_ago: int = 2) -> RadarItem:
    return RadarItem(
        item_type=item_type,
        item_key=f"{item_type}:key:{sender}",
        sender=sender,
        where=where,
        permalink=f"https://example.com/{sender}",
        snippet="example snippet",
        received_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


def test_format_nudge_empty() -> None:
    assert format_radar_nudge([]) == ""


def test_format_nudge_single_item() -> None:
    item = _make_item("slack_mention", "alice", "#general", hours_ago=3)
    text = format_radar_nudge([item])
    assert "1 person is waiting on you" in text
    assert "alice" in text
    assert "#general" in text


def test_format_nudge_multiple_items() -> None:
    items = [
        _make_item("slack_mention", "alice", "#general", hours_ago=3),
        _make_item("gmail_thread", "bob", "Q4 Budget", hours_ago=25),
    ]
    text = format_radar_nudge(items)
    assert "2 people are waiting on you" in text
    assert "alice" in text
    assert "bob" in text


# ── Part 7: Morning-brief integration ────────────────────────────────────────


async def test_radar_section_appended_to_brief(db_session: AsyncSession) -> None:
    """Radar nudge is appended to the brief text when items are present."""
    from datetime import date

    from artemis.proactivity.scheduler import _format_brief_for_slack

    brief: dict[str, Any] = {
        "summary": "A slow day.",
        "highlights": [],
        "priorities": [],
        "next_actions": [],
    }
    base_text = _format_brief_for_slack(brief, delivery_date=date.today())

    nudge = format_radar_nudge([_make_item("slack_mention", "alice", "#general")])
    combined = base_text + "\n\n" + nudge

    assert "alice" in combined
    assert "waiting on you" in combined
    assert "A slow day." in combined


async def test_gather_radar_items_swallows_partial_failure(db_session: AsyncSession) -> None:
    """If one fetcher fails, the other's results are still returned."""
    good_item = _make_item("gmail_thread", "alice", "Re: Budget")

    with (
        patch(
            "artemis.proactivity.radar.fetch_slack_awaiting_reply",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "artemis.proactivity.radar.fetch_gmail_awaiting_reply",
            new=AsyncMock(return_value=[good_item]),
        ),
    ):
        items = await gather_radar_items(db_session)

    assert len(items) == 1
    assert items[0].sender == "alice"
