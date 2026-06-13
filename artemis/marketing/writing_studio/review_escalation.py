"""Daily stale-review escalation for Writing Studio drafts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Text, and_, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import Approval, CampaignDeliverable
from artemis.marketing.writing_studio import review_notifications
from artemis.marketing.writing_studio.review_notifications import ReviewPingResult


@dataclass(frozen=True)
class ReviewEscalationSummary:
    checked: int
    eligible: int
    sent: int
    failed: int


async def send_stale_review_escalations(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_after: timedelta,
) -> ReviewEscalationSummary:
    """DM each stale pending review once, stamping metadata on success."""

    current_time = now or datetime.now(UTC)
    pending_rows = await _load_pending_review_rows(session)
    checked = len(pending_rows)
    eligible = 0
    sent = 0
    failed = 0

    for deliverable, _approval in pending_rows:
        metadata = _metadata_copy(deliverable)
        ready_for_review_at = _parse_metadata_datetime(
            metadata.get("ready_for_review_at")
            or metadata.get("readyForReviewAt")
            or metadata.get("review_requested_at")
            or metadata.get("reviewRequestedAt")
        )
        if ready_for_review_at is None:
            continue
        if current_time - ready_for_review_at < stale_after:
            continue
        if _parse_metadata_datetime(metadata.get("escalated_at")) is not None:
            continue

        eligible += 1
        reviewer_email = await review_notifications.resolve_reviewer_email(session, deliverable)
        ping = await review_notifications.send_callie_ready_for_review_ping(
            session,
            draft_id=deliverable.id,
            title=_title_from_metadata(metadata, deliverable_id=deliverable.id),
            author_name=_author_name_from_metadata(metadata),
            reviewer_email=reviewer_email,
            mode="dm",
            kind="stale_review_escalation",
        )
        if await _record_escalation_result(
            session,
            deliverable=deliverable,
            metadata=metadata,
            ping=ping,
            current_time=current_time,
        ):
            sent += 1
        else:
            failed += 1

    return ReviewEscalationSummary(
        checked=checked,
        eligible=eligible,
        sent=sent,
        failed=failed,
    )


async def _load_pending_review_rows(
    session: AsyncSession,
) -> list[tuple[CampaignDeliverable, Approval]]:
    result = await session.execute(
        select(CampaignDeliverable, Approval)
        .join(
            Approval,
            and_(
                Approval.kind == "writing_gate_2",
                Approval.status == "pending",
                Approval.subject_id == cast(CampaignDeliverable.id, Text),
            ),
        )
        .order_by(Approval.created_at.asc(), CampaignDeliverable.id.asc())
    )
    return list(result.all())


async def _record_escalation_result(
    session: AsyncSession,
    *,
    deliverable: CampaignDeliverable,
    metadata: dict[str, object],
    ping: ReviewPingResult,
    current_time: datetime,
) -> bool:
    metadata["review_escalation_attempted_at"] = current_time.isoformat()
    metadata["review_escalation_target"] = ping.target
    metadata["review_escalation_slack_user_id"] = ping.slack_user_id
    metadata["review_escalation_channel_id"] = ping.channel_id
    metadata["review_escalation_error"] = ping.error
    if ping.ok:
        metadata["escalated_at"] = current_time.isoformat()
        metadata["review_escalation_sent_at"] = current_time.isoformat()
    deliverable.deliverable_metadata = metadata

    try:
        await session.flush()
        await session.commit()
    except Exception:
        await session.rollback()
        return False
    return ping.ok


def _metadata_copy(deliverable: CampaignDeliverable) -> dict[str, object]:
    raw = deliverable.deliverable_metadata
    return dict(raw) if isinstance(raw, dict) else {}


def _author_name_from_metadata(metadata: dict[str, object]) -> str:
    for key in ("review_requested_by_name", "reviewRequestedByName", "review_requested_by_email"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "The author"


def _title_from_metadata(metadata: dict[str, object], *, deliverable_id: int) -> str:
    for key in ("title", "externalTitle"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"Draft {deliverable_id}"


def _parse_metadata_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
