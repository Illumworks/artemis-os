"""Pre-meeting prep assembly for Artemis proactivity.

Gathers context ahead of a calendar event so Jon walks in ready:
  - attendees (names / emails)
  - related open commitments (from the commitments table)
  - relevant memory observations (via FTS on meeting title + attendees)

All functions are pure/dependency-injectable so they are unit-testable
without a live GCal or Slack connection.

Scheduled: fires on ``pre_meeting_prep_cron`` (default: every 30 min on
weekdays). Each run scans today's upcoming events and fires a prep DM for
any event starting within the lookahead window that has not already had
a prep sent (dedup via memory observation in scope agent:floating-artemis).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

# How far ahead to fire the prep DM (minutes before the event starts).
PRE_MEETING_LOOKAHEAD_MINUTES: int = 30
# If an event starts in less than this many minutes it is too late to prep.
PRE_MEETING_TOO_LATE_MINUTES: int = 5
# Dedup tag prefix in memory scope agent:floating-artemis.
_PREP_SENT_PREFIX: str = "pre_meeting_prep_sent:"


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class UpcomingEvent:
    """A calendar event normalised to the fields we care about for prep."""

    event_id: str
    title: str
    start_utc: datetime
    end_utc: datetime | None
    attendees: list[str]  # email addresses
    description: str | None = None


@dataclass
class MeetingPrepContext:
    """Assembled context for one upcoming meeting."""

    event: UpcomingEvent
    related_commitments: list[dict[str, Any]] = field(default_factory=list)
    memory_snippets: list[str] = field(default_factory=list)


# ── Pure helpers ──────────────────────────────────────────────────────────────


def filter_events_needing_prep(
    events: list[UpcomingEvent],
    *,
    now: datetime,
    already_sent: set[str],
    lookahead_minutes: int = PRE_MEETING_LOOKAHEAD_MINUTES,
    too_late_minutes: int = PRE_MEETING_TOO_LATE_MINUTES,
) -> list[UpcomingEvent]:
    """Return events that are in the lookahead window and haven't had a prep sent.

    Args:
        events: All upcoming events (sorted or unsorted; order preserved).
        now: Current time (UTC-aware).
        already_sent: Set of event_ids that have already received a prep DM.
        lookahead_minutes: How far ahead to fire the prep.
        too_late_minutes: Events starting sooner than this are skipped (too late).

    Returns:
        Filtered list of events that need a prep DM.
    """
    result: list[UpcomingEvent] = []
    for event in events:
        start = event.start_utc
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        minutes_until = (start - now).total_seconds() / 60
        if minutes_until < too_late_minutes:
            continue
        if minutes_until > lookahead_minutes:
            continue
        if event.event_id in already_sent:
            continue
        result.append(event)
    return result


def format_prep_message(ctx: MeetingPrepContext, *, now: datetime) -> str:
    """Render a Slack-ready prep message for a meeting.

    Keeps it tight — only surfaces what's actionable. Returns plain text
    with Slack mrkdwn formatting (bold via *…*).

    Args:
        ctx: Assembled meeting context.
        now: Current time, used for computing "starts in X min" label.

    Returns:
        Formatted Slack message string.
    """
    event = ctx.event
    start = event.start_utc
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    minutes_until = max(0, int((start - now).total_seconds() / 60))

    lines: list[str] = [
        f"*Coming up in {minutes_until} min: {event.title}*",
    ]

    if event.attendees:
        attendee_list = ", ".join(event.attendees[:6])
        if len(event.attendees) > 6:
            attendee_list += f" (+{len(event.attendees) - 6} more)"
        lines.append(f"With: {attendee_list}")

    if ctx.related_commitments:
        lines.append("")
        lines.append("*Open commitments relevant to this meeting:*")
        for c in ctx.related_commitments[:3]:
            text = c.get("text") or ""
            due = c.get("due")
            due_label = f" (due {due})" if due else ""
            lines.append(f"- {text}{due_label}")

    if ctx.memory_snippets:
        lines.append("")
        lines.append("*Context:*")
        for snippet in ctx.memory_snippets[:3]:
            lines.append(f"- {snippet[:120]}")

    return "\n".join(lines)


def prep_dedup_key(event_id: str) -> str:
    """Return the memory observation content that marks a prep as sent.

    This is what we write to scope agent:floating-artemis after sending the
    prep DM, and what we read back to filter already-sent preps.
    """
    return f"{_PREP_SENT_PREFIX}{event_id}"


def parse_already_sent_event_ids(observations: list[Any]) -> set[str]:
    """Extract event_ids from pre_meeting_prep_sent observations.

    Accepts both Observation Pydantic objects (with .content) and plain dicts.

    Args:
        observations: Memory observations from the agent:floating-artemis scope.

    Returns:
        Set of event_ids that already had a prep sent.
    """
    sent: set[str] = set()
    for obs in observations:
        content = (obs.content if hasattr(obs, "content") else obs.get("content", "")) or ""
        if content.startswith(_PREP_SENT_PREFIX):
            event_id = content[len(_PREP_SENT_PREFIX):].strip()
            if event_id:
                sent.add(event_id)
    return sent


# ── I/O helpers (thin adapters over existing infra) ───────────────────────────


async def fetch_today_upcoming_events(session: Any) -> list[UpcomingEvent]:
    """Fetch today's upcoming GCal events as UpcomingEvent objects.

    Returns empty list if GCal is not connected or the call fails (graceful
    degradation — same pattern as _safe_calendar in brief/sources.py).

    Args:
        session: AsyncSession.

    Returns:
        List of UpcomingEvent objects for today's events.
    """
    try:
        from datetime import time

        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.gcal.client import GCalClient

        rows = await repo.list_active(session, provider="gcal")
        if not rows:
            return []

        integration = rows[0]
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        client = GCalClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
        )

        now_utc = datetime.now(UTC)
        today_start = datetime.combine(now_utc.date(), time.min).replace(tzinfo=UTC)
        today_end = datetime.combine(now_utc.date(), time(23, 59, 59)).replace(tzinfo=UTC)

        events = await client.list_events(
            calendar_id="primary",
            time_min=today_start.isoformat(),
            time_max=today_end.isoformat(),
        )

        result: list[UpcomingEvent] = []
        for event in events:
            start_str = event.start.date_time
            if not start_str:
                continue  # all-day events — skip
            try:
                start_dt = datetime.fromisoformat(start_str)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=UTC)
            except (ValueError, AttributeError):
                continue

            end_dt: datetime | None = None
            end_str = event.end.date_time if event.end else None
            if end_str:
                try:
                    end_dt = datetime.fromisoformat(end_str)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=UTC)
                except (ValueError, AttributeError):
                    pass

            attendees = [
                a.email
                for a in (event.attendees or [])
                if getattr(a, "email", None)
            ]

            result.append(
                UpcomingEvent(
                    event_id=event.id or "",
                    title=event.summary or "Untitled event",
                    start_utc=start_dt,
                    end_utc=end_dt,
                    attendees=attendees,
                    description=getattr(event, "description", None),
                )
            )
        return result
    except Exception:
        logger.debug("fetch_today_upcoming_events failed", exc_info=True)
        return []


async def fetch_already_sent_event_ids(session: Any) -> set[str]:
    """Read the agent:floating-artemis scope to find prep-already-sent event IDs.

    Args:
        session: AsyncSession.

    Returns:
        Set of event_ids for which prep has already been sent today.
    """
    try:
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        scope = [Scope(scope_kind="agent", scope_id="floating-artemis")]
        results = await search_observations(
            session=session,
            scope_set=scope,
            query="pre_meeting_prep_sent",
            limit=50,
            modes=["fts"],
        )
        return parse_already_sent_event_ids(results)
    except Exception:
        logger.debug("fetch_already_sent_event_ids failed", exc_info=True)
        return set()


async def fetch_related_commitments(session: Any, *, title: str) -> list[dict[str, Any]]:
    """Return open commitments that might relate to a meeting by title keywords.

    Uses a simple keyword search — no LLM. Returns at most 3 relevant items
    so the prep stays concise.

    Args:
        session: AsyncSession.
        title: Meeting title to extract keywords from.

    Returns:
        List of commitment dicts with at least 'text' and optionally 'due'.
    """
    try:
        from sqlalchemy import or_, select

        from artemis.proactivity.models import Commitment

        # Extract meaningful words (3+ chars) from the title.
        import re
        keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", title) if w.lower() not in {
            "the", "and", "for", "with", "that", "this", "are", "was", "its",
            "from", "have", "has", "had", "not", "but", "what", "all", "when",
        }][:5]

        if not keywords:
            return []

        stmt = select(Commitment).where(
            Commitment.status == "active",
        )
        rows = (await session.execute(stmt)).scalars().all()

        # Score by keyword overlap with commitment text.
        scored: list[tuple[int, Commitment]] = []
        for c in rows:
            text_lower = c.text.lower()
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        result: list[dict[str, Any]] = []
        for _, c in scored[:3]:
            item: dict[str, Any] = {"text": c.text}
            if c.due is not None:
                item["due"] = c.due.astimezone(UTC).date().isoformat()
            result.append(item)
        return result
    except Exception:
        logger.debug("fetch_related_commitments failed", exc_info=True)
        return []


async def fetch_memory_snippets(session: Any, *, query: str) -> list[str]:
    """Fetch relevant memory observations for a meeting query.

    Args:
        session: AsyncSession.
        query: Search query (e.g. meeting title + attendees).

    Returns:
        List of observation content strings (at most 3).
    """
    try:
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        scope = [Scope(scope_kind="agent", scope_id="floating-artemis")]
        results = await search_observations(
            session=session,
            scope_set=scope,
            query=query,
            limit=5,
            modes=["fts"],
        )
        snippets: list[str] = []
        for obs in results:
            content = (obs.content if hasattr(obs, "content") else obs.get("content", "")) or ""
            # Skip dedup/system observations (pre_meeting_prep_sent, brief_exclusion, etc.)
            if any(content.startswith(pfx) for pfx in (_PREP_SENT_PREFIX, "brief_exclusion:", "brief_reaction:")):
                continue
            if content:
                snippets.append(content)
            if len(snippets) >= 3:
                break
        return snippets
    except Exception:
        logger.debug("fetch_memory_snippets failed", exc_info=True)
        return []


async def mark_prep_sent(session: Any, *, event_id: str) -> None:
    """Write a dedup observation so this event doesn't get prepped again today.

    Uses the same memory pattern as brief_exclusion in brief/sources.py.

    Args:
        session: AsyncSession (caller must commit).
        event_id: GCal event ID.
    """
    from artemis.memory.schemas import Scope, SourceQualityHint
    from artemis.memory.store import write_observation

    scope = Scope(scope_kind="agent", scope_id="floating-artemis")
    await write_observation(
        session,
        scope=scope,
        content=prep_dedup_key(event_id),
        category="convention",
        source_quality=SourceQualityHint.agent,
        raw_source_kind="pre_meeting_prep",
        raw_source_id=event_id,
        raw_actor="artemis-proactivity",
    )


async def assemble_prep_context(
    session: Any,
    *,
    event: UpcomingEvent,
) -> MeetingPrepContext:
    """Gather commitments + memory snippets for a meeting.

    Args:
        session: AsyncSession.
        event: The upcoming event to prep for.

    Returns:
        Populated MeetingPrepContext.
    """
    import asyncio

    query = event.title
    if event.attendees:
        query += " " + " ".join(event.attendees[:3])

    related_commitments, memory_snippets = await asyncio.gather(
        fetch_related_commitments(session, title=event.title),
        fetch_memory_snippets(session, query=query),
        return_exceptions=True,
    )

    if isinstance(related_commitments, BaseException):
        logger.debug("assemble_prep_context: commitments failed", exc_info=True)
        related_commitments = []
    if isinstance(memory_snippets, BaseException):
        logger.debug("assemble_prep_context: memory snippets failed", exc_info=True)
        memory_snippets = []

    return MeetingPrepContext(
        event=event,
        related_commitments=list(related_commitments),
        memory_snippets=list(memory_snippets),
    )
