"""SQLAlchemy ORM models for the crisis-content approval pipeline (slice B1).

Four additive tables (a fifth, ``CrisisContentThreadNote``, added in CCA9).
See ``docs/crisis-content-approval-pipeline.md`` for the design and
``alembic/versions/0106_crisis_content_transitions.py`` for the migration
that creates the first three -- alembic is this schema's authority, not
this module; nothing here calls ``Base.metadata.create_all()``.

``CrisisContentCard``   -- latest observed state, one row per card identity.
``CrisisContentCopyVersion`` -- append-only copy version log (CLAUDE.md
    rule 3, lossless memory). No code path in this package ever UPDATEs or
    DELETEs a row here.
``CrisisContentNotification`` -- the dedup ledger for "have we already
    notified on this (card, route, status, copy_hash)". Written only by
    ``artemis.crisis_content.transitions.mark_notified``. ``copy_hash``,
    ``channel_id``, and ``message_ts`` joined the ledger in CCA9 (migration
    0109) -- see that migration's docstring and
    ``artemis.crisis_content.transitions``'s "re-approval fix" notes for why
    ``copy_hash`` is part of the dedup key, and
    ``artemis.crisis_content.thread_notes`` for why ``channel_id``/
    ``message_ts`` (where the card actually landed) are recorded at all.
``CrisisContentDecision`` -- append-only decision log (slice B2c, CCA5).
    Written only by ``artemis.crisis_content.decisions.record_decision``.
    Same lossless-memory rule as the copy version log: no UPDATE, no DELETE,
    anywhere. See ``alembic/versions/0107_crisis_content_decisions.py``.
``CrisisContentThreadNote`` -- append-only capture of a Slack thread reply
    on a posted card (CCA9). Written only by
    ``artemis.crisis_content.thread_notes.handle_thread_reply``. Never a
    decision -- see that module for the "never infer approval from prose"
    constraint. See ``alembic/versions/0109_cca9_card_lifecycle.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

__all__ = [
    "CrisisContentCard",
    "CrisisContentCopyVersion",
    "CrisisContentNotification",
    "CrisisContentDecision",
    "CrisisContentThreadNote",
]


class CrisisContentCard(Base):
    """Latest observed state for one review-card identity.

    Identity is ``(identity_header, identity_platform, identity_ordinal)``,
    mirroring ``ReviewCard.identity_key``. ``identity_platform`` is nullable
    (Jen's platform chip can be unset); uniqueness on the triple is enforced
    by a COALESCE-based expression index in the migration, not a plain
    ``UniqueConstraint`` here -- see that migration's docstring for why a
    plain constraint would let two platform-less rows collide.
    """

    __tablename__ = "crisis_content_cards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    identity_header: Mapped[str] = mapped_column(Text, nullable=False)
    identity_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    copy_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    copy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentCopyVersion(Base):
    """Append-only copy version log -- never UPDATEd, never DELETEd.

    One row per ``(card_id, copy_hash)`` first-seen. This is the only place
    the vendor's original wording survives a later edit -- see
    ``docs/crisis-content-approval-pipeline.md``, "Decided 2026-08-11 --
    capture edits and rejections, not just approvals", and ``CLAUDE.md``
    rule 3 (lossless memory). There is deliberately no public function
    anywhere in this package that updates or deletes a row in this table.
    """

    __tablename__ = "crisis_content_copy_versions"
    __table_args__ = (
        UniqueConstraint(
            "card_id", "copy_hash", name="uq_crisis_content_copy_versions_card_hash"
        ),
        Index("ix_crisis_content_copy_versions_card_id", "card_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("crisis_content_cards.id", name="fk_copy_versions_card", ondelete="CASCADE"),
        nullable=False,
    )
    copy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    copy_body: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentNotification(Base):
    """Dedup ledger -- one row per ``(card, route, status, copy_hash)`` notified.

    Written by ``mark_notified`` (slice B2 calls it only after a successful
    Slack post -- never before, so a delivery failure is never mistaken for
    a delivered one). ``artemis.crisis_content.transitions`` otherwise only
    reads this table, via ``has_notified`` and the header-rename-guard join,
    to decide what to suppress.

    ``copy_hash``, ``channel_id``, ``message_ts`` added in CCA9 (migration
    0109; nullable because rows written before that migration have none):

    - ``copy_hash`` joined the UNIQUE constraint (was
      ``(card_id, route, status_value)``, now adds ``copy_hash``) -- this is
      what lets a genuinely revised post re-fire after a ``changes_requested``
      decision instead of being swallowed by the OLD ledger row for the same
      ``(card, route, 'Ready')``. See
      ``artemis.crisis_content.transitions``'s "re-approval fix" notes.
    - ``channel_id``/``message_ts`` record exactly where ``post_transition_card``
      posted this notification's card, from the real Slack API response --
      see ``artemis.crisis_content.notify.PostedCardMessage``. This is the
      only place a Slack THREAD reply can be mapped back to a card; see
      ``artemis.crisis_content.thread_notes.find_card_thread_target``.
    """

    __tablename__ = "crisis_content_notifications"
    __table_args__ = (
        UniqueConstraint(
            "card_id",
            "route",
            "status_value",
            "copy_hash",
            name="uq_crisis_content_notifications_card_route_status",
        ),
        Index("ix_crisis_content_notifications_card_id", "card_id"),
        Index("ix_crisis_content_notifications_message_ts", "message_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("crisis_content_cards.id", name="fk_notifications_card", ondelete="CASCADE"),
        nullable=False,
    )
    route: Mapped[str] = mapped_column(Text, nullable=False)
    status_value: Mapped[str] = mapped_column(Text, nullable=False)
    copy_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_ts: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentDecision(Base):
    """Append-only decision log -- never UPDATEd, never DELETEd (slice B2c, CCA5).

    One row per Approve / Request-changes click, ever. Deliberately NO
    unique constraint on ``(card_id, route)``: a route can legitimately go
    ``changes_requested`` -> ``approved`` later, and both rows must survive
    (``CLAUDE.md`` rule 3, lossless memory) -- see
    ``artemis.crisis_content.decisions`` for the "is this a genuine
    duplicate click, or a legitimate later decision" business rule, which
    lives entirely in code, never as a DB constraint that would make the
    legitimate case impossible to write.

    ``decided_by_slack_user_id`` comes ONLY from the verified Slack
    interactivity payload's top-level ``user.id`` -- never from a button
    ``value`` or a modal's ``private_metadata``. See
    ``artemis/routes/integrations_slack_interactivity.py`` and
    ``artemis/crisis_content/slack_actions.py``.
    """

    __tablename__ = "crisis_content_decisions"
    __table_args__ = (
        Index("ix_crisis_content_decisions_card_route", "card_id", "route"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "crisis_content_cards.id", name="fk_crisis_content_decisions_card", ondelete="CASCADE"
        ),
        nullable=False,
    )
    route: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_slack_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentThreadNote(Base):
    """Append-only capture of a Slack thread reply on a posted card (CCA9).

    Never UPDATEd, never DELETEd (``CLAUDE.md`` rule 3) -- same
    lossless-memory discipline as ``CrisisContentCopyVersion`` /
    ``CrisisContentDecision``. Written only by
    ``artemis.crisis_content.thread_notes.handle_thread_reply``.

    Deliberately NEVER a decision: a reply saying "approved" or "looks
    good" records NOTHING here that any caller could ever read as consent.
    ``artemis.crisis_content.decisions.record_decision`` -- fed only by a
    verified Slack button click -- is the ONLY path that can write a
    ``CrisisContentDecision`` row. ``route`` is nullable purely for
    triage/readability (which route's thread this reply landed in, if
    known); nothing in this package branches on it.

    Unique on ``(card_id, message_ts)`` so a Slack retry of the same reply
    cannot insert twice -- ``handle_thread_reply``'s write is
    ``ON CONFLICT DO NOTHING`` on this constraint, and that idempotency is
    also what makes the "nudge once per thread" rule safe under retry: see
    that function's docstring.

    ``channel_id`` and ``file_count`` added in CCA10 (migration 0110):
    ``channel_id`` was already a parameter ``handle_thread_reply`` received
    (used to post the nudge) but never persisted; CCA10's image-link flow
    (``artemis.crisis_content.image_link``) needs it back to call
    ``chat.getPermalink`` for a note it only has by id. ``file_count`` is
    the number of files this reply's Slack ``files[]`` array carried (array
    length only -- never file content), for the "one reply, three files ->
    one line saying '3 images'" rule. Both nullable/defaulted because rows
    written before CCA10 have neither.
    """

    __tablename__ = "crisis_content_thread_notes"
    __table_args__ = (
        UniqueConstraint(
            "card_id", "message_ts", name="uq_crisis_content_thread_notes_card_message_ts"
        ),
        Index("ix_crisis_content_thread_notes_card_id", "card_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "crisis_content_cards.id",
            name="fk_crisis_content_thread_notes_card",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    route: Mapped[str | None] = mapped_column(Text, nullable=True)
    slack_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    author_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    has_attachment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # CCA10: the Slack channel this reply landed in (needed by image_link.py
    # to call chat.getPermalink against a note fetched later by id alone)
    # and how many files its `files[]` array carried (array length, never
    # file content -- see the class docstring).
    channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_ts: Mapped[str] = mapped_column(Text, nullable=False)
    # Parent thread of this reply. "Nudge once" is scoped to (card_id,
    # thread_ts), not card_id alone -- see the migration's column comment.
    thread_ts: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
