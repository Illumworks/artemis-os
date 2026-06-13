from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as integrations_repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.marketing.models import Approval, CampaignDeliverable
from artemis.marketing.writing_studio import invoke as ws_invoke
from artemis.marketing.writing_studio.review_escalation import send_stale_review_escalations
from artemis.proactivity.scheduler import (
    get_proactivity_scheduler,
    start_proactivity_scheduler,
    stop_proactivity_scheduler,
)

pytestmark = pytest.mark.asyncio


async def _seed_callie_integration(session: AsyncSession) -> None:
    await integrations_repo.upsert_integration(
        session,
        provider="slack",
        workspace_id="T_TEST",
        agent_id="callie",
        encrypted_credentials=encrypt_credentials({"bot_token": "xoxb-callie"}),
        display_name="Callie Test Workspace",
        metadata={"allowed_channel_ids": ["C_MARKETING"]},
    )
    await session.commit()


async def _seed_ready_for_review_draft(
    session: AsyncSession,
    *,
    reviewer_email: str,
    ready_for_review_at: datetime,
    approval_status: str = "pending",
) -> CampaignDeliverable:
    draft = await ws_invoke.create_blank_draft(session, title="Escalation draft")
    deliverable = await session.get(CampaignDeliverable, draft.id)
    assert deliverable is not None
    metadata = dict(deliverable.deliverable_metadata or {})
    metadata.update(
        {
            "title": "Escalation draft",
            "ready_for_review": True,
            "review_status": "ready_for_review",
            "ready_for_review_at": ready_for_review_at.isoformat(),
            "review_requested_at": ready_for_review_at.isoformat(),
            "reviewer_email": reviewer_email,
            "review_requested_by_name": "Local Dev",
        }
    )
    deliverable.deliverable_metadata = metadata
    session.add(
        Approval(
            kind="writing_gate_2",
            subject_id=str(deliverable.id),
            status=approval_status,
            decision_payload={"deliverableId": deliverable.id},
        )
    )
    await session.commit()
    return deliverable


async def test_stale_review_escalation_sends_one_dm_and_dedupes(
    db_session: AsyncSession,
) -> None:
    await _seed_callie_integration(db_session)
    stale_at = datetime.now(UTC) - timedelta(days=2)
    draft = await _seed_ready_for_review_draft(
        db_session,
        reviewer_email="jon.fila@amiralearning.com",
        ready_for_review_at=stale_at,
    )
    now = stale_at + timedelta(days=2, hours=1)

    with (
        patch(
            "artemis.integrations.slack.client.SlackClient.lookup_user_by_email",
            new=AsyncMock(return_value="U_JON"),
        ) as lookup_mock,
        patch(
            "artemis.integrations.slack.client.SlackClient.post_dm",
            new=AsyncMock(return_value={"ok": True}),
        ) as post_dm_mock,
    ):
        first = await send_stale_review_escalations(
            db_session,
            now=now,
            stale_after=timedelta(hours=24),
        )
        second = await send_stale_review_escalations(
            db_session,
            now=now + timedelta(minutes=5),
            stale_after=timedelta(hours=24),
        )

    assert first.checked == 1
    assert first.eligible == 1
    assert first.sent == 1
    assert first.failed == 0
    assert second.checked == 1
    assert second.eligible == 0
    assert second.sent == 0
    lookup_mock.assert_awaited_once_with("jon.fila@amiralearning.com")
    post_dm_mock.assert_awaited_once_with(
        "U_JON",
        (
            f'Follow-up: "Escalation draft" by Local Dev is still waiting for review. '
            f"<http://127.0.0.1:8000/#writing-studio?draft={draft.id}|Open draft>"
        ),
    )

    refreshed = await db_session.get(CampaignDeliverable, draft.id)
    assert refreshed is not None
    metadata = dict(refreshed.deliverable_metadata or {})
    assert metadata["escalated_at"] == now.isoformat()
    assert metadata["review_escalation_sent_at"] == now.isoformat()
    assert metadata["review_escalation_target"] == "dm"
    assert metadata["review_escalation_slack_user_id"] == "U_JON"


async def test_stale_review_escalation_skips_fresh_and_already_approved(
    db_session: AsyncSession,
) -> None:
    await _seed_callie_integration(db_session)
    now = datetime.now(UTC)
    fresh_draft = await _seed_ready_for_review_draft(
        db_session,
        reviewer_email="fresh@example.com",
        ready_for_review_at=now - timedelta(hours=2),
    )
    approved_draft = await _seed_ready_for_review_draft(
        db_session,
        reviewer_email="approved@example.com",
        ready_for_review_at=now - timedelta(days=2),
        approval_status="approved",
    )

    with (
        patch(
            "artemis.integrations.slack.client.SlackClient.lookup_user_by_email",
            new=AsyncMock(return_value="U_ANY"),
        ) as lookup_mock,
        patch(
            "artemis.integrations.slack.client.SlackClient.post_dm",
            new=AsyncMock(return_value={"ok": True}),
        ) as post_dm_mock,
    ):
        summary = await send_stale_review_escalations(
            db_session,
            now=now,
            stale_after=timedelta(hours=24),
        )

    assert summary.checked == 1
    assert summary.eligible == 0
    assert summary.sent == 0
    assert summary.failed == 0
    lookup_mock.assert_not_called()
    post_dm_mock.assert_not_called()

    fresh = await db_session.get(CampaignDeliverable, fresh_draft.id)
    approved = await db_session.get(CampaignDeliverable, approved_draft.id)
    assert fresh is not None and "escalated_at" not in dict(fresh.deliverable_metadata or {})
    assert approved is not None and "escalated_at" not in dict(approved.deliverable_metadata or {})


async def test_proactivity_scheduler_registers_stale_review_job() -> None:
    with (
        patch("artemis.proactivity.scheduler.settings.review_escalation_cron", "45 16 * * *"),
        patch("artemis.proactivity.scheduler.settings.review_escalation_tz", "America/New_York"),
    ):
        start_proactivity_scheduler()

    try:
        job = get_proactivity_scheduler().get_job("proactivity_stale_review_escalation")
        assert job is not None
        assert job.misfire_grace_time == 3600
        assert "hour='16'" in str(job.trigger)
        assert "minute='45'" in str(job.trigger)
    finally:
        stop_proactivity_scheduler()
