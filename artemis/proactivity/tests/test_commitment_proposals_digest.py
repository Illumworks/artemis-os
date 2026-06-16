"""Unit tests for the commitment proposals digest (Phase 1 surface layer).

Covers:
1. _render_proposals_digest: numbered list + commitment_map correctness.
2. send_commitment_proposals_digest dry_run=True: returns payload, no Slack send.
3. send_commitment_proposals_digest: skips when no proposed items.
4. send_commitment_proposals_digest: skips when live unanswered breadcrumb <24h.
5. try_apply_proposals_reply: no breadcrumb -> returns None.
6. try_apply_proposals_reply: track none -> complete crumb, no approvals.
7. try_apply_proposals_reply: track all -> approve all.
8. try_apply_proposals_reply: track 1,3 -> approve only those items.
9. try_apply_proposals_reply: track 1 3 (space-sep) -> approve those items.
10. try_apply_proposals_reply: unrelated text -> returns None (breadcrumb untouched).
11. Lint-clean output: no em-dashes, no emojis in digest text.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from artemis.proactivity.commitments import (
    ProposalsDigestSummary,
    _render_proposals_digest,
    send_commitment_proposals_digest,
    try_apply_proposals_reply,
)
from artemis.proactivity.models import Commitment, CommitmentProposalsBreadcrumb

_NOW = datetime(2026, 6, 15, 13, 0, tzinfo=UTC)
_DUE_1 = datetime(2026, 6, 20, 21, 0, tzinfo=UTC)
_DUE_2 = datetime(2026, 6, 25, 21, 0, tzinfo=UTC)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _make_commitment(
    *,
    id: int,
    text: str = "Test commitment",
    due: datetime | None = None,
    status: str = "proposed",
    sensitivity: str = "personal_ops",
    source_type: str = "granola_meeting",
    source_id: str = "g-1",
) -> Commitment:
    c = Commitment()
    c.id = id
    c.text = text
    c.due = due
    c.status = status
    c.sensitivity = sensitivity
    c.source_type = source_type
    c.source_id = source_id
    c.owner_user_id = 42
    c.created_at = _NOW
    c.updated_at = _NOW
    return c


def _make_breadcrumb(
    *,
    id: int = 1,
    recipient_id: str = "U12345",
    commitment_map: dict[str, int] | None = None,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> CommitmentProposalsBreadcrumb:
    crumb = CommitmentProposalsBreadcrumb()
    crumb.id = id
    crumb.recipient_id = recipient_id
    crumb.commitment_map = commitment_map or {"1": 101, "2": 102, "3": 103}
    crumb.proposal_text = "Test digest"
    crumb.created_at = created_at or _NOW
    crumb.expires_at = expires_at or (_NOW + timedelta(hours=48))
    crumb.completed_at = None
    return crumb


# ─── _render_proposals_digest ────────────────────────────────────────────────


def test_render_proposals_digest_numbered_list() -> None:
    """Digest renders a numbered list with due dates."""
    c1 = _make_commitment(id=101, text="Finish the report", due=_DUE_1)
    c2 = _make_commitment(id=102, text="Schedule the call", due=None)
    text, commitment_map = _render_proposals_digest([c1, c2], now=_NOW)

    assert "1." in text
    assert "Finish the report" in text
    assert "2026-06-20" in text  # due date of c1
    assert "2." in text
    assert "Schedule the call" in text
    assert commitment_map == {"1": 101, "2": 102}


def test_render_proposals_digest_includes_instructions() -> None:
    """Digest text includes the reply instructions."""
    c1 = _make_commitment(id=101, text="Do the thing", due=_DUE_1)
    text, _ = _render_proposals_digest([c1], now=_NOW)

    assert "track" in text.lower()
    assert "track all" in text.lower() or "track all" in text
    assert "track none" in text.lower() or "track none" in text


def test_render_proposals_digest_lint_clean() -> None:
    """Digest text must not contain em-dashes or emoji (lint invariant)."""
    c1 = _make_commitment(id=1, text="Review the doc -- urgent", due=_DUE_1)
    c2 = _make_commitment(id=2, text="Schedule sync", due=None)
    text, _ = _render_proposals_digest([c1, c2], now=_NOW)

    assert "—" not in text, f"Em-dash found in digest: {text!r}"
    assert "–" not in text, f"En-dash found in digest: {text!r}"
    # Basic emoji check: no characters in emoji ranges
    for char in text:
        cp = ord(char)
        assert cp < 0x1F300 or cp > 0x1FAFF, f"Emoji char {char!r} found in digest"


def test_render_proposals_digest_commitment_map_keys_are_strings() -> None:
    """commitment_map keys must be str (JSONB round-trip safety)."""
    commitments = [_make_commitment(id=i + 100) for i in range(3)]
    _, commitment_map = _render_proposals_digest(commitments, now=_NOW)
    for key in commitment_map:
        assert isinstance(key, str), f"Key {key!r} is not a string"


# ─── send_commitment_proposals_digest ────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_returns_payload_without_sending() -> None:
    """dry_run=True returns the text and skips Slack send + breadcrumb write."""
    c1 = _make_commitment(id=101, text="Do the thing", due=_DUE_1)
    session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.proactivity.commitments.repo.list_proposed_commitments",
            new=AsyncMock(return_value=[c1]),
        ),
        patch(
            "artemis.proactivity.commitments._resolve_artemis_dm_recipient",
            new=AsyncMock(return_value="U12345"),
        ),
        patch(
            "artemis.proactivity.commitments._get_slack_token_for_agent",
            new=AsyncMock(return_value="xoxb-test"),
        ) as mock_slack_token,
        patch(
            "artemis.proactivity.commitments.repo.create_commitment_proposals_breadcrumb",
            new=AsyncMock(),
        ) as mock_crumb,
    ):
        summary = await send_commitment_proposals_digest(session, now=_NOW, dry_run=True)

    assert isinstance(summary, ProposalsDigestSummary)
    assert summary.dry_run is True
    assert summary.sent is False
    assert summary.proposed_count == 1
    assert "Do the thing" in summary.payload
    # No actual Slack send or breadcrumb write
    mock_slack_token.assert_not_awaited()
    mock_crumb.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_proposed_commitments_returns_unsent() -> None:
    """Nothing to send when there are no proposed commitments."""
    session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.proactivity.commitments.repo.list_proposed_commitments",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "artemis.proactivity.commitments._resolve_artemis_dm_recipient",
            new=AsyncMock(return_value="U12345"),
        ),
    ):
        summary = await send_commitment_proposals_digest(session, now=_NOW)

    assert summary.sent is False
    assert summary.proposed_count == 0


@pytest.mark.asyncio
async def test_unanswered_crumb_less_than_24h_skips() -> None:
    """Skip if unanswered breadcrumb was created less than 24h ago."""
    crumb = _make_breadcrumb(created_at=_NOW - timedelta(hours=10))
    session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.commitments._resolve_artemis_dm_recipient",
            new=AsyncMock(return_value="U12345"),
        ),
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.list_proposed_commitments",
            new=AsyncMock(return_value=[]),
        ) as mock_list,
    ):
        summary = await send_commitment_proposals_digest(session, now=_NOW)

    assert summary.sent is False
    assert summary.proposed_count == 0
    # list_proposed_commitments should not even be called — we bailed early
    mock_list.assert_not_awaited()


# ─── try_apply_proposals_reply ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_live_breadcrumb_returns_none() -> None:
    """No live breadcrumb -> return None (not a digest reply)."""
    session = AsyncMock()
    with patch(
        "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
        new=AsyncMock(return_value=None),
    ):
        result = await try_apply_proposals_reply(
            session, text="track 1", slack_user_id="U12345"
        )
    assert result is None


@pytest.mark.asyncio
async def test_track_none_completes_crumb_no_approvals() -> None:
    """track none -> breadcrumb completed, nothing approved."""
    crumb = _make_breadcrumb()
    session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ) as mock_complete,
        patch(
            "artemis.proactivity.commitments.approve_commitment",
            new=AsyncMock(),
        ) as mock_approve,
    ):
        result = await try_apply_proposals_reply(
            session, text="track none", slack_user_id="U12345"
        )

    assert result is not None
    assert "leave" in result.lower() or "left" in result.lower() or "for now" in result.lower()
    mock_complete.assert_awaited_once_with(session, crumb.id)
    mock_approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_completes_crumb_no_approvals() -> None:
    """skip -> same as track none."""
    crumb = _make_breadcrumb()
    session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ) as mock_complete,
        patch(
            "artemis.proactivity.commitments.approve_commitment",
            new=AsyncMock(),
        ) as mock_approve,
    ):
        result = await try_apply_proposals_reply(
            session, text="skip", slack_user_id="U12345"
        )

    assert result is not None
    mock_complete.assert_awaited_once_with(session, crumb.id)
    mock_approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_track_all_approves_all() -> None:
    """track all -> approves every commitment in the breadcrumb map."""
    crumb = _make_breadcrumb(commitment_map={"1": 101, "2": 102})
    session = AsyncMock()

    approved: list[int] = []

    async def fake_approve(sess: Any, commitment_id: int) -> MagicMock:
        approved.append(commitment_id)
        c = _make_commitment(id=commitment_id, status="active")
        return c

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ),
        patch(
            "artemis.proactivity.commitments.approve_commitment",
            new=fake_approve,
        ),
    ):
        result = await try_apply_proposals_reply(
            session, text="track all", slack_user_id="U12345"
        )

    assert result is not None
    assert "2" in result  # "Tracking 2 commitments"
    assert sorted(approved) == [101, 102]


@pytest.mark.asyncio
async def test_track_nums_comma_separated_approves_selected() -> None:
    """track 1,3 -> approves items 1 and 3 only."""
    crumb = _make_breadcrumb(commitment_map={"1": 101, "2": 102, "3": 103})
    session = AsyncMock()

    approved: list[int] = []

    async def fake_approve(sess: Any, commitment_id: int) -> MagicMock:
        approved.append(commitment_id)
        return _make_commitment(id=commitment_id, status="active")

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ),
        patch(
            "artemis.proactivity.commitments.approve_commitment",
            new=fake_approve,
        ),
    ):
        result = await try_apply_proposals_reply(
            session, text="track 1,3", slack_user_id="U12345"
        )

    assert result is not None
    assert sorted(approved) == [101, 103]
    assert "2" in result  # "Tracking 2 commitments"
    # Left count
    assert "1" in result  # "The other 1"


@pytest.mark.asyncio
async def test_track_nums_space_separated_approves_selected() -> None:
    """track 1 3 (space-separated) -> same as track 1,3."""
    crumb = _make_breadcrumb(commitment_map={"1": 101, "2": 102, "3": 103})
    session = AsyncMock()

    approved: list[int] = []

    async def fake_approve(sess: Any, commitment_id: int) -> MagicMock:
        approved.append(commitment_id)
        return _make_commitment(id=commitment_id, status="active")

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ),
        patch(
            "artemis.proactivity.commitments.approve_commitment",
            new=fake_approve,
        ),
    ):
        result = await try_apply_proposals_reply(
            session, text="track 1 3", slack_user_id="U12345"
        )

    assert result is not None
    assert sorted(approved) == [101, 103]


@pytest.mark.asyncio
async def test_unrelated_text_returns_none_breadcrumb_untouched() -> None:
    """Text that doesn't match any digest pattern -> None, breadcrumb stays live."""
    crumb = _make_breadcrumb()
    session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ) as mock_complete,
    ):
        result = await try_apply_proposals_reply(
            session, text="how is the weather today?", slack_user_id="U12345"
        )

    assert result is None
    mock_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_track_single_item_singular_grammar() -> None:
    """Tracking 1 item uses singular 'commitment' not 'commitments'."""
    crumb = _make_breadcrumb(commitment_map={"1": 101})
    session = AsyncMock()

    async def fake_approve(sess: Any, commitment_id: int) -> MagicMock:
        return _make_commitment(id=commitment_id, status="active")

    with (
        patch(
            "artemis.proactivity.commitments.repo.get_live_proposals_breadcrumb",
            new=AsyncMock(return_value=crumb),
        ),
        patch(
            "artemis.proactivity.commitments.repo.complete_proposals_breadcrumb",
            new=AsyncMock(),
        ),
        patch(
            "artemis.proactivity.commitments.approve_commitment",
            new=fake_approve,
        ),
    ):
        result = await try_apply_proposals_reply(
            session, text="track all", slack_user_id="U12345"
        )

    assert result is not None
    assert "1 commitment" in result  # singular
