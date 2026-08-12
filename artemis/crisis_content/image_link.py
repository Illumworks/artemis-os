"""Link a thread-attached image into Jen's doc, as a text line (CCA10).

Background: ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca10-slack-image-to-doc.md``. When someone drops an image into the
Slack thread on one of Callie's crisis-content cards, this module inserts a
line into that post's card in Jen's Google Doc linking to the Slack message
that carries it -- **no image bytes move anywhere**.

An earlier draft of the brief uploaded the file to Drive and embedded it with
``insertInlineImage``. That is explicitly out of scope here, for reasons
worth restating because they constrain the implementation, not just the
design intent:

- **No file download, ever.** Callie has no ``files:read`` scope and cannot
  fetch a Slack attachment's ``url_private`` -- that call would 403. This
  module never attempts it, and never even sees the file's bytes or its
  ``url_private``; ``chat.getPermalink`` (see ``SlackClient.get_permalink``)
  only needs a channel + a message ts, both of which
  ``artemis.crisis_content.thread_notes`` already records for every reply.
- **No Drive upload, no Google image fetch.** ``insertInlineImage`` needs a
  URI Google's own servers can retrieve; a plain text line with a Slack
  permalink needs nothing Google-side beyond the same ``insertText`` CCA7
  already performs.

Reused from CCA7 (``artemis.crisis_content.writeback``), per the brief:
``locate_card_table`` (unchanged, imported as-is) and ``write_doc_line``
(the generic locate + insert + verify sequence, promoted to public in that
module specifically so this one could call it instead of re-deriving the
same ~40 lines). ``CardNotLocatedError`` and ``WritebackVerificationError``
are the same two exception types for the same reason: a card this module
cannot positively identify, or a post-write verification mismatch, are the
exact same failure modes CCA7 already defined and alerts on.

NOT reused: Google-credential resolution. This module carries its own copy
of ``_resolve_personal_credential``/``_resolve_docs_access_token``, matching
the deliberate "independent copy per module" precedent already documented
in ``poller.py`` and ``writeback.py`` (each has its own, for its own
failure contract) -- adding a fourth copy here is consistent with that
choice, not a new one.

Idempotency: one row per ``thread_note_id`` in
``crisis_content_image_link_deliveries`` (see the migration and
``CrisisContentImageLinkDelivery`` below) -- the brief's "extend CCA7's
delivery-ledger pattern, keyed on the thread note id." There is only one
side-effecting action here (the doc line; the confirmation reply is a
best-effort courtesy, same treatment ``thread_notes.py`` gives its own
nudge), so the ledger has no ``action`` column the way CCA7's does -- one
row per note is the whole contract.

**Off by default.** ``settings.crisis_content_image_link_enabled`` gates
the doc write + confirmation reply, checked as the very first thing
``deliver_image_link`` does, before any DB query or network call --
mirroring ``writeback.deliver_decision_writeback``'s own settings check.
Two reasons, not one: writing into an externally-owned, live document is
Jon's call to turn on (same reasoning CCA7 shipped on originally), and it
also keeps this module from making any real Slack/Docs call when
``schedule_image_link_delivery`` is exercised incidentally by CCA9's own
test suite (``tests/test_crisis_content_lifecycle.py``), which seeds no
Slack integration or Google credential for the background task to find.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

import artemis.db as _db
from artemis.config import settings
from artemis.crisis_content.export_client import TARGET_DOCUMENT_ID
from artemis.crisis_content.orm import CrisisContentCard, CrisisContentThreadNote
from artemis.crisis_content.writeback import (
    CardNotLocatedError,
    WritebackVerificationError,
    write_doc_line,
)
from artemis.db import Base
from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
from artemis.google_docs.models import GoogleCredential
from artemis.google_integration import resolve_google_oauth_client_config
from artemis.integrations.slack.client import SlackAPIError, SlackClient
from artemis.proactivity.commitments import (
    _get_slack_token_for_agent,
    _resolve_artemis_dm_recipient,
)
from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

logger = logging.getLogger(__name__)

__all__ = [
    "CrisisContentImageLinkDelivery",
    "deliver_image_link",
    "schedule_image_link_delivery",
    "render_image_link_line",
]

_DELIVERY_CONSTRAINT = "uq_crisis_content_image_link_deliveries_thread_note"

DeliveryStatus = Literal[
    "delivered", "already_delivered", "not_located", "damaged", "failed", "skipped", "disabled"
]


class CrisisContentImageLinkDelivery(Base):
    """Idempotency ledger -- one row per ``thread_note_id`` delivered.

    See ``alembic/versions/0110_crisis_content_image_link.py`` for the
    migration. Mirrors ``CrisisContentWritebackDelivery`` (CCA7) minus the
    ``action`` column -- CCA10 has exactly one side-effecting action per
    note, so the note's id alone is the whole idempotency key.
    """

    __tablename__ = "crisis_content_image_link_deliveries"
    __table_args__ = (UniqueConstraint("thread_note_id", name=_DELIVERY_CONSTRAINT),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_note_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "crisis_content_thread_notes.id",
            name="fk_crisis_content_image_link_thread_note",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    delivered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class _CredentialUnavailableError(Exception):
    """The personal Google credential is missing, expired, or under-scoped."""


# ---------------------------------------------------------------------------
# Line rendering
# ---------------------------------------------------------------------------


def _poster_label(note: CrisisContentThreadNote) -> str:
    """Best-effort human label -- mirrors ``writeback._actor_label``.

    Uses the raw email (or a Slack mention as a fallback), same convention
    as every other actor label in this pipeline -- there is no display-name
    resolution utility anywhere in this package, and inventing one here
    (an extra ``users.info`` call) is outside this slice's scope.
    """
    if note.author_email:
        return note.author_email
    return f"<@{note.slack_user_id}>"


def render_image_link_line(
    *, poster_label: str, posted_at: datetime, permalink: str, file_count: int
) -> str:
    """The line inserted into the card after the status block.

    ``file_count <= 1`` renders the brief's literal singular example
    (``"🖼 Asset in Slack — posted by …"``); anything higher renders the
    plural with the count folded in (``"🖼 Assets in Slack (3 images) —
    …"``) per the brief's "one reply with three files ... say '3 images'"
    rule. The permalink is to the MESSAGE, never a file -- one line per
    message, regardless of how many files it carried.
    """
    stamp = f"{posted_at.strftime('%b')} {posted_at.day}, {posted_at.strftime('%-I:%M%p').lower()}"
    if file_count <= 1:
        head = f"🖼 Asset in Slack — posted by {poster_label}, {stamp}"
    else:
        head = f"🖼 Assets in Slack ({file_count} images) — posted by {poster_label}, {stamp}"
    return f"{head}: {permalink}"


_CONFIRM_TEXT = "Linked in the doc now — thanks for the visual."


# ---------------------------------------------------------------------------
# Google credential resolution -- an independent copy, same reasoning as
# poller.py / writeback.py's own copies (see the module docstring).
# ---------------------------------------------------------------------------


async def _resolve_personal_credential(session: AsyncSession) -> GoogleCredential:
    result = await session.execute(
        select(GoogleCredential)
        .where(GoogleCredential.purpose == "personal")
        .order_by(GoogleCredential.updated_at.desc())
        .limit(1)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise _CredentialUnavailableError(
            "No personal Google credential connected -- connect via "
            "/api/google/oauth/start?purpose=personal"
        )

    now = datetime.now(UTC)
    if credential.expiry > now + timedelta(seconds=60):
        return credential

    if not credential.refresh_token:
        raise _CredentialUnavailableError(
            "Personal Google credential has no refresh_token -- reconnect required"
        )

    client_config = await resolve_google_oauth_client_config(session)
    try:
        refreshed = await refresh_access_token(
            refresh_token=credential.refresh_token,
            client_id=client_config.client_id,
            client_secret=client_config.client_secret,
        )
    except GoogleReauthRequiredError as exc:
        raise _CredentialUnavailableError(f"Google reconnect required: {exc}") from exc
    except httpx.HTTPError as exc:
        raise _CredentialUnavailableError(f"Google token refresh failed: {exc}") from exc

    credential.access_token = refreshed.access_token
    credential.refresh_token = refreshed.refresh_token
    credential.expiry = refreshed.expiry
    if refreshed.scope:
        credential.scope = refreshed.scope
    credential.updated_at = now
    await session.flush()
    return credential


async def _resolve_docs_access_token(session: AsyncSession) -> str:
    credential = await _resolve_personal_credential(session)
    return credential.access_token


# ---------------------------------------------------------------------------
# Owner alert -- another independent copy of the same pattern (see the
# module docstring's "NOT reused" paragraph).
# ---------------------------------------------------------------------------


async def _alert_jon(session: AsyncSession, text: str) -> None:
    """Best-effort Slack DM to Jon via the Artemis bot. Never raises."""
    try:
        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning(
                "crisis_content image_link: no active Slack token for agent_id='artemis' "
                "-- cannot alert Jon"
            )
            return
        recipient_id = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=text)
    except Exception:
        logger.exception("crisis_content image_link: failed to send owner alert DM")


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------


async def _has_delivered(session: AsyncSession, thread_note_id: int) -> bool:
    stmt = (
        select(CrisisContentImageLinkDelivery.id)
        .where(CrisisContentImageLinkDelivery.thread_note_id == thread_note_id)
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _mark_delivered(session: AsyncSession, thread_note_id: int) -> None:
    stmt = (
        pg_insert(CrisisContentImageLinkDelivery)
        .values(thread_note_id=thread_note_id)
        .on_conflict_do_nothing(constraint=_DELIVERY_CONSTRAINT)
    )
    await session.execute(stmt)
    await session.commit()


async def _load_note(session: AsyncSession, thread_note_id: int) -> CrisisContentThreadNote | None:
    result = await session.execute(
        select(CrisisContentThreadNote).where(CrisisContentThreadNote.id == thread_note_id)
    )
    return result.scalar_one_or_none()


async def _load_card(session: AsyncSession, card_id: int) -> CrisisContentCard:
    result = await session.execute(select(CrisisContentCard).where(CrisisContentCard.id == card_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise CardNotLocatedError(f"No CrisisContentCard row for card_id={card_id}")
    return row


# ---------------------------------------------------------------------------
# The one action
# ---------------------------------------------------------------------------


async def deliver_image_link(session: AsyncSession, thread_note_id: int) -> DeliveryStatus:
    """Deliver the permalink line for one thread note, exactly once.

    Never raises -- every failure branch is caught, logged, and (for the
    two doc-level failure modes CCA7 already defines) alerted to Jon. Safe
    to call more than once for the SAME ``thread_note_id`` (Slack retry, or
    a second call after a transient ``chat.getPermalink`` failure): the
    ledger check at the top skips everything if already delivered, so
    calling this twice never produces a second line or a second
    confirmation reply.

    Order of checks, cheapest/least-risky first:
    1. the settings kill switch (zero DB queries -- see the module
       docstring's "Off by default"),
    2. the idempotency ledger,
    3. the note itself, and whether it actually has an attachment
       (defensive -- the real caller, ``thread_notes.maybe_handle_thread_reply``,
       already only schedules this for notes with one),
    4. resolving Callie's Slack token and calling ``chat.getPermalink`` --
       logged and returned as ``"failed"`` on any failure, WITHOUT marking
       the ledger, so a transient failure (rate limit, a momentary Slack
       API hiccup) is safe to retry later,
    5. the doc write itself, via ``writeback.write_doc_line`` --
       ``CardNotLocatedError``/``WritebackVerificationError`` get the exact
       same "log ERROR, alert Jon, write nothing" / "alert loudly, mark
       delivered so a retry never compounds the damage" handling CCA7 uses
       for the identical failure modes,
    6. the ledger write, then the best-effort in-thread confirmation reply
       (a failed confirmation is a missed courtesy, not a lost delivery --
       the ledger row is already committed by the time it's attempted).
    """
    if not settings.crisis_content_image_link_enabled:
        logger.warning(
            "crisis_content image_link: disabled via settings "
            "(crisis_content_image_link_enabled=False) -- thread_note_id=%s will NOT be "
            "linked into the doc until re-enabled",
            thread_note_id,
        )
        return "disabled"

    if await _has_delivered(session, thread_note_id):
        return "already_delivered"

    note = await _load_note(session, thread_note_id)
    if note is None:
        logger.error("crisis_content image_link: no thread note row for id=%s", thread_note_id)
        return "failed"

    if not note.has_attachment or note.file_count < 1:
        logger.debug(
            "crisis_content image_link: thread_note_id=%s has no attachment -- nothing to do",
            thread_note_id,
        )
        return "skipped"

    if not note.channel_id:
        logger.error(
            "crisis_content image_link: thread_note_id=%s has no channel_id recorded -- "
            "cannot resolve a permalink",
            thread_note_id,
        )
        return "failed"

    card = await _load_card(session, note.card_id)

    agent_cfg = await _resolve_agent_slack_config(session, agent_id="callie", team_id=None)
    if not agent_cfg.access_token:
        logger.error(
            "crisis_content image_link: no active Slack token for agent_id='callie' -- "
            "thread_note_id=%s",
            thread_note_id,
        )
        return "failed"

    slack = SlackClient(token=agent_cfg.access_token)

    try:
        permalink = await slack.get_permalink(channel=note.channel_id, message_ts=note.message_ts)
    except (SlackAPIError, httpx.HTTPError) as exc:
        logger.error(
            "crisis_content image_link: chat.getPermalink failed thread_note_id=%s: %s",
            thread_note_id,
            exc,
        )
        return "failed"

    posted_at = datetime.fromtimestamp(float(note.message_ts), tz=UTC)
    line_text = render_image_link_line(
        poster_label=_poster_label(note),
        posted_at=posted_at,
        permalink=permalink,
        file_count=note.file_count,
    )

    try:
        access_token = await _resolve_docs_access_token(session)
        await write_doc_line(
            access_token,
            document_id=TARGET_DOCUMENT_ID,
            header=card.identity_header,
            copy_hash=card.copy_hash,
            line_text=line_text,
        )
    except CardNotLocatedError as exc:
        logger.error(
            "crisis_content image_link: target card not positively identified for "
            "thread_note_id=%s card_id=%s -- WRITING NOTHING. %s",
            thread_note_id,
            note.card_id,
            exc,
        )
        await _alert_jon(
            session,
            "🚨 Crisis-content image link: could not positively identify the target card "
            f"in Jen's doc for thread note #{thread_note_id} ({card.identity_header!r}). "
            f"Nothing was written -- please check the doc by hand.\n{exc}",
        )
        return "not_located"
    except WritebackVerificationError as exc:
        logger.critical(
            "crisis_content image_link: POST-WRITE VERIFICATION FAILED for "
            "thread_note_id=%s -- the document may be damaged. %s",
            thread_note_id,
            exc,
        )
        await _alert_jon(
            session,
            "🚨🚨 Crisis-content image link: wrote to Jen's doc but post-write "
            f"verification FAILED for thread note #{thread_note_id} — {exc}\nDo NOT "
            "attempt a fix from here; check the doc by hand immediately.",
        )
        # The insertText call itself succeeded -- mark delivered so a retry
        # never attempts a second, compounding insert into a doc that may
        # already be damaged. Mirrors writeback._deliver_doc_line.
        await _mark_delivered(session, thread_note_id)
        return "damaged"
    except _CredentialUnavailableError as exc:
        logger.error(
            "crisis_content image_link: doc write failed (credential) thread_note_id=%s: %s",
            thread_note_id,
            exc,
        )
        await _alert_jon(
            session, f"🚨 Crisis-content image link could not run (Google credential): {exc}"
        )
        return "failed"
    except httpx.HTTPError as exc:
        logger.exception(
            "crisis_content image_link: doc write HTTP failure thread_note_id=%s", thread_note_id
        )
        await _alert_jon(
            session,
            "🚨 Crisis-content image link: doc write failed for thread note "
            f"#{thread_note_id}: {exc}",
        )
        return "failed"

    await _mark_delivered(session, thread_note_id)
    logger.info("crisis_content image_link: delivered thread_note_id=%s", thread_note_id)

    try:
        await slack.post_message(channel=note.channel_id, text=_CONFIRM_TEXT, thread_ts=note.thread_ts)
    except Exception:
        logger.exception(
            "crisis_content image_link: doc line delivered but the confirmation reply "
            "failed thread_note_id=%s",
            thread_note_id,
        )

    return "delivered"


# ---------------------------------------------------------------------------
# Fire-and-forget scheduling -- called from thread_notes.py
# ---------------------------------------------------------------------------

_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def schedule_image_link_delivery(thread_note_id: int) -> None:
    """Fire-and-forget: run the image-link delivery for one thread note.

    Mirrors ``writeback.schedule_decision_writeback`` -- called immediately
    after ``thread_notes.handle_thread_reply`` commits a note with an
    attachment. Opens its OWN session rather than reusing the caller's (the
    events route's session may already be committed/closed by the time this
    task actually runs), and is never awaited inline: a
    ``chat.getPermalink`` + Docs fetch/insert/verify round trip can outlast
    the Slack Events API's response budget.
    """
    task = asyncio.create_task(_run_image_link_background(thread_note_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _run_image_link_background(thread_note_id: int) -> None:
    try:
        async with _db.SessionLocal() as session:
            outcome = await deliver_image_link(session, thread_note_id)
            logger.info(
                "crisis_content image_link: thread_note_id=%s outcome=%r",
                thread_note_id,
                outcome,
            )
    except Exception:
        logger.exception(
            "crisis_content image_link: unhandled error in background task for "
            "thread_note_id=%s",
            thread_note_id,
        )
