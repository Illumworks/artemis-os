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
from artemis.identity.scope_policy import OWNER_EMAIL
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

# ── Proposals digest reply parsers ────────────────────────────────────────────
# Deterministic, no LLM.  Patterns:
#   track 1,3          -> approve items 1 and 3
#   track 1 3 2        -> approve items 1, 2, 3 (space-separated)
#   track all          -> approve all
#   track none / skip  -> leave all as proposed
_TRACK_NUMS_RE = re.compile(
    r"^track\s+([0-9][0-9,\s]*)\s*$",
    re.IGNORECASE,
)
_TRACK_ALL_RE = re.compile(r"^track\s+all\s*$", re.IGNORECASE)
_TRACK_NONE_RE = re.compile(r"^(?:track\s+none|skip)\s*$", re.IGNORECASE)

# How many proposed items to surface in one digest (prevents wall-of-text).
_PROPOSALS_DIGEST_LIMIT = 10
# Breadcrumb TTL in hours.
_PROPOSALS_BREADCRUMB_TTL_HOURS = 48


@dataclass(frozen=True)
class ProposalsDigestSummary:
    proposed_count: int
    sent: bool
    dry_run: bool
    payload: str  # the digest text (useful for dry_run inspection)


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


async def _resolve_canonical_owner_user_id(session: AsyncSession) -> int | None:
    """Return the user.id for the canonical system owner (jon.fila@).

    Uses the same lookup path as _resolve_owner_user_id so the resolution
    is consistent.  Returns None if the owner user row does not yet exist
    (e.g. in a fresh test DB).
    """
    return await _resolve_owner_user_id(session, OWNER_EMAIL)


async def ingest_meeting_commitments(
    session: AsyncSession,
    *,
    granola_id: str,
    title: str,
    action_items: list[dict[str, Any]],
    now: datetime | None = None,
) -> CommitmentIngestSummary:
    """Upsert meeting action items into commitments + mirrored memory observations.

    Opt-in gate (Phase 1): a commitment is only created when BOTH conditions hold:
    1. The item's owner resolves to the system owner (jon.fila@).
    2. The item has a deadline (due_value is not None).

    Items that fail the gate are still mirrored to memory (lossless invariant) —
    they just don't produce a commitment row and therefore can never auto-fire.

    When the gate passes, the commitment lands as ``status='proposed'`` so it
    cannot trigger follow-up notifications until the owner explicitly approves it.
    """
    current_time = now or datetime.now(UTC)
    seen = 0
    inserted = 0
    deduped = 0

    # Resolve once per ingest call to avoid N DB round-trips.
    canonical_owner_user_id = await _resolve_canonical_owner_user_id(session)

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

        # ── Opt-in gate: only create a commitment when owner IS the system owner
        # AND the item has a deadline.  Items that miss either condition are
        # still written to memory below (lossless), but do NOT get a commitment.
        owner_is_owner = (
            canonical_owner_user_id is not None and owner_user_id == canonical_owner_user_id
        )
        had_due = due_value is not None

        if owner_is_owner and had_due:
            commitment, created = await repo.upsert_commitment(
                session,
                source_type="granola_meeting",
                source_id=granola_id,
                text=text,
                owner_user_id=owner_user_id,
                due=due_value,
                sensitivity=sensitivity,
                status="proposed",
            )
            if created:
                inserted += 1
            else:
                deduped += 1
            commitment_id: int | None = commitment.id
        else:
            logger.debug(
                "ingest_meeting_commitments: skipping commitment creation "
                "(owner_is_owner=%s, had_due=%s) for item=%r granola_id=%s",
                owner_is_owner,
                had_due,
                text[:60],
                granola_id,
            )
            commitment_id = None

        # Memory observation is ALWAYS written — lossless invariant.
        scope = _agent_scope_for(sensitivity)
        raw_payload = {
            "granola_id": granola_id,
            "meeting_title": title,
            "commitment_id": commitment_id,
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


async def _build_decision_features(
    session: AsyncSession,
    commitment: Commitment,
) -> dict[str, Any]:
    """Build the feature snapshot for a commitment decision row.

    Features are designed to be sufficient for Phase 2-3 learning:
    - owner_is_owner: was the item owned by the system owner?
    - had_due: did the item have a deadline?
    - sensitivity: personal_ops / marketing
    - source_type: e.g. granola_meeting
    - source_id: granola meeting ID
    """
    canonical_owner_id = await _resolve_canonical_owner_user_id(session)
    owner_is_owner = (
        canonical_owner_id is not None and commitment.owner_user_id == canonical_owner_id
    )
    return {
        "owner_is_owner": owner_is_owner,
        "had_due": commitment.due is not None,
        "sensitivity": commitment.sensitivity,
        "source_type": commitment.source_type,
        "source_id": commitment.source_id,
    }


async def approve_commitment(
    session: AsyncSession,
    commitment_id: int,
) -> Commitment | None:
    """Promote a proposed commitment to active and record the approval signal.

    Intended for use by the Slack digest reply handler (Phase 1 follow-on).
    Returns the updated Commitment row, or None if not found.
    Caller is responsible for session.commit().
    """
    commitment = await repo.get_commitment(session, commitment_id)
    if commitment is None:
        return None
    features = await _build_decision_features(session, commitment)
    return await repo.approve_commitment(
        session,
        commitment_id=commitment_id,
        features=features,
    )


async def dismiss_commitment(
    session: AsyncSession,
    commitment_id: int,
) -> Commitment | None:
    """Move a commitment to dismissed and record the dismiss signal.

    Complements ``try_apply_commitment_reply`` which handles text-based dismiss
    commands.  This function is for programmatic dismiss from the Slack digest
    handler (Phase 1 follow-on), and captures the learning-signal row.
    Returns the updated Commitment row, or None if not found.
    Caller is responsible for session.commit().
    """
    commitment = await repo.get_commitment(session, commitment_id)
    if commitment is None:
        return None
    features = await _build_decision_features(session, commitment)
    return await repo.dismiss_commitment_with_decision(
        session,
        commitment_id=commitment_id,
        features=features,
    )


def _render_proposals_digest(
    commitments: list[Commitment],
    *,
    now: datetime,
) -> tuple[str, dict[str, int]]:
    """Render the numbered digest text and commitment_map {num_str -> commitment_id}.

    Returns (text, commitment_map).
    commitment_map keys are str (e.g. "1") so they round-trip cleanly through JSONB.
    """
    local_tz = ZoneInfo(settings.morning_brief_tz)
    lines: list[str] = [
        "Caught these commitments from your recent meetings. "
        "Reply 'track 1,3', 'track all', or 'track none' to decide which to follow up on."
    ]
    commitment_map: dict[str, int] = {}
    for idx, c in enumerate(commitments, start=1):
        num = str(idx)
        commitment_map[num] = c.id
        due_label = ""
        if c.due is not None:
            local_due = c.due.astimezone(local_tz)
            due_label = f" (due {local_due.date().isoformat()})"
        lines.append(f"{idx}. {c.text}{due_label}")
    text = "\n".join(lines)
    return str(lint_agent_text(text)), commitment_map


async def send_commitment_proposals_digest(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ProposalsDigestSummary:
    """Gather proposed commitments and DM Jon a numbered roundup digest.

    Idempotency guard: if an unanswered breadcrumb exists from <24h ago, skip
    (don't re-spam the same list).  Jon's reply handles the breadcrumb; if he
    never replies the breadcrumb expires in 48h and a fresh digest fires next day.

    If ``dry_run=True``, skip the actual Slack send and breadcrumb write; just
    return the payload text.  Useful for offline inspection / unit testing.

    Selected -> ``active`` via approve_commitment; unselected stay ``proposed``
    (never auto-dismissed).  Caller is responsible for session.commit() on the
    outer transaction if needed — internally this function commits after the
    breadcrumb is created.
    """
    current_time = now or datetime.now(UTC)
    recipient_id = await _resolve_artemis_dm_recipient(session)

    # ── Idempotency guard: skip if there is already a live unanswered breadcrumb ──
    existing_crumb = await repo.get_live_proposals_breadcrumb(session, recipient_id)
    if existing_crumb is not None:
        # Guard: only skip if the existing breadcrumb was created less than 24h ago.
        age = (
            current_time - existing_crumb.created_at.replace(tzinfo=UTC)
            if existing_crumb.created_at.tzinfo is None
            else current_time - existing_crumb.created_at
        )
        if age.total_seconds() < 86400:
            logger.info(
                "send_commitment_proposals_digest: unanswered digest already exists "
                "(breadcrumb_id=%d, created=%s, age=%.1fh) -- skipping",
                existing_crumb.id,
                existing_crumb.created_at.isoformat(),
                age.total_seconds() / 3600,
            )
            return ProposalsDigestSummary(
                proposed_count=0,
                sent=False,
                dry_run=dry_run,
                payload="",
            )

    # ── Gather proposed commitments ───────────────────────────────────────────
    proposed = await repo.list_proposed_commitments(
        session,
        limit=_PROPOSALS_DIGEST_LIMIT,
    )
    if not proposed:
        logger.info("send_commitment_proposals_digest: no proposed commitments -- nothing to send")
        return ProposalsDigestSummary(
            proposed_count=0,
            sent=False,
            dry_run=dry_run,
            payload="",
        )

    # ── Render ────────────────────────────────────────────────────────────────
    digest_text, commitment_map = _render_proposals_digest(proposed, now=current_time)

    if dry_run:
        return ProposalsDigestSummary(
            proposed_count=len(proposed),
            sent=False,
            dry_run=True,
            payload=digest_text,
        )

    # ── Send via Artemis bot ──────────────────────────────────────────────────
    token = await _get_slack_token_for_agent(session, agent_id="artemis")
    if not token:
        raise RuntimeError("No active Slack token found for agent_id='artemis'")

    await SlackClient(token=token).post_dm(user=recipient_id, text=digest_text)

    # ── Leave breadcrumb (DB-backed, TTL 48h) ─────────────────────────────────
    expires_at = current_time + timedelta(hours=_PROPOSALS_BREADCRUMB_TTL_HOURS)
    await repo.create_commitment_proposals_breadcrumb(
        session,
        recipient_id=recipient_id,
        commitment_map=commitment_map,
        proposal_text=digest_text,
        expires_at=expires_at,
    )
    await session.commit()

    logger.info(
        "send_commitment_proposals_digest: sent digest with %d items to %s (commitment_ids=%s)",
        len(proposed),
        recipient_id,
        [c.id for c in proposed],
    )
    return ProposalsDigestSummary(
        proposed_count=len(proposed),
        sent=True,
        dry_run=False,
        payload=digest_text,
    )


async def try_apply_proposals_reply(
    session: AsyncSession,
    *,
    text: str,
    slack_user_id: str,
) -> str | None:
    """Deterministically handle a DM reply to the proposals digest.

    Checks whether *slack_user_id* has a live proposals breadcrumb.  If yes,
    parses the reply with a keyword classifier (no LLM):
      - ``track <nums>``  -> approve those items, leave the rest as proposed
      - ``track all``     -> approve all items in the digest
      - ``track none`` / ``skip`` -> leave all as proposed (no action)

    Returns a confirmation string if the reply was handled, or ``None`` if
    there is no live breadcrumb / the message doesn't match.

    Caller is responsible for session.commit() after this returns a non-None
    value (approvals may be pending in the session).
    """
    crumb = await repo.get_live_proposals_breadcrumb(session, slack_user_id)
    if crumb is None:
        return None

    normalized = text.strip()

    # ── Classify reply ────────────────────────────────────────────────────────
    if _TRACK_NONE_RE.match(normalized):
        # Leave everything as proposed — just complete the breadcrumb.
        await repo.complete_proposals_breadcrumb(session, crumb.id)
        await session.commit()
        logger.info(
            "try_apply_proposals_reply: track none -- all %d items stay proposed (user=%s)",
            len(crumb.commitment_map),
            slack_user_id,
        )
        return "Got it. I'll leave those for now — they'll appear in the next digest."

    # Map of valid numbers from the breadcrumb.
    commitment_map: dict[str, int] = {
        str(k): int(v) for k, v in (crumb.commitment_map or {}).items() if str(k).isdigit()
    }
    all_ids = list(commitment_map.values())

    if _TRACK_ALL_RE.match(normalized):
        selected_ids = all_ids
    elif _TRACK_NUMS_RE.match(normalized):
        # Parse comma-and-space separated numbers.
        raw = _TRACK_NUMS_RE.match(normalized).group(1)  # type: ignore[union-attr]
        tokens = re.split(r"[,\s]+", raw.strip())
        selected_nums: list[str] = [t.strip() for t in tokens if t.strip().isdigit()]
        selected_ids = [commitment_map[n] for n in selected_nums if n in commitment_map]
        if not selected_ids and selected_nums:
            # Numbers mentioned but none valid — likely a mis-parse.
            return None
    else:
        # Not a digest reply syntax — don't consume the breadcrumb.
        return None

    # ── Approve selected, leave others as proposed ────────────────────────────
    approved_ids: list[int] = []
    for cid in selected_ids:
        result = await approve_commitment(session, cid)
        if result is not None:
            approved_ids.append(cid)

    await repo.complete_proposals_breadcrumb(session, crumb.id)
    await session.commit()

    left_count = len(all_ids) - len(approved_ids)
    logger.info(
        "try_apply_proposals_reply: approved %d commitment(s) %s; %d left as proposed (user=%s)",
        len(approved_ids),
        approved_ids,
        left_count,
        slack_user_id,
    )

    if not approved_ids:
        return "No valid items found. Nothing changed."

    approved_word = "commitment" if len(approved_ids) == 1 else "commitments"
    if left_count == 0:
        return f"Tracking {len(approved_ids)} {approved_word}. I'll follow up as they come due."
    elif left_count == 1:
        return f"Tracking {len(approved_ids)} {approved_word}. The other 1 I'll leave for now."
    else:
        return (
            f"Tracking {len(approved_ids)} {approved_word}. "
            f"The other {left_count} I'll leave for now."
        )
