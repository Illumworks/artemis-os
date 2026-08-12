"""SQLAlchemy ORM models for the CCA15 rule-mining slice.

Two additive tables, both new to this slice (see
``alembic/versions/0112_crisis_content_rule_mining.py``). Kept in a module
separate from ``artemis/crisis_content/orm.py`` deliberately: another slice
is running concurrently against this package and CCA15's brief scopes this
worker to "a NEW module ... do NOT touch" the existing package files, so
these models live entirely in new files rather than appending to the
existing ``orm.py`` (which this slice never opens).

``CrisisContentRuleMiningObservation`` -- append-only, one row per distinct
    suggestion occurrence (CLAUDE.md rule 3, lossless memory -- same
    discipline as ``CrisisContentCopyVersion``/``CrisisContentDecision`` in
    ``orm.py``). Never UPDATEd, never DELETEd. Uniqueness on
    ``occurrence_key`` is what makes re-polling the same still-pending Docs
    suggestion a no-op insert rather than a double count -- see
    ``artemis.crisis_content.rule_mining._occurrence_key``.

``CrisisContentRuleMiningPair`` -- latest aggregate state for one
    *normalized* (deleted, inserted) pair, mutable (this IS updated in
    place -- it is a running counter and a "have we already proposed this"
    flag, not a lossless log; the lossless record of every occurrence lives
    in the observations table above). One row per
    ``(normalized_deleted, normalized_inserted)``. ``proposed_candidate_id``
    is a soft, unenforced reference into ``writing_training_candidates.id``
    (a different module's table) -- no FK constraint, mirroring
    ``WritingTrainingCandidate.approved_memory_observation_id``'s own
    cross-module reference style in ``artemis/writing_rules/models.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

__all__ = [
    "CrisisContentRuleMiningObservation",
    "CrisisContentRuleMiningPair",
]


class CrisisContentRuleMiningObservation(Base):
    """Append-only log -- one row per distinct suggestion occurrence, ever.

    Never UPDATEd, never DELETEd. ``occurrence_key`` is unique so recording
    the same physical Docs suggestion twice (e.g. it is still pending on a
    later poll) is a no-op insert -- see the module docstring and
    ``artemis.crisis_content.rule_mining.record_and_propose``'s idempotency
    contract. ``deleted_text``/``inserted_text`` keep the ORIGINAL casing and
    whitespace as observed (display); ``normalized_deleted``/
    ``normalized_inserted`` are the casefolded, whitespace-collapsed
    aggregation key (see ``rule_mining.normalize_for_aggregation``).
    """

    __tablename__ = "crisis_content_rule_mining_observations"
    __table_args__ = (
        UniqueConstraint(
            "occurrence_key", name="uq_crisis_content_rule_mining_observations_occurrence"
        ),
        Index(
            "ix_crisis_content_rule_mining_observations_pair",
            "normalized_deleted",
            "normalized_inserted",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurrence_key: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_deleted: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_inserted: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_text: Mapped[str] = mapped_column(Text, nullable=False)
    inserted_text: Mapped[str] = mapped_column(Text, nullable=False)
    tab_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tab_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentRuleMiningPair(Base):
    """Latest aggregate state for one normalized (deleted, inserted) pair.

    One row per ``(normalized_deleted, normalized_inserted)``. ``status``
    starts ``"counting"`` and moves to ``"proposed"`` exactly once, the
    moment ``occurrence_count`` first reaches the threshold -- see
    ``rule_mining.record_and_propose``. That one-way flip is what makes a
    re-run idempotent: a pair already at ``"proposed"`` is never re-proposed
    even if its count keeps climbing on later polls (more evidence for an
    already-surfaced rule is not a second finding).
    """

    __tablename__ = "crisis_content_rule_mining_pairs"
    __table_args__ = (
        UniqueConstraint(
            "normalized_deleted",
            "normalized_inserted",
            name="uq_crisis_content_rule_mining_pairs_pair",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    normalized_deleted: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_inserted: Mapped[str] = mapped_column(Text, nullable=False)
    # First-seen display casing -- "Case is preserved for display but not for
    # aggregation" (CCA15 brief). Never overwritten after creation.
    display_deleted: Mapped[str] = mapped_column(Text, nullable=False)
    display_inserted: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="counting")
    proposed_candidate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
