"""Post-meeting scheduling action execution (v1).

Jon's vision: a meeting produces an action item like *"schedule a Writing
Studio training for my team."*  Artemis does the legwork — classifies the
item as schedule-able, finds candidate times (attempting coworkers' free/busy,
degrading gracefully when it can't read them), then PROPOSES to Jon via his
Artemis DM ("here are 3 times that work — want me to send the invite?").  On
Jon's conversational OK, the existing agency gate creates the event + invites.

This REPLACES the (disabled) pre-meeting prep — the value is post-meeting
action execution, not pre-meeting context.

Design
------
- Detection is an LLM classify over each meeting action item.  It is the ONLY
  LLM call; everything downstream is deterministic so it is unit-testable.
- Slot-finding queries Google free/busy for Jon + any resolvable attendee
  calendars.  Coworker calendars that can't be read (Google returns an
  ``errors`` entry or omits them) are flagged "pending their availability"
  and the slots fall back to Jon's free time only.  We NEVER treat an
  unreadable calendar as "free".
- Proposing + executing reuses the agency gate
  (``propose_action`` → ``send_proposal_dm`` → yes/no reply →
  ``execute_proposed_action`` → ``calendar.create``).  We do NOT reinvent the
  send-as-Jon path; creating an invite to coworkers IS a send-as-Jon action
  and therefore goes through that gate + Jon's explicit confirmation.
- PROPOSE-ONLY by default.  Nothing is ever auto-created.

All pure helpers take their inputs as arguments (no hidden I/O) so the
classify→slot→propose pipeline is testable without a live GCal/Slack/LLM.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

# Default meeting length when the action item doesn't state one.
DEFAULT_DURATION_MINUTES: int = 60
# How many candidate slots to surface to Jon.
DEFAULT_SLOT_COUNT: int = 3
# Working-hours window (local time) we'll propose inside.
WORK_DAY_START_HOUR: int = 9
WORK_DAY_END_HOUR: int = 17
# How far ahead to look for slots when the timeframe is vague.
DEFAULT_SEARCH_HORIZON_DAYS: int = 10
# Dedup tag prefix in memory scope agent:floating-artemis (one proposal per item).
_SCHEDULED_PREFIX: str = "post_meeting_schedule_proposed:"
# Confidence below which we ignore the classifier verdict entirely.
MIN_DETECTION_CONFIDENCE: float = 0.6


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SchedulingIntent:
    """The structured result of classifying one action item.

    ``is_scheduling`` False means "not a scheduling request" — the other
    fields are then meaningless and should be ignored.
    """

    is_scheduling: bool
    title: str = ""
    attendees: list[str] = field(default_factory=list)  # names or emails as stated
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    timeframe: str = ""  # free text e.g. "next week", "this Friday"
    confidence: float = 0.0
    # The classifier's own read of who owns this item, given the ``owner``
    # context we now pass into the prompt: True only if it believes the
    # commitment belongs to Jon (the account owner), False otherwise/unstated.
    # This is a corroborating signal ONLY — the authoritative eligibility gate
    # is the deterministic owner resolution in run_post_meeting_scheduling_sweep
    # (mirrors artemis.proactivity.commitments.ingest_meeting_commitments).
    owner_is_operator: bool = False


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CandidateSlot:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class SlotProposal:
    """Everything needed to render a proposal + build the calendar.create payload."""

    intent: SchedulingIntent
    slots: list[CandidateSlot]
    resolved_attendees: list[str]  # emails we could resolve + will invite
    unresolved_attendees: list[str]  # names we couldn't map to an email
    availability_pending: list[str]  # attendee emails whose free/busy we couldn't read


# ── Detection (LLM classify) ──────────────────────────────────────────────────

_DETECTION_SYSTEM = (
    "You classify a single meeting action item. Decide whether it is a request "
    "to SCHEDULE a meeting, training, session, or call with other people "
    "(something that becomes a calendar event with attendees). "
    "Pure reminders, tasks, doc edits, or follow-ups that are NOT a calendar "
    "event are NOT scheduling.\n\n"
    "You are also told who the item's owner is (the person who committed to "
    "it). 'Jon' / 'Me' refers to the account owner using this system; anyone "
    "else is a different person.\n\n"
    "Return ONLY a JSON object, no prose, with keys:\n"
    '  is_scheduling (bool)\n'
    '  title (str: a concise event title, e.g. "Writing Studio training")\n'
    '  attendees (array of str: people/teams named, e.g. ["my team", "Angela"]; '
    "[] if none named)\n"
    '  duration_minutes (int: stated length, else 60)\n'
    '  timeframe (str: when, e.g. "next week"; "" if unstated)\n'
    '  confidence (number 0..1)\n'
    '  owner_is_operator (bool: true ONLY if the stated owner is Jon/"Me" '
    "(the account owner) — false if the owner is someone else or unstated)\n"
)


def build_detection_prompt(
    *, action_item_text: str, meeting_title: str, owner: str | None = None
) -> str:
    """Render the user prompt for the detection classify call (pure)."""
    owner_line = f'Owner (who committed to this): "{owner}"' if owner else (
        "Owner (who committed to this): unstated"
    )
    return (
        f"Meeting: {meeting_title}\n"
        f'Action item: "{action_item_text}"\n'
        f"{owner_line}\n\n"
        "Classify this action item. Return only the JSON object."
    )


def parse_detection_response(raw: str) -> SchedulingIntent:
    """Parse the classifier's JSON into a SchedulingIntent (pure, defensive).

    Tolerates code fences and surrounding prose. On any parse failure returns a
    non-scheduling intent so a bad LLM response can never trigger an action.
    """
    text = (raw or "").strip()
    # Strip ```json … ``` fences if present.
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    if not fence:
        return SchedulingIntent(is_scheduling=False)
    try:
        data = json.loads(fence.group(0))
    except (ValueError, TypeError):
        return SchedulingIntent(is_scheduling=False)
    if not isinstance(data, dict):
        return SchedulingIntent(is_scheduling=False)

    is_scheduling = bool(data.get("is_scheduling"))
    try:
        confidence = float(data.get("confidence", 0.0))
    except (ValueError, TypeError):
        confidence = 0.0

    attendees_raw = data.get("attendees") or []
    attendees = [
        str(a).strip()
        for a in attendees_raw
        if isinstance(a, (str, int, float)) and str(a).strip()
    ] if isinstance(attendees_raw, list) else []

    try:
        duration = int(data.get("duration_minutes") or DEFAULT_DURATION_MINUTES)
    except (ValueError, TypeError):
        duration = DEFAULT_DURATION_MINUTES
    if duration <= 0:
        duration = DEFAULT_DURATION_MINUTES

    return SchedulingIntent(
        is_scheduling=is_scheduling,
        title=str(data.get("title") or "").strip(),
        attendees=attendees,
        duration_minutes=duration,
        timeframe=str(data.get("timeframe") or "").strip(),
        confidence=confidence,
        owner_is_operator=bool(data.get("owner_is_operator")),
    )


async def classify_action_item(
    *,
    action_item_text: str,
    meeting_title: str,
    adapter: Any,
    owner: str | None = None,
) -> SchedulingIntent:
    """Run the LLM detection classify for one action item.

    ``adapter`` is a ModelAdapter (artemis.agent.client). Returns a
    non-scheduling intent on any failure — fail closed.

    ``owner`` is the action item's structured owner label (if any), passed
    through for prompt context / the ``owner_is_operator`` signal. It is NOT
    the authoritative eligibility check — callers must still gate on the
    deterministic owner resolution (see run_post_meeting_scheduling_sweep).
    """
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock

    try:
        prompt = build_detection_prompt(
            action_item_text=action_item_text, meeting_title=meeting_title, owner=owner
        )
        response = await adapter.complete(
            CompletionRequest(
                messages=[Message(role="user", content=[TextBlock(text=prompt)])],
                system=_DETECTION_SYSTEM,
                max_tokens=300,
                reasoning_effort="low",
                cache_system=False,
                cache_tools=False,
            )
        )
        flat = _flatten_blocks(response.message.content)
        intent = parse_detection_response(flat)
    except Exception:
        logger.warning("post_meeting_scheduling: detection classify failed", exc_info=True)
        return SchedulingIntent(is_scheduling=False)

    # Confidence gate — a low-confidence "yes" is treated as "no".
    if intent.is_scheduling and intent.confidence < MIN_DETECTION_CONFIDENCE:
        return SchedulingIntent(is_scheduling=False)
    return intent


def _flatten_blocks(blocks: Any) -> str:
    parts: list[str] = []
    for block in blocks or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


# ── Timeframe → search window (pure) ──────────────────────────────────────────


def resolve_search_window(
    timeframe: str,
    *,
    now: datetime,
    tz: ZoneInfo,
    horizon_days: int = DEFAULT_SEARCH_HORIZON_DAYS,
) -> tuple[datetime, datetime]:
    """Map a free-text timeframe to a (start, end) UTC search window.

    Starts no earlier than tomorrow (we never propose a same-day scramble).
    Falls back to a horizon window when the timeframe is vague/empty.
    """
    local_now = now.astimezone(tz)
    value = " ".join((timeframe or "").lower().split())

    # Default: tomorrow through horizon.
    start_date = local_now.date() + timedelta(days=1)
    end_date = start_date + timedelta(days=horizon_days)

    if "tomorrow" in value:
        start_date = local_now.date() + timedelta(days=1)
        end_date = start_date + timedelta(days=1)
    elif "this week" in value:
        start_date = local_now.date() + timedelta(days=1)
        days_until_sunday = 6 - local_now.weekday()
        end_date = local_now.date() + timedelta(days=max(1, days_until_sunday))
    elif "next week" in value:
        days_until_next_monday = (7 - local_now.weekday()) % 7 or 7
        start_date = local_now.date() + timedelta(days=days_until_next_monday)
        end_date = start_date + timedelta(days=6)

    start_local = datetime.combine(start_date, time(0, 0), tzinfo=tz)
    end_local = datetime.combine(end_date, time(23, 59, 59), tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


# ── Slot finder (pure) ────────────────────────────────────────────────────────


def find_free_slots(
    *,
    busy: list[BusyInterval],
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    tz: ZoneInfo,
    count: int = DEFAULT_SLOT_COUNT,
    work_start_hour: int = WORK_DAY_START_HOUR,
    work_end_hour: int = WORK_DAY_END_HOUR,
) -> list[CandidateSlot]:
    """Find up to ``count`` open slots inside working hours that avoid all busy.

    Pure: ``busy`` is the merged busy set across whatever calendars we could
    read. Walks each weekday in the window in 30-min steps, returning the first
    ``count`` non-conflicting slots. Skips weekends.
    """
    duration = timedelta(minutes=max(1, duration_minutes))
    merged = _merge_intervals(busy)
    slots: list[CandidateSlot] = []

    # Iterate day by day in local time so working hours line up.
    day = window_start.astimezone(tz).date()
    last_day = window_end.astimezone(tz).date()
    step = timedelta(minutes=30)

    while day <= last_day and len(slots) < count:
        if day.weekday() >= 5:  # 5=Sat, 6=Sun
            day += timedelta(days=1)
            continue
        cursor = datetime.combine(day, time(work_start_hour, 0), tzinfo=tz).astimezone(UTC)
        day_end = datetime.combine(day, time(work_end_hour, 0), tzinfo=tz).astimezone(UTC)
        while cursor + duration <= day_end and len(slots) < count:
            slot_start = cursor
            slot_end = cursor + duration
            if slot_start < window_start:
                cursor += step
                continue
            if slot_end > window_end:
                break
            if not _conflicts(slot_start, slot_end, merged):
                slots.append(CandidateSlot(start=slot_start, end=slot_end))
                # Jump past this slot so candidates don't overlap each other.
                cursor = slot_end
                continue
            cursor += step
        day += timedelta(days=1)

    return slots


def _merge_intervals(intervals: list[BusyInterval]) -> list[BusyInterval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda b: b.start)
    merged: list[BusyInterval] = [ordered[0]]
    for cur in ordered[1:]:
        last = merged[-1]
        if cur.start <= last.end:
            if cur.end > last.end:
                merged[-1] = BusyInterval(start=last.start, end=cur.end)
        else:
            merged.append(cur)
    return merged


def _conflicts(start: datetime, end: datetime, merged: list[BusyInterval]) -> bool:
    return any(start < b.end and end > b.start for b in merged)


# ── Free/busy response parsing (pure) ─────────────────────────────────────────


def parse_freebusy_response(
    data: dict[str, Any],
    *,
    requested_calendars: list[str],
) -> tuple[list[BusyInterval], list[str]]:
    """Parse a Google freeBusy response into (busy_intervals, unreadable_ids).

    ``unreadable_ids`` are calendars Google reported an error for OR omitted —
    their availability is UNKNOWN (treated as pending, never as free).
    """
    busy: list[BusyInterval] = []
    unreadable: list[str] = []
    calendars = data.get("calendars") or {}
    if not isinstance(calendars, dict):
        calendars = {}

    for cid in requested_calendars:
        entry = calendars.get(cid)
        if not isinstance(entry, dict) or entry.get("errors"):
            unreadable.append(cid)
            continue
        for b in entry.get("busy") or []:
            try:
                start = datetime.fromisoformat(str(b["start"]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(b["end"]).replace("Z", "+00:00"))
            except (KeyError, ValueError, TypeError):
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
            busy.append(BusyInterval(start=start.astimezone(UTC), end=end.astimezone(UTC)))

    return busy, unreadable


# ── Attendee resolution ───────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def split_resolved_attendees(
    attendees: list[str],
) -> tuple[list[str], list[str]]:
    """Split stated attendees into (emails, non-email-labels) — pure, no DB.

    Anything already an email passes through. Non-emails (names, "my team") are
    returned as unresolved labels; the I/O layer may attempt a directory lookup
    but v1 falls back to flagging them.
    """
    emails: list[str] = []
    labels: list[str] = []
    for a in attendees:
        a = a.strip()
        if not a:
            continue
        if _EMAIL_RE.match(a):
            emails.append(a)
        else:
            labels.append(a)
    return emails, labels


# ── Proposal rendering (pure) ─────────────────────────────────────────────────


def format_slot_label(slot: CandidateSlot, *, tz: ZoneInfo) -> str:
    """Human label, e.g. 'Tue Jun 24, 10:00–11:00 AM'."""
    start_local = slot.start.astimezone(tz)
    end_local = slot.end.astimezone(tz)
    day = start_local.strftime("%a %b %-d") if hasattr(start_local, "strftime") else ""
    start_t = start_local.strftime("%-I:%M")
    end_t = end_local.strftime("%-I:%M %p")
    return f"{day}, {start_t}–{end_t}"


def format_proposal_message(proposal: SlotProposal, *, tz: ZoneInfo) -> str:
    """Render the conversational DM Jon receives (no buttons).

    Returns empty string if there are no slots to offer (caller skips).
    """
    intent = proposal.intent
    if not proposal.slots:
        return ""

    title = intent.title or "a meeting"
    invited = proposal.resolved_attendees

    lines = [f"I can set up *{title}* — these times work:"]
    for i, slot in enumerate(proposal.slots, start=1):
        lines.append(f"  {i}. {format_slot_label(slot, tz=tz)}")

    # Always make the invitee list explicit so Jon can catch anyone missing BEFORE
    # the invite goes out — a meeting that should include 5 people but only lists 1
    # is easy to miss when "who" is buried in the title line.
    lines.append("")
    if invited:
        lines.append("*Inviting:* " + ", ".join(invited))
    else:
        lines.append("*Inviting:* no one mapped yet — tell me who should be on it.")

    if proposal.unresolved_attendees:
        lines.append(
            "I couldn't map "
            + ", ".join(proposal.unresolved_attendees)
            + " to a calendar, so they are NOT on the invite — send me their email to add them."
        )
    if proposal.availability_pending:
        lines.append(
            "Heads up — I couldn't read "
            + ", ".join(proposal.availability_pending)
            + "'s availability, so these times are based on your calendar; theirs are pending."
        )

    lines.append("")
    lines.append(
        "Reply *yes* to send (or *yes 2* to pick a time) — "
        "and tell me if anyone else should be on it first."
    )
    return "\n".join(lines)


def build_calendar_create_payload(
    *,
    slot: CandidateSlot,
    title: str,
    attendees: list[str],
    description: str | None = None,
    tz_name: str,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Build the agency-gate calendar.create payload for a chosen slot (pure).

    Shape matches what ``_execute_calendar_create`` consumes:
    {calendar_id, summary, start{dateTime,timeZone}, end{...}, attendees, description}
    """
    return {
        "calendar_id": calendar_id,
        "summary": title,
        "start": {
            "dateTime": slot.start.astimezone(ZoneInfo(tz_name)).isoformat(),
            "timeZone": tz_name,
        },
        "end": {
            "dateTime": slot.end.astimezone(ZoneInfo(tz_name)).isoformat(),
            "timeZone": tz_name,
        },
        "attendees": attendees,
        "description": description,
    }


def build_proposal_preview(proposal: SlotProposal, *, tz: ZoneInfo) -> str:
    """Short one-line preview stored on the ProposedAction (for the gate DM)."""
    if not proposal.slots:
        return ""
    first = format_slot_label(proposal.slots[0], tz=tz)
    title = proposal.intent.title or "a meeting"
    invitees = (
        " (" + ", ".join(proposal.resolved_attendees) + ")"
        if proposal.resolved_attendees
        else ""
    )
    return f"create '{title}'{invitees} at {first}"


def scheduled_dedup_key(granola_id: str, action_item_key: str) -> str:
    """Memory dedup content: one scheduling proposal per (meeting, action item)."""
    return f"{_SCHEDULED_PREFIX}{granola_id}:{action_item_key}"


# ── I/O orchestration (thin adapters over existing infra) ─────────────────────
#
# The functions below perform the real-world I/O (GCal, LLM, Slack, memory) and
# wire the pure helpers together.  They are deliberately thin and degrade
# gracefully — every external call is guarded so a missing integration can
# never crash the scheduler sweep.

# How many of the most-recent meetings to scan each sweep.
_RECENT_MEETINGS_SCAN_LIMIT: int = 10
# Only consider meetings summarized within this many hours (avoid re-litigating
# old meetings if memory dedup is ever wiped).
_RECENT_MEETINGS_WINDOW_HOURS: int = 48


async def _resolve_gcal_client_io(session: Any) -> Any:
    """Build a live GCalClient from the active integration, or None.

    Mirrors agency_gate._resolve_gcal_client / meeting_prep so there is one
    consistent credential path.
    """
    try:
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.gcal.client import GCalClient

        rows = await repo.list_active(session, provider="gcal")
        if not rows:
            return None
        creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
        return GCalClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
        )
    except Exception:
        logger.debug("post_meeting_scheduling: gcal client resolve failed", exc_info=True)
        return None


async def _owner_calendar_id(session: Any) -> str:
    """The authed account's own calendar id for free/busy. 'primary' is correct
    for the connected account."""
    return "primary"


async def build_slot_proposal(
    session: Any,
    *,
    intent: SchedulingIntent,
    gcal_client: Any,
    now: datetime,
    tz_name: str,
) -> SlotProposal:
    """Resolve attendees, query free/busy, and find candidate slots.

    Degrades gracefully:
      - non-email attendees are flagged unresolved (v1 has no directory lookup).
      - calendars whose free/busy can't be read are flagged availability_pending
        and excluded from the busy set (we fall back to Jon's calendar only).
    """
    tz = ZoneInfo(tz_name)
    window_start, window_end = resolve_search_window(intent.timeframe, now=now, tz=tz)

    attendee_emails, unresolved = split_resolved_attendees(intent.attendees)

    # Directory resolution: try to map each unresolved NAME to an email via the
    # name→email directory. Only single, confident, unambiguous matches are used
    # (resolve_one returns None otherwise) — those names stay unresolved and keep
    # the existing "couldn't map" behaviour unchanged. Import lazily to avoid any
    # circular-import / provider-stack pull at module load.
    if unresolved:
        from artemis.directory.resolver import resolve_one

        still_unresolved: list[str] = []
        for name in unresolved:
            resolved_email: str | None = None
            try:
                resolved_email = await resolve_one(name, session)
            except Exception:
                logger.warning(
                    "post_meeting_scheduling: directory resolve_one failed for %r — "
                    "leaving unresolved",
                    name,
                    exc_info=True,
                )
            if resolved_email and resolved_email not in attendee_emails:
                attendee_emails.append(resolved_email)
            elif resolved_email is None:
                still_unresolved.append(name)
        unresolved = still_unresolved

    # Always include the owner's own calendar so we never double-book Jon.
    owner_cal = await _owner_calendar_id(session)
    calendars_to_query = [owner_cal, *attendee_emails]

    busy: list[BusyInterval] = []
    availability_pending: list[str] = []

    if gcal_client is not None:
        try:
            data = await gcal_client.query_freebusy(
                time_min=window_start.isoformat(),
                time_max=window_end.isoformat(),
                calendar_ids=calendars_to_query,
            )
            busy, unreadable = parse_freebusy_response(
                data, requested_calendars=calendars_to_query
            )
            # The owner calendar being unreadable is a hard problem (bad creds);
            # attendee calendars being unreadable is the expected coworker case.
            availability_pending = [c for c in unreadable if c != owner_cal]
        except Exception:
            logger.warning(
                "post_meeting_scheduling: freebusy query failed — falling back to "
                "empty busy set (proposing tentative slots)",
                exc_info=True,
            )
            # No busy data at all → still propose, but every attendee is pending.
            availability_pending = list(attendee_emails)
    else:
        availability_pending = list(attendee_emails)

    slots = find_free_slots(
        busy=busy,
        window_start=window_start,
        window_end=window_end,
        duration_minutes=intent.duration_minutes,
        tz=tz,
    )

    return SlotProposal(
        intent=intent,
        slots=slots,
        resolved_attendees=attendee_emails,
        unresolved_attendees=unresolved,
        availability_pending=availability_pending,
    )


async def _already_proposed(session: Any, *, dedup_content: str) -> bool:
    """True if we've already proposed scheduling for this action item."""
    try:
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        scope = [Scope(scope_kind="agent", scope_id="floating-artemis")]
        results = await search_observations(
            session=session,
            scope_set=scope,
            query=dedup_content,
            limit=20,
            modes=["fts"],
        )
        for obs in results:
            content = (obs.content if hasattr(obs, "content") else obs.get("content", "")) or ""
            if content.strip() == dedup_content:
                return True
        return False
    except Exception:
        logger.debug("post_meeting_scheduling: dedup check failed", exc_info=True)
        return False


async def _mark_proposed(session: Any, *, dedup_content: str, granola_id: str) -> None:
    """Write the dedup observation so we don't re-propose this action item."""
    from artemis.memory.schemas import Scope, SourceQualityHint
    from artemis.memory.store import write_observation

    scope = Scope(scope_kind="agent", scope_id="floating-artemis")
    await write_observation(
        session,
        scope=scope,
        content=dedup_content,
        category="convention",
        source_quality=SourceQualityHint.agent,
        raw_source_kind="post_meeting_schedule",
        raw_source_id=granola_id,
        raw_actor="artemis-proactivity",
    )


@dataclass(frozen=True)
class SchedulingSweepSummary:
    meetings_scanned: int
    items_classified: int
    scheduling_items: int
    proposals_sent: int
    skipped_already_proposed: int
    skipped_no_slots: int
    # Scheduling-shaped items whose owner did NOT resolve to Jon (the account
    # owner) — e.g. a coworker's action item. We never propose a calendar
    # action on Jon's behalf for someone else's commitment.
    skipped_not_owner: int = 0


async def run_post_meeting_scheduling_sweep(
    session: Any,
    *,
    adapter: Any = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SchedulingSweepSummary:
    """Scan recent meetings, detect scheduling action items, propose times to Jon.

    PROPOSE-ONLY. This NEVER creates an event. Creation happens later via the
    agency gate when Jon replies "yes" to the DM, routed through the existing
    proposed-action reply handler.

    Owner gating: mirrors the opt-in pattern in
    artemis.proactivity.commitments.ingest_meeting_commitments. Each action
    item's ``owner`` field is resolved to a user id and compared against the
    canonical system owner (jon.fila@, OWNER_EMAIL). A calendar proposal is
    only ever sent when the item's owner resolves to Jon. Scheduling-shaped
    items owned by someone else (or with an unresolved/unstated owner) are
    skipped — Artemis never proposes a calendar action on Jon's behalf for a
    commitment someone else made.

    Returns a summary of what happened (counts), suitable for logging/tests.
    """
    from sqlalchemy import select

    from artemis.config import settings
    from artemis.meetings.models import MeetingSummary
    from artemis.proactivity.agency_gate import propose_action
    from artemis.proactivity.commitments import (  # reuse identity resolution
        _normalize_text,
        _resolve_artemis_dm_recipient,
        _resolve_canonical_owner_user_id,
        _resolve_owner_user_id,
        action_item_key,
    )

    current = now or datetime.now(UTC)
    tz_name = settings.morning_brief_tz
    tz = ZoneInfo(tz_name)

    meetings_scanned = 0
    items_classified = 0
    scheduling_items = 0
    proposals_sent = 0
    skipped_already = 0
    skipped_no_slots = 0
    skipped_not_owner = 0

    if adapter is None:
        from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

        try:
            adapter = resolve_adapter(provider="claude-code")
        except NoProviderAvailableError:
            logger.info("post_meeting_scheduling: no LLM provider — sweep skipped")
            return SchedulingSweepSummary(0, 0, 0, 0, 0, 0)

    cutoff = current - timedelta(hours=_RECENT_MEETINGS_WINDOW_HOURS)
    rows = (
        (
            await session.execute(
                select(MeetingSummary)
                .where(MeetingSummary.created_at >= cutoff)
                .order_by(MeetingSummary.created_at.desc())
                .limit(_RECENT_MEETINGS_SCAN_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    gcal_client = await _resolve_gcal_client_io(session)
    target_user_id = await _resolve_artemis_dm_recipient(session)
    # Resolve once per sweep (mirrors ingest_meeting_commitments) to avoid a
    # DB round-trip per action item.
    canonical_owner_user_id = await _resolve_canonical_owner_user_id(session)

    for meeting in rows:
        meetings_scanned += 1
        action_items = meeting.action_items or []
        if not isinstance(action_items, list):
            continue

        for item in action_items:
            if not isinstance(item, dict):
                continue
            text = " ".join(str(item.get("text") or "").split()).strip()
            if not text:
                continue
            owner_label = _normalize_text(item.get("owner")) or None

            dedup = scheduled_dedup_key(meeting.granola_id, action_item_key(text))
            if await _already_proposed(session, dedup_content=dedup):
                skipped_already += 1
                continue

            items_classified += 1
            intent = await classify_action_item(
                action_item_text=text,
                meeting_title=meeting.title,
                adapter=adapter,
                owner=owner_label,
            )
            if not intent.is_scheduling:
                continue
            scheduling_items += 1

            # ── Owner gate ────────────────────────────────────────────────────
            # Mirrors commitments.ingest_meeting_commitments's opt-in gate: only
            # propose a calendar action when the item's owner resolves to Jon
            # (the account owner). A coworker's action item — or one with no
            # resolvable owner — is skipped; Artemis never proposes inviting
            # people to a meeting on Jon's behalf for someone else's commitment.
            owner_user_id = await _resolve_owner_user_id(session, owner_label)
            owner_is_owner = (
                canonical_owner_user_id is not None
                and owner_user_id == canonical_owner_user_id
            )
            if not owner_is_owner:
                skipped_not_owner += 1
                logger.info(
                    "post_meeting_scheduling: skipping scheduling proposal — owner=%r "
                    "does not resolve to the account owner (granola_id=%s, "
                    "llm_owner_is_operator=%s)",
                    owner_label,
                    meeting.granola_id,
                    intent.owner_is_operator,
                )
                # Still mark proposed so we don't re-classify every sweep.
                if not dry_run:
                    await _mark_proposed(
                        session, dedup_content=dedup, granola_id=meeting.granola_id
                    )
                continue

            proposal = await build_slot_proposal(
                session,
                intent=intent,
                gcal_client=gcal_client,
                now=current,
                tz_name=tz_name,
            )
            message = format_proposal_message(proposal, tz=tz)
            if not message or not proposal.slots:
                skipped_no_slots += 1
                # Still mark proposed so we don't re-classify every sweep.
                if not dry_run:
                    await _mark_proposed(
                        session, dedup_content=dedup, granola_id=meeting.granola_id
                    )
                continue

            if dry_run:
                proposals_sent += 1
                continue

            # Propose the FIRST slot through the agency gate (Jon can pick another
            # in his reply via "yes N"; v1 executes slot 1 on a bare "yes").
            chosen = proposal.slots[0]
            payload = build_calendar_create_payload(
                slot=chosen,
                title=intent.title or "Meeting",
                attendees=proposal.resolved_attendees,
                description=f"Scheduled from meeting: {meeting.title}",
                tz_name=tz_name,
            )
            preview = build_proposal_preview(proposal, tz=tz)

            action = await propose_action(
                session,
                action_type="calendar.create",
                payload=payload,
                preview=preview,
                requested_by="artemis",
                target_user_id=target_user_id,
            )
            # Send the rich, conversational proposal DM (with all slot options +
            # caveats) rather than the gate's terse default. The gate's yes/no
            # reply handler still matches on the proposal id.
            await _send_scheduling_dm(
                session,
                message=message + f"\n\n_(reply *yes A{action.id}* to confirm)_",
            )
            await _mark_proposed(
                session, dedup_content=dedup, granola_id=meeting.granola_id
            )
            proposals_sent += 1

    if not dry_run:
        await session.commit()

    return SchedulingSweepSummary(
        meetings_scanned=meetings_scanned,
        items_classified=items_classified,
        scheduling_items=scheduling_items,
        proposals_sent=proposals_sent,
        skipped_already_proposed=skipped_already,
        skipped_no_slots=skipped_no_slots,
        skipped_not_owner=skipped_not_owner,
    )


async def _send_scheduling_dm(session: Any, *, message: str) -> None:
    """DM Jon the rich scheduling proposal via the Artemis bot (sole-interrupt path)."""
    try:
        from artemis.integrations.slack.client import SlackClient
        from artemis.proactivity.commitments import (
            _get_slack_token_for_agent,
            _resolve_artemis_dm_recipient,
        )

        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning("post_meeting_scheduling: no Slack token for artemis agent")
            return
        recipient = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient, text=message)
    except Exception:
        logger.warning("post_meeting_scheduling: scheduling DM send failed", exc_info=True)
