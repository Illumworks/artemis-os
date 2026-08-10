"""Tests for the P2 Friday OKR check-in flow.

Three coverage areas:
  1. update_okr_kr is registered at layer 3 (requires confirmation, never auto-invoked).
  2. Friday job: reserves once-per-week (idempotent), posts a proposal, writes NO OKR.
  3. Proposal generator: a KR with no grounding source produces no proposed change.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.okr import register_okr_tools
from artemis.integrations import repository as integration_repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.okr import (
    repository as okr_repo,  # noqa: F401 — imported to make the patch target resolvable
)
from artemis.proactivity.models import MorningBriefDelivery
from artemis.proactivity.okr_checkin import (
    build_okr_checkin_proposal,
    format_checkin_for_slack,
)
from artemis.proactivity.scheduler import (
    _fire_okr_checkin,
    _register_okr_checkin_job,
    get_proactivity_scheduler,
    stop_proactivity_scheduler,
)

pytestmark = pytest.mark.asyncio

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def reset_scheduler() -> AsyncGenerator[None, None]:
    yield
    stop_proactivity_scheduler()


async def _seed_slack_context(db_session: AsyncSession) -> None:
    db_session.add(
        Integration(
            provider="slack",
            workspace_id="default",
            agent_id="artemis",
            encrypted_credentials=encrypt_credentials({"access_token": "xoxb-artemis"}),
            connected_at=datetime.now(UTC),
            status="active",
        )
    )
    await integration_repo.upsert_provider_config(
        db_session,
        "slack",
        {"authed_user_id": "U_JON"},
    )
    await db_session.commit()


# ── 1. Layer-3 gate on update_okr_kr ─────────────────────────────────────────


def test_update_okr_kr_is_layer3_requires_confirmation() -> None:
    """update_okr_kr must be layer 3: requires confirmation, never auto-invoked."""
    registry = AuthorizedToolRegistry()
    register_okr_tools(registry)

    # Confirm registration exists.
    assert "update_okr_kr" in registry

    entry = registry.get("update_okr_kr")
    assert entry is not None
    assert entry.layer == 3, f"Expected layer 3, got {entry.layer}"

    # Layer 3 must not be auto-invoked.
    assert not registry.is_auto_invoke("update_okr_kr"), (
        "update_okr_kr must NOT be auto-invoked (layer 3)"
    )
    # Layer 3 must require confirmation.
    assert registry.requires_confirmation("update_okr_kr"), (
        "update_okr_kr must require confirmation (layer 3)"
    )


def test_list_okr_objectives_is_still_layer1() -> None:
    """list_okr_objectives remains layer 1 (read-only, auto-invoke)."""
    registry = AuthorizedToolRegistry()
    register_okr_tools(registry)
    assert registry.is_auto_invoke("list_okr_objectives")
    assert not registry.requires_confirmation("list_okr_objectives")


# ── 2. Friday job: idempotent, no OKR write ───────────────────────────────────


async def test_fire_okr_checkin_delivers_proposal_once(db_session: AsyncSession) -> None:
    """The Friday job reserves once per week, posts a proposal, and writes no OKR."""
    await _seed_slack_context(db_session)

    okr_update_calls: list[Any] = []

    async def _spy_update_key_result(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
        okr_update_calls.append((args, kwargs))

    # Empty sources — proposal will be empty but the delivery should still fire.
    with (
        patch(
            "artemis.proactivity.scheduler.gather_checkin_sources",
            new_callable=AsyncMock,
            return_value={
                "objectives": [],
                "activity": [],
                "jira_done": [],
                "action_items": [],
            },
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_post_dm,
        # Spy on the OKR repo's update_key_result to prove it is never called.
        patch(
            "artemis.okr.repository.update_key_result",
            side_effect=_spy_update_key_result,
        ),
    ):
        # Fire twice — second should be idempotent (no DM sent again).
        await _fire_okr_checkin()
        await _fire_okr_checkin()

    # Only one DM sent.
    assert mock_post_dm.await_count == 1

    # No OKR update called by the Friday job.
    assert okr_update_calls == [], (
        f"update_key_result must NOT be called by the Friday job itself; got {okr_update_calls}"
    )

    # Exactly one reservation row.
    result = await db_session.execute(
        select(MorningBriefDelivery).where(MorningBriefDelivery.delivery_kind == "okr_checkin")
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].recipient_id == "U_JON"


async def test_fire_okr_checkin_proposal_includes_cited_krs(db_session: AsyncSession) -> None:
    """When sources have activity for a KR the opener's digest includes that KR."""
    await _seed_slack_context(db_session)

    # Build a mock KR ORM object.
    mock_kr = MagicMock()
    mock_kr.id = 7
    mock_kr.title = "Increase pipeline coverage"
    mock_kr.prog = 45
    mock_kr.status = "inprogress"
    mock_kr.archived_at = None

    mock_activity = MagicMock()
    mock_activity.kr_id = 7
    mock_activity.text = "Merged pipeline coverage PR"
    mock_activity.created_at = datetime.now(UTC)

    mock_obj = MagicMock()
    mock_obj.id = 1
    mock_obj.title = "Grow product reach"
    mock_obj.key_results = [mock_kr]

    sources: dict[str, Any] = {
        "objectives": [mock_obj],
        "activity": [mock_activity],
        "jira_done": [],
        "action_items": [],
    }

    with (
        patch(
            "artemis.proactivity.scheduler.gather_checkin_sources",
            new_callable=AsyncMock,
            return_value=sources,
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_post_dm,
    ):
        await _fire_okr_checkin()

    assert mock_post_dm.await_count == 1
    assert mock_post_dm.await_args is not None
    slack_text = str(mock_post_dm.await_args.kwargs["text"])
    # Digest-based opener: the in-motion KR should appear by name and progress.
    assert "Increase pipeline coverage" in slack_text
    assert "45%" in slack_text
    # The ask must be present.
    assert "move" in slack_text.lower() or "map" in slack_text.lower()


# ── 3. Proposal generator: no source → no proposal ───────────────────────────


def test_build_proposal_no_source_produces_no_change() -> None:
    """A KR with no activity, no Jira match, no action-item match → not proposed."""
    mock_kr = MagicMock()
    mock_kr.id = 99
    mock_kr.title = "Unique KR title xyzzy"
    mock_kr.prog = 10
    mock_kr.archived_at = None

    mock_obj = MagicMock()
    mock_obj.id = 1
    mock_obj.title = "Parent Objective"
    mock_obj.key_results = [mock_kr]

    sources: dict[str, Any] = {
        "objectives": [mock_obj],
        "activity": [],  # no activity at all
        "jira_done": [],  # no Jira
        "action_items": [],  # no meeting items
    }

    proposals = build_okr_checkin_proposal(sources)
    assert proposals == [], f"Expected no proposals for ungrounded KR, got {proposals}"


def test_build_proposal_with_activity_includes_kr() -> None:
    """A KR with matching activity this week IS included in proposals."""
    mock_kr = MagicMock()
    mock_kr.id = 42
    mock_kr.title = "Improve retention metrics"
    mock_kr.prog = 30
    mock_kr.archived_at = None

    mock_activity = MagicMock()
    mock_activity.kr_id = 42
    mock_activity.text = "Shipped retention dashboard"
    mock_activity.created_at = datetime.now(UTC)

    mock_obj = MagicMock()
    mock_obj.id = 1
    mock_obj.title = "Grow user base"
    mock_obj.key_results = [mock_kr]

    sources: dict[str, Any] = {
        "objectives": [mock_obj],
        "activity": [mock_activity],
        "jira_done": [],
        "action_items": [],
    }

    proposals = build_okr_checkin_proposal(sources)
    assert len(proposals) == 1
    p = proposals[0]
    assert p["kr_id"] == 42
    assert p["kr_title"] == "Improve retention metrics"
    assert any("OKR activity" in b for b in p["basis"])


def test_build_proposal_archived_kr_excluded() -> None:
    """Archived KRs are excluded from proposals even if they have activity."""
    mock_kr = MagicMock()
    mock_kr.id = 11
    mock_kr.title = "Old archived KR"
    mock_kr.prog = 100
    mock_kr.archived_at = datetime(2025, 1, 1, tzinfo=UTC)

    mock_activity = MagicMock()
    mock_activity.kr_id = 11
    mock_activity.text = "Some archived activity"
    mock_activity.created_at = datetime.now(UTC)

    mock_obj = MagicMock()
    mock_obj.id = 1
    mock_obj.title = "Old Objective"
    mock_obj.key_results = [mock_kr]

    sources: dict[str, Any] = {
        "objectives": [mock_obj],
        "activity": [mock_activity],
        "jira_done": [],
        "action_items": [],
    }

    proposals = build_okr_checkin_proposal(sources)
    assert proposals == [], "Archived KRs must not be proposed"


def test_format_checkin_empty_proposals() -> None:
    """Empty proposals produce a minimal digest message asking Jon to share what moved."""
    # June 12, 2026 is a Friday — header should say "Friday check-in".
    friday = date(2026, 6, 12)
    text_friday = format_checkin_for_slack([], delivery_date=friday)
    assert "Friday" in text_friday
    assert "check-in" in text_friday.lower()
    assert "go" in text_friday

    # A non-Friday date — header should NOT say "Friday check-in".
    thursday = date(2026, 6, 11)
    text_thursday = format_checkin_for_slack([], delivery_date=thursday)
    # Must NOT start with "Friday check-in" header.
    assert text_thursday.startswith("*Friday") is False
    assert "OKR check-in" in text_thursday or "check-in" in text_thursday.lower()
    assert "go" in text_thursday


def test_format_checkin_with_proposals_still_asks_and_gates() -> None:
    """format_checkin_for_slack always produces an ask + safety gate regardless of proposals.

    The opener no longer echoes the proposal list — the digest drives the opener.
    This test verifies the structure: header, ask, and safety gate are present.
    """
    proposals = [
        {
            "kr_id": 1,
            "kr_title": "Improve pipeline",
            "objective_title": "Scale ops",
            "current_prog": 50,
            "basis": ["OKR activity: Merged pipeline fix"],
        }
    ]
    # Use a Friday date so header is predictable.
    text = format_checkin_for_slack(proposals, delivery_date=date(2026, 6, 12))
    # Header must be date-aware.
    assert "Friday" in text
    assert "check-in" in text.lower()
    # Must ask what Jon moved.
    assert "move" in text.lower() or "map" in text.lower()
    # Safety gate must be present.
    assert "go" in text.lower()


# ── 4. Scheduler registration ─────────────────────────────────────────────────


def test_okr_checkin_scheduler_registration() -> None:
    """The OKR check-in job registers with the configured cron expression."""
    with (
        patch("artemis.proactivity.scheduler.settings.okr_checkin_cron", "0 16 * * 5"),
        patch("artemis.proactivity.scheduler.settings.morning_brief_tz", "America/New_York"),
    ):
        scheduler = get_proactivity_scheduler()
        _register_okr_checkin_job(scheduler)

    job = scheduler.get_job("proactivity_okr_checkin")
    assert job is not None
    assert job.misfire_grace_time == 3600
    assert "day_of_week='fri'" in str(job.trigger) or "5" in str(job.trigger)
    assert "hour='16'" in str(job.trigger)
