"""Slack thread replies on a posted crisis-content card (CCA9).

Section 3 of ``briefs/cca9-card-lifecycle.md``. This module owns the entire
"someone replied in a card's thread" path: mapping the thread back to a
card (``find_card_thread_target``), recording the reply
(``handle_thread_reply``), and the single entry point the Slack events
route calls (``maybe_handle_thread_reply``).

**Never a decision.** A reply saying "approved" or "looks good" records NO
decision here -- this module only ever writes to
``crisis_content_thread_notes``, never to ``crisis_content_decisions``. The
button is the only thing that decides (``artemis.crisis_content.decisions``,
fed only by a verified Slack interactivity payload). See
``CrisisContentThreadNote``'s docstring for the same constraint at the ORM
layer.

**Nudge once per thread.** A note already existing for this ``(card_id,
thread_ts)`` pair (from an earlier reply, checked BEFORE this reply's own
insert) means "already nudged" -- capture silently, no second nudge.

Scoped per THREAD, not per card, on purpose: one card can have both routes
posted to different places (asset -> Jon's DM, copy -> the channel), and the
re-approval fix posts a brand-new card in a brand-new thread. Card-scoped
dedup would swallow the nudge on a thread's genuinely first reply -- exactly
the moment someone is most likely to believe their reply counted as an
approval, which is the entire reason the nudge exists. This read-before-write order
is also what makes the rule safe under a Slack retry of the SAME reply: the
retry's own insert is idempotent (``ON CONFLICT DO NOTHING`` on
``(card_id, message_ts)``), and by the time the retry runs, this exact
note already exists, so the retry itself reads as "already noted" and
stays silent -- never nudging twice for what is really one reply delivered
twice.

**No `files:read` scope.** Callie can see a reply's ``files[]`` array (a
reply carries file metadata whether or not the app can read file content),
but has no scope to fetch ``url_private`` -- that call would 403. This
module never attempts it; it only ever sets ``has_attachment`` from
whether ``files`` was non-empty, and acknowledges the attachment in the
nudge text. Do not add the scope here or anywhere in this slice.

**CCA10 extension.** ``handle_thread_reply`` now also persists ``channel_id``
(a parameter it already received, for the nudge, but never stored) and
``file_count`` (the length of the reply's files array -- a count, not file
content), and returns the note's id. ``maybe_handle_thread_reply`` uses that
id to fire ``artemis.crisis_content.image_link.schedule_image_link_delivery``
whenever the reply carried at least one file -- that module turns the note
into a permalink line in Jen's doc, entirely separately from the capture and
nudge this module still owns. See that module's docstring for why it needs
no new Slack scope either.

**Wiring (owned by ``artemis/routes/integrations_slack_events.py``, not
this module).** ``maybe_handle_thread_reply`` is called from
``_handle_mentionable_event`` BEFORE the channel-allowlist gate
(``_is_authorized_inbound``) and before the generic conversational agent
loop (``route_inbound``) would otherwise run -- a card reply must get the
nudge, not an LLM improvisation. See that route module for why this must
run early: Callie's `allowed_channel_ids` deliberately does NOT include
the crisis-content channel (it also carries Jon<->Jen 1:1 traffic Callie
must never join uninvited), so this hook is the ONLY delivery path for a
reply to one of Callie's own cards. Returning ``False`` here must leave
every other guard's behavior completely unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.crisis_content.image_link import schedule_image_link_delivery
from artemis.crisis_content.orm import CrisisContentNotification, CrisisContentThreadNote
from artemis.crisis_content.transitions import Route
from artemis.directory.models import DirectoryPerson
from artemis.integrations.slack.client import SlackClient

logger = logging.getLogger(__name__)

__all__ = [
    "CardThreadTarget",
    "find_card_thread_target",
    "handle_thread_reply",
    "maybe_handle_thread_reply",
]

_THREAD_NOTES_CONSTRAINT = "uq_crisis_content_thread_notes_card_message_ts"

_NUDGE_TEXT = "Got it, noted. Tap Approve above when you're happy and I'll record it."
_NUDGE_TEXT_WITH_ATTACHMENT = (
    "Thanks — I can see the image in the thread. Tap Approve above when you're happy "
    "and I'll record it."
)


@dataclass(frozen=True)
class CardThreadTarget:
    """A Slack thread resolved back to the card it was posted for."""

    card_id: int
    route: Route | None


async def find_card_thread_target(
    session: AsyncSession, *, channel_id: str, thread_ts: str
) -> CardThreadTarget | None:
    """Map a Slack thread back to the card it was posted for, if any.

    Looks up ``crisis_content_notifications`` by the EXACT ``(channel_id,
    message_ts)`` the card was posted with (CCA9 section 2 --
    ``artemis.crisis_content.notify.post_transition_card`` /
    ``artemis.crisis_content.transitions.mark_notified``). This is the ONLY
    place that mapping is read back. Returns ``None`` for any thread that
    isn't a known card's root message -- the caller treats that as "not
    ours, leave it alone," per this module's docstring.

    Newest match wins (``ORDER BY id DESC``) in the (currently impossible)
    case of more than one row sharing a ``(channel_id, message_ts)`` pair.
    """
    stmt = (
        select(CrisisContentNotification.card_id, CrisisContentNotification.route)
        .where(
            CrisisContentNotification.channel_id == channel_id,
            CrisisContentNotification.message_ts == thread_ts,
        )
        .order_by(CrisisContentNotification.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    return CardThreadTarget(card_id=row[0], route=cast("Route | None", row[1]))


async def _has_note_in_thread(session: AsyncSession, card_id: int, thread_ts: str) -> bool:
    """Whether this specific THREAD has already been nudged.

    Scoped to ``(card_id, thread_ts)`` rather than ``card_id`` alone. One card
    can have both routes posted to different places (asset -> Jon's DM, copy ->
    the channel), and the re-approval fix posts a brand-new card in a new
    thread. Card-scoped dedup would swallow the nudge on a thread's genuinely
    first reply -- precisely the moment someone is most likely to assume their
    reply counted as an approval, which is the whole reason the nudge exists.
    """
    stmt = (
        select(CrisisContentThreadNote.id)
        .where(
            CrisisContentThreadNote.card_id == card_id,
            CrisisContentThreadNote.thread_ts == thread_ts,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _resolve_directory_email(
    session: AsyncSession, slack_user_id: str, *, access_token: str = ""
) -> str | None:
    """Best-effort Slack user id -> directory email. ``None`` on any miss.

    A separate, small copy of the same lookup
    ``artemis.crisis_content.slack_actions._resolve_email`` performs --
    deliberately not imported from there (that function is private to that
    module, and this caller's failure contract differs: a miss here just
    means ``author_email`` is recorded as ``None``, never "unauthorized").
    """
    if not slack_user_id:
        return None
    try:
        result = await session.execute(
            select(DirectoryPerson.email).where(DirectoryPerson.slack_user_id == slack_user_id)
        )
        email = result.scalar_one_or_none()
    except Exception:
        logger.exception(
            "crisis_content: directory lookup failed for slack_user_id=%s", slack_user_id
        )
        email = None
    if email:
        return str(email)

    # Same users.info fallback slack_actions._resolve_email got after the
    # 2026-08-12 postmortem, and for the same reason: every real approver is in
    # directory_people with slack_user_id = NULL, so the directory alone always
    # misses. Without this, author_email on every thread note from a real
    # approver stays NULL forever -- which quietly guts the Writing Studio
    # harvest, whose whole value is knowing WHO changed what.
    #
    # Unlike slack_actions._resolve_email this gates nothing: a miss here means
    # an unattributed note, never "unauthorized". So it stays best-effort.
    if not access_token:
        return None
    try:
        return await SlackClient(token=access_token).lookup_user_email(slack_user_id)
    except Exception:
        logger.exception(
            "crisis_content: users.info lookup failed for slack_user_id=%s", slack_user_id
        )
        return None


async def handle_thread_reply(
    session: AsyncSession,
    *,
    card_id: int,
    route: Route | None,
    slack_user_id: str,
    author_email: str | None,
    text: str,
    has_attachment: bool,
    message_ts: str,
    channel_id: str,
    thread_ts: str,
    access_token: str,
    file_count: int = 0,
) -> int:
    """Record one thread reply, and nudge iff this card has never had a note before.

    Never infers a decision from ``text`` -- see the module docstring's
    "Never a decision" constraint; this function only ever writes to
    ``crisis_content_thread_notes``.

    Commits before returning -- this is a terminal write off the request
    path (mirrors ``artemis.crisis_content.decisions.record_decision``'s own
    "commits before returning" choice), not a step in some larger
    transaction another caller needs to bundle with.

    Idempotent under Slack retry: the INSERT is ``ON CONFLICT DO NOTHING``
    keyed on ``(card_id, message_ts)`` (the migration's unique constraint),
    and whether to nudge is decided from whether ANY note already existed
    for this card BEFORE this insert -- so a retried delivery of the SAME
    reply reads as "already nudged" and stays silent. See the module
    docstring's "Nudge once per thread".

    Returns the id of the note row for this ``(card_id, message_ts)`` --
    freshly inserted, or (under a Slack retry of the same reply) the row
    from the first delivery. The caller (``maybe_handle_thread_reply``)
    uses this id to schedule the CCA10 image-link follow-on exactly once
    per genuinely distinct reply, the same way ``slack_actions.py`` uses
    ``record_decision``'s returned row to call ``schedule_decision_writeback``.
    """
    already_noted = await _has_note_in_thread(session, card_id, thread_ts)

    stmt = (
        pg_insert(CrisisContentThreadNote)
        .values(
            card_id=card_id,
            route=route,
            slack_user_id=slack_user_id,
            author_email=author_email,
            text=text,
            has_attachment=has_attachment,
            channel_id=channel_id,
            file_count=file_count,
            message_ts=message_ts,
            thread_ts=thread_ts,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint=_THREAD_NOTES_CONSTRAINT)
        .returning(CrisisContentThreadNote.id)
    )
    result = await session.execute(stmt)
    note_id = result.scalar_one_or_none()
    if note_id is None:
        # Conflict: a Slack retry of the exact same (card_id, message_ts).
        # The row from the first delivery already exists -- fetch its id so
        # the caller can still (idempotently) schedule the follow-on.
        existing = await session.execute(
            select(CrisisContentThreadNote.id).where(
                CrisisContentThreadNote.card_id == card_id,
                CrisisContentThreadNote.message_ts == message_ts,
            )
        )
        note_id = existing.scalar_one()
    await session.commit()

    if already_noted:
        logger.info(
            "crisis_content: thread reply captured silently for card_id=%s "
            "thread_ts=%s (this thread was already nudged)",
            card_id,
            thread_ts,
        )
        return note_id

    nudge = _NUDGE_TEXT_WITH_ATTACHMENT if has_attachment else _NUDGE_TEXT
    try:
        await SlackClient(token=access_token).post_message(
            channel=channel_id, text=nudge, thread_ts=thread_ts
        )
    except Exception:
        # The note is already committed -- a failed nudge is a missed
        # courtesy, not a lost capture. Never raise back into the events
        # route over this.
        logger.exception(
            "crisis_content: failed to post thread-reply nudge for card_id=%s", card_id
        )

    return note_id


async def maybe_handle_thread_reply(
    session: AsyncSession,
    *,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    slack_user_id: str,
    text: str,
    has_files: bool,
    access_token: str,
    file_count: int = 0,
) -> bool:
    """Entry point for the Slack events hook (CCA9).

    ``True`` iff this event was a reply to a known crisis-content card and
    has been fully handled -- the caller MUST return immediately without
    falling through to the channel-allowlist gate or the generic
    conversational agent loop. ``False`` for anything else (not a reply to
    a known card), in which case the caller's EXISTING behavior must run
    completely unchanged -- this function makes no side effect at all on a
    ``False`` return.

    CCA10: when the reply carried at least one file, schedules
    ``artemis.crisis_content.image_link.schedule_image_link_delivery`` for
    the note ``handle_thread_reply`` just wrote (or, under a Slack retry,
    the pre-existing one) -- that module's own idempotency ledger is what
    keeps a retried delivery of this call from ever producing a second doc
    line, not anything here. A note with no files never schedules anything.
    """
    target = await find_card_thread_target(session, channel_id=channel_id, thread_ts=thread_ts)
    if target is None:
        return False

    author_email = await _resolve_directory_email(session, slack_user_id, access_token=access_token)

    note_id = await handle_thread_reply(
        session,
        card_id=target.card_id,
        route=target.route,
        slack_user_id=slack_user_id,
        author_email=author_email,
        text=text,
        has_attachment=has_files,
        message_ts=message_ts,
        channel_id=channel_id,
        thread_ts=thread_ts,
        access_token=access_token,
        file_count=file_count,
    )

    if has_files or file_count > 0:
        try:
            schedule_image_link_delivery(note_id)
        except Exception:
            logger.exception(
                "crisis_content: failed to schedule image-link delivery for "
                "thread_note_id=%s -- the reply is captured but the doc will not be "
                "linked until this is retried",
                note_id,
            )

    return True
