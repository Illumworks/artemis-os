from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.identity.repository import get_or_create_user
from artemis.integrations import repository as integrations_repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.memory.store import list_observations_for_scope, list_scopes_for_observation
from artemis.proactivity import repository as prepo
from artemis.proactivity.commitments import (
    ingest_meeting_commitments,
    send_commitment_followups,
    try_apply_commitment_reply,
)
from artemis.proactivity.models import Commitment
from artemis.proactivity.scheduler import (
    get_proactivity_scheduler,
    start_proactivity_scheduler,
    stop_proactivity_scheduler,
)

pytestmark = pytest.mark.asyncio


async def _seed_user(session: AsyncSession, *, email: str, name: str) -> int:
    user = await get_or_create_user(session, email, name)
    await session.commit()
    await session.refresh(user)
    return user.id


async def _seed_artemis_slack(session: AsyncSession) -> None:
    await integrations_repo.upsert_integration(
        session,
        provider="slack",
        workspace_id="T_ARTEMIS",
        agent_id="artemis",
        encrypted_credentials=encrypt_credentials({"bot_token": "xoxb-artemis"}),
        display_name="Artemis Workspace",
    )
    await integrations_repo.upsert_provider_config(
        session,
        "slack",
        {"authed_user_id": "U_JON"},
    )
    await session.commit()


async def _seed_callie_slack(session: AsyncSession) -> None:
    await integrations_repo.upsert_integration(
        session,
        provider="slack",
        workspace_id="T_CALLIE",
        agent_id="callie",
        encrypted_credentials=encrypt_credentials({"bot_token": "xoxb-callie"}),
        display_name="Callie Workspace",
        metadata={"allowed_channel_ids": ["C_MARKETING"]},
    )
    await session.commit()


async def test_meeting_ingest_writes_commitment_and_memory_and_dedupes(
    db_session: AsyncSession,
) -> None:
    owner_id = await _seed_user(
        db_session,
        email="jon.fila@amiralearning.com",
        name="Jon Fila",
    )
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    first = await ingest_meeting_commitments(
        db_session,
        granola_id="g-commit-1",
        title="Weekly ops sync",
        action_items=[
            {
                "text": "Send the board recap",
                "owner": "Jon",
                "due": "2026-06-15",
            }
        ],
        now=now,
    )
    await db_session.commit()

    second = await ingest_meeting_commitments(
        db_session,
        granola_id="g-commit-1",
        title="Weekly ops sync",
        action_items=[
            {
                "text": "Send the board recap",
                "owner": "Jon",
                "due": "2026-06-15",
            }
        ],
        now=now,
    )
    await db_session.commit()

    rows = list(
        (await db_session.execute(select(Commitment).order_by(Commitment.id.asc()))).scalars()
    )
    assert first.seen == 1
    assert first.inserted == 1
    assert first.deduped == 0
    assert second.inserted == 0
    assert second.deduped == 1
    assert len(rows) == 1
    assert rows[0].owner_user_id == owner_id
    assert rows[0].status == "active"
    assert rows[0].sensitivity == "personal_ops"
    assert rows[0].due == datetime(2026, 6, 15, 21, 0, tzinfo=UTC)

    observations = await list_observations_for_scope(
        db_session,
        "agent",
        "floating-artemis",
    )
    assert len(observations) == 1
    assert observations[0].category == "commitment"
    scopes = await list_scopes_for_observation(db_session, observations[0].id)
    assert ("meeting", "g-commit-1", 1.0, False) in scopes


async def test_followups_dm_artemis_and_skip_snoozed_done_and_dedupe(
    db_session: AsyncSession,
) -> None:
    owner_id = await _seed_user(
        db_session,
        email="jon.fila@amiralearning.com",
        name="Jon Fila",
    )
    await _seed_artemis_slack(db_session)
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    active, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-1",
        text="Send the recap",
        owner_user_id=owner_id,
        due=now + timedelta(hours=2),
        sensitivity="personal_ops",
    )
    snoozed, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-2",
        text="Review the notes",
        owner_user_id=owner_id,
        due=now + timedelta(hours=2),
        sensitivity="personal_ops",
    )
    done, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-3",
        text="Close the loop",
        owner_user_id=owner_id,
        due=now + timedelta(hours=2),
        sensitivity="personal_ops",
    )
    await db_session.commit()

    await prepo.snooze_commitment(
        db_session,
        commitment_id=snoozed.id,
        snoozed_until=now + timedelta(days=2),
        now=now,
    )
    await prepo.mark_commitment_done(
        db_session,
        commitment_id=done.id,
        now=now,
    )
    await db_session.commit()

    with patch(
        "artemis.integrations.slack.client.SlackClient.post_dm",
        new=AsyncMock(return_value={"ok": True}),
    ) as post_dm_mock:
        first = await send_commitment_followups(db_session, now=now)
        second = await send_commitment_followups(db_session, now=now + timedelta(minutes=5))

    assert first.sent == 1
    assert second.sent == 0
    post_dm_mock.assert_awaited_once()
    args = post_dm_mock.await_args
    assert args.kwargs["user"] == "U_JON"
    assert "done" in args.kwargs["text"]
    assert "snooze" in args.kwargs["text"]

    refreshed_active = await db_session.get(Commitment, active.id)
    refreshed_snoozed = await db_session.get(Commitment, snoozed.id)
    refreshed_done = await db_session.get(Commitment, done.id)
    assert refreshed_active is not None and refreshed_active.last_notified_at == now
    assert refreshed_snoozed is not None and refreshed_snoozed.last_notified_at is None
    assert refreshed_done is not None and refreshed_done.last_notified_at is None


async def test_followups_route_marketing_to_callie_channel(
    db_session: AsyncSession,
) -> None:
    await _seed_callie_slack(db_session)
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    commitment, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-mkt",
        text="Finalize the campaign brief",
        owner_user_id=None,
        due=now + timedelta(hours=4),
        sensitivity="marketing",
    )
    await db_session.commit()

    with patch(
        "artemis.integrations.slack.client.SlackClient.post_message",
        new=AsyncMock(return_value={"ok": True}),
    ) as post_message_mock:
        summary = await send_commitment_followups(db_session, now=now)

    assert summary.sent == 1
    post_message_mock.assert_awaited_once()
    args = post_message_mock.await_args
    assert args.kwargs["channel"] == "C_MARKETING"
    assert f"C{commitment.id}" in args.kwargs["text"]


async def test_commitment_reply_marks_snooze_then_done(db_session: AsyncSession) -> None:
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)
    commitment, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-reply",
        text="Follow up on the intro email",
        owner_user_id=None,
        due=now + timedelta(days=1),
        sensitivity="personal_ops",
    )
    await db_session.commit()

    snoozed = await try_apply_commitment_reply(
        db_session,
        text=f"snooze {commitment.id} 3d",
    )
    refreshed = await db_session.get(Commitment, commitment.id)
    assert snoozed is not None and "Snoozed commitment" in snoozed
    assert refreshed is not None and refreshed.status == "snoozed"
    assert refreshed.snoozed_until is not None

    done = await try_apply_commitment_reply(
        db_session,
        text=f"done {commitment.id}",
    )
    refreshed_done = await db_session.get(Commitment, commitment.id)
    assert done == f"Marked commitment C{commitment.id} done."
    assert refreshed_done is not None and refreshed_done.status == "done"
    assert refreshed_done.snoozed_until is None


async def test_proactivity_scheduler_registers_commitments_job() -> None:
    with (
        patch("artemis.proactivity.scheduler.settings.commitments_followup_cron", "10 11 * * *"),
        patch(
            "artemis.proactivity.scheduler.settings.commitments_followup_tz",
            "America/New_York",
        ),
    ):
        start_proactivity_scheduler()

    try:
        job = get_proactivity_scheduler().get_job("proactivity_commitments_followup")
        assert job is not None
        assert job.misfire_grace_time == 3600
        assert "hour='11'" in str(job.trigger)
        assert "minute='10'" in str(job.trigger)
    finally:
        stop_proactivity_scheduler()
