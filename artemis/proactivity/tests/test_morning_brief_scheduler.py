"""Tests for the P2a scheduled morning brief Slack delivery."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.brief.models import BriefSnapshot
from artemis.integrations import repository as integration_repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.proactivity.models import MorningBriefDelivery
from artemis.proactivity.scheduler import (
    _fire_morning_brief,
    _format_brief_for_slack,
    get_proactivity_scheduler,
    start_proactivity_scheduler,
    stop_proactivity_scheduler,
)


@pytest.fixture(autouse=True)
async def reset_scheduler() -> AsyncGenerator[None, None]:
    yield
    stop_proactivity_scheduler()


async def _seed_slack_delivery_context(db_session: AsyncSession) -> None:
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


@pytest.mark.asyncio
async def test_fire_morning_brief_generates_and_delivers_once(db_session: AsyncSession) -> None:
    await _seed_slack_delivery_context(db_session)
    db_session.add(
        BriefSnapshot(
            id=42,
            brief_json={"summary": "Persisted snapshot"},
            sources_json={"sources": ["slack"]},
            model="claude-haiku-4-5-20251001",
            tokens_input=10,
            tokens_output=5,
            generated_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    brief = {
        "_snapshotId": 42,
        "summary": "Focus on pipeline cleanup today.",
        "top_priorities": [{"item": "Review candidates", "rationale": "Gate is waiting", "urgency": "high"}],
        "waiting_on_you": [{"who": "Angela", "context": "Reply needed re: pipeline"}],
        "okr_at_risk": None,
        "confidence": "high",
    }

    with (
        patch(
            "artemis.proactivity.scheduler.generate_brief",
            new_callable=AsyncMock,
            return_value=brief,
        ) as mock_generate,
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_post_dm,
    ):
        await _fire_morning_brief()
        await _fire_morning_brief()

    mock_generate.assert_awaited_once()
    assert mock_post_dm.await_count == 1
    assert mock_post_dm.await_args is not None
    delivered_text = str(mock_post_dm.await_args.kwargs["text"])
    assert mock_post_dm.await_args.kwargs["user"] == "U_JON"
    assert "*Morning brief for" in delivered_text
    assert "Review candidates" in delivered_text

    result = await db_session.execute(select(MorningBriefDelivery))
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].snapshot_id == 42
    assert rows[0].recipient_id == "U_JON"


def test_format_brief_for_slack_lints_tables_and_banned_chars() -> None:
    delivery_date = datetime(2026, 6, 11, tzinfo=ZoneInfo("America/New_York")).date()
    brief = {
        "summary": "| Topic | Note |\n| --- | --- |\n| Pipeline | Needs review |\n\nShip it — today 😀",
        "top_priorities": [],
        "waiting_on_you": [],
        "okr_at_risk": None,
        "confidence": "medium",
    }

    text = _format_brief_for_slack(brief, delivery_date=delivery_date)

    assert "😀" not in text
    assert "—" not in text
    assert "| Topic | Note |" not in text
    assert "- **Pipeline:** Needs review" in text
    assert "Ship it, today" in text


def test_format_brief_no_confidence_line_no_trailing_tags() -> None:
    """Trimmed brief: no 'Confidence' line, no redundant sections."""
    delivery_date = datetime(2026, 6, 11, tzinfo=ZoneInfo("America/New_York")).date()
    brief = {
        "summary": "A focused day.",
        "top_priorities": [
            {"item": "Ship the release", "rationale": "Blocked team downstream", "urgency": "high"},
            {"item": "Review OKR progress", "rationale": None, "urgency": "medium"},
        ],
        "waiting_on_you": [
            {"who": "Angela", "context": "Waiting on pipeline approval"},
        ],
        "okr_at_risk": "Product KR at 34% — stalled",
        "confidence": "high",
    }

    text = _format_brief_for_slack(brief, delivery_date=delivery_date)

    # No Confidence block at all
    assert "Confidence" not in text

    # Priorities shown with rationale, no trailing urgency tag
    assert "Ship the release: Blocked team downstream" in text
    assert "; high" not in text
    assert "; medium" not in text

    # Waiting on you section present
    assert "Angela" in text
    assert "Waiting on pipeline approval" in text

    # OKR at risk shown
    assert "Product KR at 34%" in text

    # Removed sections must NOT appear
    assert "*Highlights*" not in text
    assert "*Next Actions*" not in text
    assert "*Risks*" not in text

    # Sanity: core content still present
    assert "A focused day" in text
    assert "Ship the release" in text
    assert "Review OKR progress" in text


@pytest.mark.asyncio
async def test_scheduler_registration_uses_configured_cron() -> None:
    with (
        patch("artemis.proactivity.scheduler.settings.morning_brief_cron", "15 7 * * *"),
        patch("artemis.proactivity.scheduler.settings.morning_brief_tz", "America/New_York"),
    ):
        start_proactivity_scheduler()

    job = get_proactivity_scheduler().get_job("proactivity_morning_brief")
    assert job is not None
    assert job.misfire_grace_time == 3600
    assert "hour='7'" in str(job.trigger)
    assert "minute='15'" in str(job.trigger)
