"""Deterministic review-request notifications for Writing Studio drafts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.integrations import repository as integrations_repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.slack.client import SlackClient
from artemis.marketing.models import Approval, CampaignDeliverable

_CALLIE_CAMPAIGN_SIGNALS_CHANNEL = "C0B9CHVC7KQ"
DEFAULT_REVIEWER_EMAIL = "angela@amiralearning.com"
ReviewPingMode = Literal["channel_mention", "dm"]


@dataclass(frozen=True)
class ReviewPingResult:
    ok: bool
    reviewer_email: str
    target: str | None = None
    slack_user_id: str | None = None
    channel_id: str | None = None
    error: str | None = None


def _normalize_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _parse_id_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value]
    else:
        items = []

    ordered: list[str] = []
    for item in items:
        if item and item not in ordered:
            ordered.append(item)
    return tuple(ordered)


async def resolve_reviewer_email(
    session: AsyncSession,
    deliverable: CampaignDeliverable,
    requested_email: str | None = None,
) -> str:
    """Resolve the effective reviewer email for a draft review request.

    Preference order:
    1. Explicit request body reviewer
    2. Previously selected reviewer stored on the draft
    3. The newest PIPE4 content_draft approval approver for the same candidate
    4. Angela fallback
    """

    explicit = _normalize_email(requested_email)
    if explicit is not None:
        return explicit

    metadata = (
        dict(deliverable.deliverable_metadata)
        if isinstance(deliverable.deliverable_metadata, dict)
        else {}
    )
    stored = _normalize_email(metadata.get("reviewer_email") or metadata.get("reviewerEmail"))
    if stored is not None:
        return stored

    candidate_id = deliverable.candidate_id
    if candidate_id is not None:
        result = await session.execute(
            select(Approval)
            .where(Approval.kind == "content_draft")
            .order_by(Approval.created_at.desc())
            .limit(200)
        )
        approvals = list(result.scalars().all())
        for approval in approvals:
            pipe4_context = (
                approval.pipe4_context if isinstance(approval.pipe4_context, dict) else {}
            )
            context = pipe4_context.get("context")
            if not isinstance(context, dict):
                continue
            if context.get("candidate_id") != candidate_id:
                continue
            payload = (
                approval.decision_payload if isinstance(approval.decision_payload, dict) else {}
            )
            approvers = payload.get("approvers")
            if not isinstance(approvers, list):
                continue
            for approver in approvers:
                normalized = _normalize_email(approver)
                if normalized is not None:
                    return normalized

    return DEFAULT_REVIEWER_EMAIL


async def send_callie_ready_for_review_ping(
    session: AsyncSession,
    *,
    draft_id: int,
    title: str,
    author_name: str,
    reviewer_email: str,
    mode: ReviewPingMode = "channel_mention",
) -> ReviewPingResult:
    """Post the deterministic review ping as Callie.

    ``channel_mention`` posts publicly in Marketing Campaigns and @mentions the
    reviewer when Slack can resolve the email. ``dm`` preserves the direct
    message path for later escalation flows.
    """

    integrations = await integrations_repo.list_active(session, provider="slack")
    callie_row = next(
        (
            row
            for row in integrations
            if str(getattr(row, "agent_id", "")).strip().lower() == "callie"
        ),
        None,
    )
    if callie_row is None:
        return ReviewPingResult(
            ok=False,
            reviewer_email=reviewer_email,
            error="Callie Slack integration is not configured.",
        )

    creds_raw = decrypt_credentials(bytes(callie_row.encrypted_credentials))
    creds = creds_raw if isinstance(creds_raw, dict) else {}
    token = str(creds.get("access_token") or creds.get("bot_token") or creds.get("token") or "")
    if not token:
        return ReviewPingResult(
            ok=False,
            reviewer_email=reviewer_email,
            error="Callie Slack integration is missing a bot token.",
        )

    metadata_raw = getattr(callie_row, "metadata_", {}) or {}
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    allowed_channel_ids = _parse_id_list(
        metadata.get("allowed_channel_ids") or creds.get("allowed_channel_ids")
    )

    preferred_channel_id = settings.marketing_campaigns_slack_channel.strip()
    channel_id = (
        preferred_channel_id
        or (allowed_channel_ids[0] if allowed_channel_ids else "")
        or _CALLIE_CAMPAIGN_SIGNALS_CHANNEL
    )

    base_url = settings.app_base_url.rstrip("/") or "http://127.0.0.1:8000"
    draft_url = f"{base_url}/#writing-studio?draft={draft_id}"
    safe_title = " ".join((title or f"Draft {draft_id}").split())
    safe_author = " ".join((author_name or "The author").split())
    fallback_reviewer_label = reviewer_email.strip() or "The reviewer"

    client = SlackClient(token)
    try:
        slack_user_id = await client.lookup_user_by_email(reviewer_email)
        if mode == "dm":
            text = f'"{safe_title}" by {safe_author} is ready for review. <{draft_url}|Open draft>'
            if slack_user_id:
                await client.post_dm(slack_user_id, text)
                return ReviewPingResult(
                    ok=True,
                    reviewer_email=reviewer_email,
                    target="dm",
                    slack_user_id=slack_user_id,
                )
            return ReviewPingResult(
                ok=False,
                reviewer_email=reviewer_email,
                error="Reviewer could not be resolved in Slack for a direct message.",
            )

        if not channel_id:
            return ReviewPingResult(
                ok=False,
                reviewer_email=reviewer_email,
                error="Marketing Campaigns channel is not configured for Callie.",
            )

        reviewer_label = f"<@{slack_user_id}>" if slack_user_id else fallback_reviewer_label
        text = f'{reviewer_label} - "{safe_title}" by {safe_author} is ready for review. <{draft_url}|Open draft>'
        await client.post_message(channel=channel_id, text=text)
        return ReviewPingResult(
            ok=True,
            reviewer_email=reviewer_email,
            target="channel_mention",
            slack_user_id=slack_user_id,
            channel_id=channel_id,
        )
    except Exception as exc:  # noqa: BLE001
        return ReviewPingResult(
            ok=False,
            reviewer_email=reviewer_email,
            error=str(exc),
        )
