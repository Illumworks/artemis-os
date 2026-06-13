"""Commitment ingestion, follow-up delivery, and deterministic reply handling."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.identity.models import User
from artemis.integrations import repository as integrations_repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.models import Integration
from artemis.integrations.slack.client import SlackClient
from artemis.meetings.models import MeetingActionItemDismissal
from artemis.memory.schemas import Scope, SourceQualityHint
from artemis.memory.store import write_observation
from artemis.proactivity import repository as repo
from artemis.proactivity.models import Commitment
from artemis.writing_rules import lint_agent_text

logger = logging.getLogger(__name__)

_MARKETING_KEYWORDS = (
    "marketing",
    "campaign",
    "callie",
    "signal",
    "brief",
    "deliverable",
    "content",
    "review",
    "asset",
    "ads",
    "launch",
)
_DONE_RE = re.compile(r"^(?:done|complete|completed)\s+(?:c)?(\d+)\b", re.IGNORECASE)
_SNOOZE_RE = re.compile(
    r"^snooze\s+(?:c)?(\d+)(?:\s+(\d+)\s*([dhw]))?\b",
    re.IGNORECASE,
)
_DISMISS_RE = re.compile(
    r"^(?:dismiss|drop|irrelevant|not relevant|skip)\s+(?:c)?(\d+)\b",
    re.IGNORECASE,
)
_CALLIE_DEFAULT_CHANNEL = "C0B9CHVC7KQ"


@dataclass(frozen=True)
class CommitmentIngestSummary:
    seen: int
    inserted: int
    deduped: int


@dataclass(frozen=True)
class CommitmentFollowupSummary:
    checked: int
    eligible: int
    sent: int
    failed: int


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def action_item_key(text: str) -> str:
    """Return a stable SHA-256 hex key for a normalised action-item text.

    This is the content hash used as the stable identifier in
    meeting_action_item_dismissals.  Callers normalise the text first via
    _normalize_text so the hash is insensitive to whitespace variation.
    """
    normalized = _normalize_text(text).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _classify_sensitivity(*, title: str, text: str) -> str:
    haystack = f"{title} {text}".lower()
    if any(keyword in haystack for keyword in _MARKETING_KEYWORDS):
        return "marketing"
    return "personal_ops"


async def _resolve_owner_user_id(
    session: AsyncSession,
    owner_label: str | None,
) -> int | None:
    normalized = _normalize_text(owner_label).lower()
    if not normalized:
        return None

    local_part = normalized.split("@", 1)[0]
    first_token = normalized.split(" ", 1)[0]

    result = await session.execute(
        select(User)
        .where(
            or_(
                func.lower(func.coalesce(User.name, "")) == normalized,
                func.lower(User.email) == normalized,
                func.split_part(func.lower(User.email), "@", 1) == local_part,
                func.split_part(func.lower(func.coalesce(User.name, "")), " ", 1) == first_token,
            )
        )
        .order_by(User.last_seen_at.desc(), User.id.asc())
        .limit(1)
    )
    user = result.scalar_one_or_none()
    return user.id if user is not None else None


def _parse_due_token(
    due: str | None,
    *,
    now: datetime,
) -> datetime | None:
    value = _normalize_text(due).lower()
    if not value or value == "tbd":
        return None

    local_tz = ZoneInfo(settings.morning_brief_tz)
    local_now = now.astimezone(local_tz)

    if value == "today":
        target_date = local_now.date()
    elif value == "tomorrow":
        target_date = local_now.date() + timedelta(days=1)
    elif value == "this week":
        days_until_friday = max(0, 4 - local_now.weekday())
        target_date = local_now.date() + timedelta(days=days_until_friday)
    elif value == "next week":
        days_until_next_friday = max(0, 4 - local_now.weekday()) + 7
        target_date = local_now.date() + timedelta(days=days_until_next_friday)
    else:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
        target_date = parsed.date()

    local_due = datetime.combine(target_date, time(hour=17, minute=0), tzinfo=local_tz)
    return local_due.astimezone(UTC)


def _agent_scope_for(sensitivity: str) -> Scope:
    if sensitivity == "marketing":
        return Scope(scope_kind="agent", scope_id="callie")
    return Scope(scope_kind="agent", scope_id="floating-artemis")


async def ingest_meeting_commitments(
    session: AsyncSession,
    *,
    granola_id: str,
    title: str,
    action_items: list[dict[str, Any]],
    now: datetime | None = None,
) -> CommitmentIngestSummary:
    """Upsert meeting action items into commitments + mirrored memory observations."""
    current_time = now or datetime.now(UTC)
    seen = 0
    inserted = 0
    deduped = 0

    for item in action_items:
        if not isinstance(item, dict):
            continue
        text = _normalize_text(item.get("text"))
        if not text:
            continue

        # KEY GATE: skip items that Jon has already dismissed — durable across re-ingest.
        item_key = action_item_key(text)
        dismissed_check = await session.execute(
            select(MeetingActionItemDismissal).where(
                MeetingActionItemDismissal.granola_id == granola_id,
                MeetingActionItemDismissal.action_item_key == item_key,
            )
        )
        if dismissed_check.scalar_one_or_none() is not None:
            logger.debug(
                "ingest_meeting_commitments: skipping dismissed item key=%s granola_id=%s",
                item_key[:12],
                granola_id,
            )
            continue

        seen += 1
        owner_label = _normalize_text(item.get("owner")) or None
        owner_user_id = await _resolve_owner_user_id(session, owner_label)
        due_value = _parse_due_token(
            item.get("due") if isinstance(item.get("due"), str) else None,
            now=current_time,
        )
        sensitivity = _classify_sensitivity(title=title, text=text)
        commitment, created = await repo.upsert_commitment(
            session,
            source_type="granola_meeting",
            source_id=granola_id,
            text=text,
            owner_user_id=owner_user_id,
            due=due_value,
            sensitivity=sensitivity,
        )
        if created:
            inserted += 1
        else:
            deduped += 1

        scope = _agent_scope_for(sensitivity)
        raw_payload = {
            "granola_id": granola_id,
            "meeting_title": title,
            "commitment_id": commitment.id,
            "action_item": {
                "text": text,
                "owner": owner_label,
                "due": item.get("due"),
            },
        }
        await write_observation(
            session,
            scope=scope,
            content=f"Commitment [{granola_id}]: {text}",
            category="commitment",
            source_quality=SourceQualityHint.extractor,
            valid_until=due_value,
            owner_user_id=owner_user_id,
            raw_payload=raw_payload,
            raw_source_kind="meeting_commitment",
            raw_source_id=granola_id,
            raw_actor="meeting-commitment-ingest",
            additional_scopes=[Scope(scope_kind="meeting", scope_id=granola_id)],
            confidence_origin="meeting_action_item",
        )

    return CommitmentIngestSummary(seen=seen, inserted=inserted, deduped=deduped)


async def _get_slack_token_for_agent(
    session: AsyncSession,
    *,
    agent_id: str,
) -> str | None:
    result = await session.execute(
        select(Integration)
        .where(
            Integration.provider == "slack",
            Integration.status == "active",
            Integration.agent_id == agent_id,
        )
        .order_by(Integration.connected_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    creds = decrypt_credentials(bytes(row.encrypted_credentials))
    token = creds.get("bot_token") or creds.get("token") or creds.get("access_token")
    return str(token) if token else None


async def _resolve_artemis_dm_recipient(session: AsyncSession) -> str:
    stored = await integrations_repo.get_provider_config(session, "slack") or {}
    recipient_id = _normalize_text(stored.get("authed_user_id"))
    return recipient_id or "U09F3EPJXSQ"


async def _resolve_callie_channel(session: AsyncSession) -> str:
    result = await session.execute(
        select(Integration)
        .where(
            Integration.provider == "slack",
            Integration.status == "active",
            Integration.agent_id == "callie",
        )
        .order_by(Integration.connected_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return settings.marketing_campaigns_slack_channel.strip() or _CALLIE_DEFAULT_CHANNEL

    metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
    allowed = metadata.get("allowed_channel_ids")
    if isinstance(allowed, list):
        for item in allowed:
            channel = _normalize_text(item)
            if channel:
                return channel
    if isinstance(allowed, str):
        channel = _normalize_text(allowed.split(",", 1)[0])
        if channel:
            return channel
    return settings.marketing_campaigns_slack_channel.strip() or _CALLIE_DEFAULT_CHANNEL


def _render_followup_text(commitment: Commitment) -> str:
    lines = [f"Commitment follow-up [C{commitment.id}]: {commitment.text}"]
    if commitment.due is not None:
        lines.append(f"Due: {commitment.due.astimezone(UTC).date().isoformat()}.")
    lines.append(
        f"Reply 'done {commitment.id}' to close it, "
        f"'snooze {commitment.id} 2d' to snooze it, "
        f"or 'dismiss {commitment.id}' to drop it as not relevant."
    )
    return str(lint_agent_text("\n".join(lines)))


async def send_commitment_followups(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> CommitmentFollowupSummary:
    """Send deterministic proactive commitment follow-ups."""
    current_time = now or datetime.now(UTC)
    await repo.reactivate_expired_snoozes(session, now=current_time)
    await session.flush()

    due_soon_cutoff = current_time + timedelta(hours=settings.commitments_due_soon_hours)
    renotify_cutoff = current_time - timedelta(hours=settings.commitments_renotify_hours)
    rows = await repo.list_commitment_followup_candidates(
        session,
        now=current_time,
        due_soon_cutoff=due_soon_cutoff,
        renotify_cutoff=renotify_cutoff,
    )

    checked = len(rows)
    eligible = 0
    sent = 0
    failed = 0

    for commitment in rows:
        eligible += 1
        text = _render_followup_text(commitment)
        try:
            if commitment.sensitivity == "marketing":
                token = await _get_slack_token_for_agent(session, agent_id="callie")
                if not token:
                    raise RuntimeError("No active Slack token found for agent_id='callie'")
                channel_id = await _resolve_callie_channel(session)
                await SlackClient(token=token).post_message(channel=channel_id, text=text)
            else:
                token = await _get_slack_token_for_agent(session, agent_id="artemis")
                if not token:
                    raise RuntimeError("No active Slack token found for agent_id='artemis'")
                recipient_id = await _resolve_artemis_dm_recipient(session)
                await SlackClient(token=token).post_dm(user=recipient_id, text=text)
            await repo.mark_commitment_notified(
                session,
                commitment_id=commitment.id,
                notified_at=current_time,
            )
            await session.commit()
            sent += 1
        except Exception:
            logger.exception("Commitment follow-up failed for commitment_id=%s", commitment.id)
            await session.rollback()
            failed += 1

    return CommitmentFollowupSummary(
        checked=checked,
        eligible=eligible,
        sent=sent,
        failed=failed,
    )


def _parse_snooze_duration(text: str) -> timedelta:
    match = _SNOOZE_RE.match(text.strip())
    if match is None:
        return timedelta(hours=settings.commitments_default_snooze_hours)
    amount = int(match.group(2) or 0) or 0
    unit = (match.group(3) or "h").lower()
    if amount <= 0:
        return timedelta(hours=settings.commitments_default_snooze_hours)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    return timedelta(hours=amount)


async def try_apply_commitment_reply(
    session: AsyncSession,
    *,
    text: str,
) -> str | None:
    """Apply a deterministic done/snooze reply if the message matches."""
    current_time = datetime.now(UTC)
    normalized = text.strip()

    done_match = _DONE_RE.match(normalized)
    if done_match:
        commitment_id = int(done_match.group(1))
        commitment = await repo.mark_commitment_done(
            session,
            commitment_id=commitment_id,
            now=current_time,
        )
        if commitment is None:
            return f"Commitment C{commitment_id} was not found."
        await session.commit()
        return f"Marked commitment C{commitment_id} done."

    snooze_match = _SNOOZE_RE.match(normalized)
    if snooze_match:
        commitment_id = int(snooze_match.group(1))
        duration = _parse_snooze_duration(normalized)
        until = current_time + duration
        commitment = await repo.snooze_commitment(
            session,
            commitment_id=commitment_id,
            snoozed_until=until,
            now=current_time,
        )
        if commitment is None:
            return f"Commitment C{commitment_id} was not found."
        await session.commit()
        return (
            f"Snoozed commitment C{commitment_id} until {until.astimezone(UTC).date().isoformat()}."
        )

    dismiss_match = _DISMISS_RE.match(normalized)
    if dismiss_match:
        commitment_id = int(dismiss_match.group(1))
        commitment = await repo.dismiss_commitment(
            session,
            commitment_id=commitment_id,
            now=current_time,
        )
        if commitment is None:
            return f"Commitment C{commitment_id} was not found."
        await session.commit()
        return f"Dismissed commitment C{commitment_id}. No further follow-up."

    return None
