from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.integrations import repository as integrations_repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.marketing.models import Approval, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.writing_studio import invoke as ws_invoke
from artemis.marketing.writing_studio import review_notifications

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


async def _make_candidate(session: AsyncSession) -> int:
    signal = await create_signal(
        session,
        headline="District literacy shift",
        campaign_family="outreach_email",
        source_type="manual",
        summary="Signal summary",
        discovered_by="test",
        state="TX",
    )
    candidate = await create_campaign_candidate_from_signal(
        session,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )
    candidate.name = "Fort Bend Follow-Up"
    await session.commit()
    return candidate.id


async def test_ready_for_review_marks_blank_draft_and_reuses_pending_approval(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_callie_integration(db_session)
    draft = await ws_invoke.create_blank_draft(db_session, title="Blank review draft")
    monkeypatch.setattr(settings, "app_base_url", "https://artemis.example")

    with (
        patch(
            "artemis.integrations.slack.client.SlackClient.lookup_user_by_email",
            new=AsyncMock(return_value="U_ANGELA"),
        ) as lookup_mock,
        patch(
            "artemis.integrations.slack.client.SlackClient.post_message",
            new=AsyncMock(return_value={"ok": True}),
        ) as post_message_mock,
    ):
        first = await client.post(
            f"/api/writing-studio/drafts/{draft.id}/ready-for-review",
            json={"reviewerEmail": "angela@amiralearning.com"},
        )
        second = await client.post(
            f"/api/writing-studio/drafts/{draft.id}/ready-for-review",
            json={"reviewerEmail": "angela@amiralearning.com"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["ok"] is True
    assert first.json()["reviewerEmail"] == "angela@amiralearning.com"
    assert first.json()["delivery"]["target"] == "channel_mention"
    assert first.json()["delivery"]["channelId"] == "C_MARKETING"
    assert first.json()["delivery"]["slackUserId"] == "U_ANGELA"
    assert lookup_mock.await_count == 2
    assert post_message_mock.await_count == 2
    post_message_mock.assert_any_await(
        channel="C_MARKETING",
        text=(
            f'<@U_ANGELA> - "Blank review draft" by Local Dev is ready for review. '
            f"<https://artemis.example/#writing-studio?draft={draft.id}|Open draft>"
        ),
    )

    deliverable = await db_session.get(CampaignDeliverable, draft.id)
    assert deliverable is not None
    assert deliverable.status == "draft_ready"
    metadata = dict(deliverable.deliverable_metadata or {})
    assert metadata["ready_for_review"] is True
    assert metadata["review_status"] == "ready_for_review"
    assert metadata["ready_for_review_at"]
    assert metadata["reviewer_email"] == "angela@amiralearning.com"

    approvals = (
        await db_session.execute(
            select(Approval).where(
                Approval.kind == "writing_gate_2",
                Approval.subject_id == str(draft.id),
            )
        )
    ).scalars()
    assert len(list(approvals)) == 1


async def test_ready_for_review_defaults_to_campaign_approver(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_callie_integration(db_session)
    candidate_id = await _make_candidate(db_session)
    draft = await ws_invoke.create_draft_from_candidate(db_session, candidate_id)

    approval = Approval(
        kind="content_draft",
        subject_id="run-123:gate_2_approval_drawer",
        status="pending",
        decision_payload={"approvers": ["reviewer@example.com"]},
        pipe4_context={
            "pipeline_run_id": "run-123",
            "context": {"candidate_id": candidate_id, "deliverable_ids": [draft.id]},
        },
    )
    db_session.add(approval)
    await db_session.commit()

    with (
        patch(
            "artemis.integrations.slack.client.SlackClient.lookup_user_by_email",
            new=AsyncMock(return_value="U_REVIEWER"),
        ) as lookup_mock,
        patch(
            "artemis.integrations.slack.client.SlackClient.post_message",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        response = await client.post(
            f"/api/writing-studio/drafts/{draft.id}/ready-for-review", json={}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["reviewerEmail"] == "reviewer@example.com"
    lookup_mock.assert_awaited_once_with("reviewer@example.com")


async def test_ready_for_review_falls_back_to_marketing_channel_when_user_missing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_callie_integration(db_session)
    draft = await ws_invoke.create_blank_draft(db_session, title="Channel fallback draft")
    monkeypatch.setattr(settings, "marketing_campaigns_slack_channel", "C_MARKETING")
    monkeypatch.setattr(settings, "app_base_url", "https://artemis.example")

    with (
        patch(
            "artemis.integrations.slack.client.SlackClient.lookup_user_by_email",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient.post_message",
            new=AsyncMock(return_value={"ok": True}),
        ) as post_message_mock,
    ):
        response = await client.post(
            f"/api/writing-studio/drafts/{draft.id}/ready-for-review",
            json={"reviewerEmail": "missing@example.com"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["delivery"]["target"] == "channel_mention"
    assert body["delivery"]["channelId"] == "C_MARKETING"
    post_message_mock.assert_awaited_once_with(
        channel="C_MARKETING",
        text=(
            f'missing@example.com - "Channel fallback draft" by Local Dev is ready for review. '
            f"<https://artemis.example/#writing-studio?draft={draft.id}|Open draft>"
        ),
    )


async def test_send_callie_ready_for_review_ping_dm_mode_still_uses_direct_message(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_callie_integration(db_session)
    monkeypatch.setattr(settings, "app_base_url", "https://artemis.example")

    with (
        patch(
            "artemis.integrations.slack.client.SlackClient.lookup_user_by_email",
            new=AsyncMock(return_value="U_REVIEWER"),
        ) as lookup_mock,
        patch(
            "artemis.integrations.slack.client.SlackClient.post_dm",
            new=AsyncMock(return_value={"ok": True}),
        ) as post_dm_mock,
        patch(
            "artemis.integrations.slack.client.SlackClient.post_message",
            new=AsyncMock(return_value={"ok": True}),
        ) as post_message_mock,
    ):
        result = await review_notifications.send_callie_ready_for_review_ping(
            db_session,
            draft_id=42,
            title="Escalation draft",
            author_name="Local Dev",
            reviewer_email="reviewer@example.com",
            mode="dm",
        )

    assert result.ok is True
    assert result.target == "dm"
    assert result.slack_user_id == "U_REVIEWER"
    lookup_mock.assert_awaited_once_with("reviewer@example.com")
    post_dm_mock.assert_awaited_once_with(
        "U_REVIEWER",
        '"Escalation draft" by Local Dev is ready for review. '
        "<https://artemis.example/#writing-studio?draft=42|Open draft>",
    )
    post_message_mock.assert_not_called()
