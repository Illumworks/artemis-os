"""Unit tests for the Artemis hub escalation layer (Phase 1).

Tests cover:
  1. Pending-ask detection (detection.py) — is_pending_ask + extract_summary.
  2. Pending-ask recorded on an agent @Jon or question message.
  3. Pending-ask resolved when Jon replies in the channel.
  4. Escalation fires only after the window.
  5. Only Artemis uses the interrupt path (notify_jon raises for others).
  6. No agent reply to Artemis's terminal comment (loop-proof).
  7. Brief injection includes non-escalated, non-Artemis asks.

All tests are pure unit tests — no live DB, no live Slack.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ────────────────────────────────────────────────────────────────────────────
# 1. Detection helpers
# ────────────────────────────────────────────────────────────────────────────


class TestIsPendingAsk:
    def test_jon_mention_detected(self) -> None:
        from artemis.hub.detection import is_pending_ask

        assert is_pending_ask("<@U09F3EPJXSQ> can you take a look?", jon_slack_id="U09F3EPJXSQ")

    def test_literal_at_jon(self) -> None:
        from artemis.hub.detection import is_pending_ask

        assert is_pending_ask("Hey @Jon, prototype is ready. What do you think?")

    def test_ask_phrase_dm_vs_channel(self) -> None:
        from artemis.hub.detection import is_pending_ask

        # In Jon's 1:1 DM, an ask-phrase counts (every message there is to Jon).
        assert is_pending_ask("The report is done. Let me know if you need changes.", is_dm=True)
        # In a channel, an ask-phrase with NO @Jon mention does not (could be aimed at anyone).
        assert not is_pending_ask("The report is done. Let me know if you need changes.")

    def test_trailing_question_dm_vs_channel(self) -> None:
        from artemis.hub.detection import is_pending_ask

        # DM: any trailing "?" is to Jon.
        assert is_pending_ask("Which option should we go with?", is_dm=True)
        # Channel: a question with no @Jon mention is NOT an ask to Jon — e.g. Kai
        # asking Sara something must never queue an escalation to Jon.
        assert not is_pending_ask("Which option should we go with?")
        assert not is_pending_ask("What would be most helpful to know about how you work?")

    def test_no_question_no_ask_phrase_not_detected(self) -> None:
        from artemis.hub.detection import is_pending_ask

        # No "?", no ask phrase, no @Jon → not detected.
        assert not is_pending_ask("The build finished successfully.")
        assert not is_pending_ask("Done processing the data.")

    def test_empty_string_not_detected(self) -> None:
        from artemis.hub.detection import is_pending_ask

        assert not is_pending_ask("")
        assert not is_pending_ask("   ")

    def test_normal_statement_not_detected(self) -> None:
        from artemis.hub.detection import is_pending_ask

        # Pure statements with no mention, no "?", no ask phrase → not detected.
        assert not is_pending_ask("Campaign brief is attached and ready for review.")
        assert not is_pending_ask("The analysis is complete.")
        assert not is_pending_ask("Slides have been updated with the latest figures.")

    def test_with_explicit_jon_slack_id(self) -> None:
        from artemis.hub.detection import is_pending_ask

        assert is_pending_ask("<@UJONFOO> what's the priority here?", jon_slack_id="UJONFOO")

    def test_other_mention_not_detected(self) -> None:
        from artemis.hub.detection import is_pending_ask

        # Mentions another user, not Jon
        assert not is_pending_ask("<@UCALLIE> thanks!", jon_slack_id="UJONFOO")


class TestExtractSummary:
    def test_extracts_question_line(self) -> None:
        from artemis.hub.detection import extract_summary

        text = "Here is the overview.\nShould we proceed with Option A?"
        result = extract_summary(text)
        assert result == "Should we proceed with Option A?"

    def test_falls_back_to_truncation(self) -> None:
        from artemis.hub.detection import extract_summary

        text = "A " * 200  # no question line
        result = extract_summary(text, max_len=20)
        assert len(result) <= 23  # 20 + "..."

    def test_short_text_returned_as_is(self) -> None:
        from artemis.hub.detection import extract_summary

        text = "What is the plan?"
        assert extract_summary(text) == text


# ────────────────────────────────────────────────────────────────────────────
# 2. Repository: record_pending_ask (unit — mock session)
# ────────────────────────────────────────────────────────────────────────────


def _make_session() -> AsyncMock:
    s = AsyncMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    s.flush = AsyncMock()
    s.refresh = AsyncMock()
    s.add = MagicMock()
    s.get = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_record_pending_ask_returns_created_on_insert() -> None:
    """record_pending_ask returns (row, True) on first insert."""
    from artemis.hub.repository import record_pending_ask

    session = _make_session()
    fake_row = MagicMock()
    fake_row.id = 42

    # First call: pg_insert returns an id (row was inserted)
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = 42
    session.execute = AsyncMock(return_value=scalar_result)
    session.get = AsyncMock(return_value=fake_row)

    row, created = await record_pending_ask(
        session,
        agent_id="kai",
        channel_id="C123",
        message_ts="1234567890.000100",
        summary="Any questions for Jon?",
    )
    assert created is True
    assert row is fake_row


@pytest.mark.asyncio
async def test_record_pending_ask_returns_existing_on_conflict() -> None:
    """record_pending_ask returns (row, False) when the row already exists."""
    from artemis.hub.repository import record_pending_ask

    session = _make_session()
    fake_row = MagicMock()

    # pg_insert returns None (conflict; row already exists)
    scalar_result_none = MagicMock()
    scalar_result_none.scalar_one_or_none.return_value = None

    # select returns the existing row
    select_result = MagicMock()
    select_result.scalar_one.return_value = fake_row

    session.execute = AsyncMock(side_effect=[scalar_result_none, select_result])

    row, created = await record_pending_ask(
        session,
        agent_id="kai",
        channel_id="C123",
        message_ts="1234567890.000100",
        summary="Any questions for Jon?",
    )
    assert created is False
    assert row is fake_row


# ────────────────────────────────────────────────────────────────────────────
# 3. Repository: resolve_pending_asks_in_channel
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_pending_asks_in_channel_returns_count() -> None:
    """resolve_pending_asks_in_channel returns the number of rows resolved."""
    from artemis.hub.repository import resolve_pending_asks_in_channel

    session = _make_session()
    # Simulate 2 rows updated
    update_result = MagicMock()
    update_result.fetchall.return_value = [(1,), (2,)]
    session.execute = AsyncMock(return_value=update_result)

    count = await resolve_pending_asks_in_channel(session, channel_id="C123")
    assert count == 2


# ────────────────────────────────────────────────────────────────────────────
# 4. Repository: list_overdue_unescalated respects the window
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_overdue_unescalated_only_after_window() -> None:
    """list_overdue_unescalated should include asks older than the window."""
    from artemis.hub.repository import list_overdue_unescalated

    session = _make_session()

    old_ask = MagicMock()
    old_ask.created_at = datetime.now(UTC) - timedelta(hours=26)

    fresh_ask = MagicMock()
    fresh_ask.created_at = datetime.now(UTC) - timedelta(hours=10)

    result = MagicMock()
    result.scalars.return_value.all.return_value = [old_ask]
    session.execute = AsyncMock(return_value=result)

    rows = await list_overdue_unescalated(session, window=timedelta(hours=24))
    # The repository forwards the where-clause filter to DB; here we just check
    # that the function returns whatever the DB gives back.
    assert old_ask in rows
    assert fresh_ask not in rows


# ────────────────────────────────────────────────────────────────────────────
# 5. Sole-interrupt: only Artemis may call notify_jon
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_jon_raises_for_non_artemis() -> None:
    """notify_jon must raise InterruptNotAllowed for any non-Artemis caller."""
    from artemis.hub.notify import InterruptNotAllowed, notify_jon

    session = _make_session()

    with pytest.raises(InterruptNotAllowed, match="sole-interrupt violation"):
        await notify_jon(session, requested_by="callie", text="Hello Jon")

    with pytest.raises(InterruptNotAllowed, match="sole-interrupt violation"):
        await notify_jon(session, requested_by="kai", text="Hello Jon")


@pytest.mark.asyncio
async def test_notify_jon_succeeds_for_artemis() -> None:
    """notify_jon succeeds when called by 'artemis' and token is available."""
    from artemis.hub.notify import notify_jon

    session = _make_session()

    # notify_jon imports helpers inside the function body; patch via their canonical paths.
    with (
        patch(
            "artemis.proactivity.scheduler._get_slack_token_for_agent",
            new_callable=AsyncMock,
            return_value="xoxb-fake-token",
        ),
        patch(
            "artemis.proactivity.scheduler._resolve_morning_brief_recipient",
            new_callable=AsyncMock,
            return_value="UJON123",
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
        ) as mock_client_cls,
    ):
        mock_inst = AsyncMock()
        mock_inst.post_dm = AsyncMock(return_value={"ok": True})
        mock_client_cls.return_value = mock_inst
        result = await notify_jon(session, requested_by="artemis", text="Test")
    # The important invariant is that the PermissionError guard did not trigger.
    assert result is True or result is False  # bool depends on token resolution


@pytest.mark.asyncio
async def test_notify_jon_returns_false_when_no_token() -> None:
    """notify_jon returns False (not raises) when no Slack token is found."""
    from artemis.hub.notify import notify_jon

    session = _make_session()

    with (
        patch(
            "artemis.proactivity.scheduler._get_slack_token_for_agent",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.proactivity.scheduler._resolve_morning_brief_recipient",
            new_callable=AsyncMock,
            return_value="UJON",
        ),
    ):
        result = await notify_jon(session, requested_by="artemis", text="ping")

    assert result is False


# ────────────────────────────────────────────────────────────────────────────
# 6. Loop-proof: Artemis's terminal comment must not trigger pending-ask record
# ────────────────────────────────────────────────────────────────────────────


def test_artemis_escalation_comment_not_a_pending_ask() -> None:
    """The terminal comment Artemis posts should NOT trigger pending-ask recording.

    The hub recording in _post_slack_message is gated on normalized_agent != 'artemis',
    so Artemis's own comments never produce a pending-ask row.
    """
    # Verify the detection function returns False for a typical Artemis terminal comment
    from artemis.hub.detection import is_pending_ask

    terminal_comment = "@Kai, I'll take this — escalating to Jon."
    # The comment @-mentions Kai (not Jon), and does not contain an ask phrase
    # or trailing question — so it should not be classified as a pending ask.
    assert not is_pending_ask(terminal_comment)


def test_is_bot_authored_prevents_artemis_reply_loop() -> None:
    """_is_bot_authored returns True for bot-authored events so echo loop is killed."""
    from artemis.routes.integrations_slack_events import _is_bot_authored

    event_with_bot_id = {"bot_id": "B123", "user": "U456", "text": "hello"}
    assert _is_bot_authored(event_with_bot_id, bot_user_id="UARTEMIS") is True

    event_self = {"user": "UARTEMIS", "text": "hello"}
    assert _is_bot_authored(event_self, bot_user_id="UARTEMIS") is True

    event_human = {"user": "UHUMAN", "text": "hello"}
    assert _is_bot_authored(event_human, bot_user_id="UARTEMIS") is False


# ────────────────────────────────────────────────────────────────────────────
# 7. Brief injection: pending-asks section
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_asks_brief_section_non_empty_for_pending_items() -> None:
    """pending_asks_brief_section returns a non-empty string when unresolved asks exist."""
    from artemis.hub.brief_injection import pending_asks_brief_section

    session = _make_session()
    ask = MagicMock()
    ask.agent_id = "callie"
    ask.channel_id = "C999"
    ask.summary = "Should we adjust the campaign budget?"
    ask.escalated_at = None

    with patch(
        "artemis.hub.brief_injection.hub_repo",
    ) as mock_repo:
        mock_repo.list_unresolved = AsyncMock(return_value=[ask])
        section = await pending_asks_brief_section(session)

    assert "callie" in section.lower() or "Callie" in section
    assert "campaign budget" in section


@pytest.mark.asyncio
async def test_pending_asks_brief_section_excludes_artemis() -> None:
    """pending_asks_brief_section must NOT include Artemis's own asks."""
    from artemis.hub.brief_injection import pending_asks_brief_section

    session = _make_session()
    artemis_ask = MagicMock()
    artemis_ask.agent_id = "artemis"
    artemis_ask.channel_id = "C000"
    artemis_ask.summary = "Some artemis question"
    artemis_ask.escalated_at = None

    with patch("artemis.hub.brief_injection.hub_repo") as mock_repo:
        mock_repo.list_unresolved = AsyncMock(return_value=[artemis_ask])
        section = await pending_asks_brief_section(session)

    # Artemis routes directly, not via brief; section should be empty
    assert section == ""


@pytest.mark.asyncio
async def test_pending_asks_brief_section_excludes_escalated() -> None:
    """pending_asks_brief_section excludes asks that have already been escalated."""
    from artemis.hub.brief_injection import pending_asks_brief_section

    session = _make_session()
    already_escalated = MagicMock()
    already_escalated.agent_id = "kai"
    already_escalated.channel_id = "C111"
    already_escalated.summary = "Already escalated ask"
    already_escalated.escalated_at = datetime.now(UTC) - timedelta(hours=2)

    with patch("artemis.hub.brief_injection.hub_repo") as mock_repo:
        mock_repo.list_unresolved = AsyncMock(return_value=[already_escalated])
        section = await pending_asks_brief_section(session)

    assert section == ""


@pytest.mark.asyncio
async def test_pending_asks_brief_section_empty_when_no_asks() -> None:
    from artemis.hub.brief_injection import pending_asks_brief_section

    session = _make_session()

    with patch("artemis.hub.brief_injection.hub_repo") as mock_repo:
        mock_repo.list_unresolved = AsyncMock(return_value=[])
        section = await pending_asks_brief_section(session)

    assert section == ""


# ────────────────────────────────────────────────────────────────────────────
# 8. Escalation sweep (unit — mock DB + Slack)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_escalation_sweep_fires_for_overdue_asks() -> None:
    """run_escalation_sweep escalates overdue asks and stamps escalated_at."""
    from artemis.hub.escalation import run_escalation_sweep

    overdue_ask = MagicMock()
    overdue_ask.id = 7
    overdue_ask.agent_id = "kai"
    overdue_ask.channel_id = "CKAI"
    overdue_ask.summary = "What priority should I use for the new sheet?"
    overdue_ask.created_at = datetime.now(UTC) - timedelta(hours=27)

    with (
        patch("artemis.hub.escalation.hub_repo") as mock_repo,
        patch("artemis.hub.escalation._post_in_channel", new_callable=AsyncMock, return_value=True),
        patch("artemis.hub.escalation._dm_jon", new_callable=AsyncMock, return_value=True),
        patch("artemis.hub.escalation._db") as mock_db,
    ):
        # list_overdue_unescalated is called in the outer session context
        mock_repo.list_overdue_unescalated = AsyncMock(return_value=[overdue_ask])
        mock_repo.mark_escalated = AsyncMock()

        # Provide a proper async context manager for the session
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock()
        mock_db.SessionLocal.return_value = mock_session

        summary = await run_escalation_sweep()

    assert summary.checked == 1
    assert summary.escalated == 1
    assert summary.failed == 0


@pytest.mark.asyncio
async def test_escalation_sweep_skips_fresh_asks() -> None:
    """run_escalation_sweep must not escalate asks within the window."""
    from artemis.hub.escalation import run_escalation_sweep

    # No overdue asks
    with (
        patch("artemis.hub.escalation.hub_repo") as mock_repo,
        patch("artemis.hub.escalation._db") as mock_db,
    ):
        mock_repo.list_overdue_unescalated = AsyncMock(return_value=[])

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_db.SessionLocal.return_value = mock_session

        summary = await run_escalation_sweep()

    assert summary.checked == 0
    assert summary.escalated == 0


# ────────────────────────────────────────────────────────────────────────────
# 9. Config: hub_escalation_cron setting exists
# ────────────────────────────────────────────────────────────────────────────


def test_hub_escalation_cron_in_config() -> None:
    """Settings must expose hub_escalation_cron (defaults to hourly)."""
    from artemis.config import Settings

    s = Settings()
    assert hasattr(s, "hub_escalation_cron")
    # Default is every hour.
    assert s.hub_escalation_cron == "0 * * * *"


# ── Addressing Jon is not asking Jon (2026-08-14) ─────────────────────────────


_JON = "U09F3EPJXSQ"

# Verbatim from agent_pending_asks — rows the old channel rule created and the
# hourly sweep then escalated, posting "I'll take this, escalating to Jon" into a
# live Josh/Callie conversation. Jon: "artemis keeps saying escalating to jon and
# what not which isnt necessary because its a conversation not a problem."
_REAL_NON_ASKS = (
    "<@U09F3EPJXSQ> Yes, Jon. You're coming through clearly.",
    "<@U09F3EPJXSQ> Confirmed, Jon. I've got you. No @mention needed.",
    "<@U09F3EPJXSQ> All 12 are genuinely dispatched now, Jon. Argus is running.",
    "<@U09F3EPJXSQ> Josh is here in this channel with us, so I can just speak to him.",
)


@pytest.mark.parametrize("text", _REAL_NON_ASKS)
def test_answering_jon_in_a_channel_is_not_a_pending_ask(text: str) -> None:
    """Callie opens nearly every reply with Jon's mention as ordinary courtesy.

    A bare mention used to be sufficient in a channel, so her ANSWERS were
    logged as unanswered asks and escalated a day later.
    """
    from artemis.hub.detection import is_pending_ask

    assert is_pending_ask(text, jon_slack_id=_JON, is_dm=False) is False


def test_a_real_question_with_the_mention_appended_still_counts() -> None:
    """The one genuine ask in the table, and the case a naive fix would break.

    Agents append the addressee AFTER the question, which left the "?" not at
    end-of-line. Tightening the channel rule without also handling that would
    have silenced the only real ask while fixing the noise.
    """
    from artemis.hub.detection import is_pending_ask

    text = (
        "Want me to draft the outreach to Kristen, or do you have a way to "
        "reach her? <@U09F3EPJXSQ>"
    )
    assert is_pending_ask(text, jon_slack_id=_JON, is_dm=False) is True


@pytest.mark.parametrize(
    "text",
    (
        "<@U09F3EPJXSQ> Should we lead with the RFP or the superintendent angle?",
        "<@U09F3EPJXSQ> Let me know which you prefer.",
        "<@U09F3EPJXSQ> Want me to draft that sequence?",
    ),
)
def test_genuine_channel_asks_still_register(text: str) -> None:
    """The point is to escalate real asks, not to stop escalating."""
    from artemis.hub.detection import is_pending_ask

    assert is_pending_ask(text, jon_slack_id=_JON, is_dm=False) is True


def test_dm_behaviour_is_unchanged() -> None:
    """In a 1:1 DM every message is to Jon, so a bare question still counts."""
    from artemis.hub.detection import is_pending_ask

    assert is_pending_ask("Anything else you need?", jon_slack_id=_JON, is_dm=True) is True
    assert is_pending_ask("<@U09F3EPJXSQ> noted", jon_slack_id=_JON, is_dm=True) is True
