"""ORM model for callie_dm_send_attempts (CALLIE-1 audit trail).

Backs ``artemis.floating_artemis.tools.callie_dm.send_guarded_dm`` — Callie's
one initiating capability. Every call is recorded here, whether it was sent,
refused, or errored, per the brief's hard requirement: "Audit every
attempt... Both sends and refusals; a refusal nobody can see is how you find
out too late."

Append-only, matching CLAUDE.md rule 3 (lossless memory / evidence): no
UPDATE or DELETE path is exposed anywhere in this codebase for this table.

``outcome``:
    "sent"    — both gates passed, Slack accepted the message.
    "refused" — a policy gate denied the attempt (identity, authorization,
                input shape). Nothing was sent.
    "error"   — both gates passed but a Slack API call failed afterwards
                (lookup or the send itself). Kept distinct from "refused" so
                a technical failure is never misread as a permissions
                decision — the CLAUDE.md lesson about not conflating "not
                permitted" with "could not look you up".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class CallieDmSendAttempt(Base):
    """One row per send_guarded_dm call — sent, refused, or errored."""

    __tablename__ = "callie_dm_send_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('sent', 'refused', 'error')",
            name="ck_callie_dm_send_attempts_outcome",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    # The requester's Slack user id, resolved from the verified inbound Slack
    # event (never from message text). NULL only when the turn carried no
    # resolvable identity at all — the fail-closed-unresolved-identity path.
    requester_slack_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The requester's email, resolved from Slack's users.info. NULL when that
    # lookup itself failed — distinct from "resolved but not authorized".
    requester_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Exactly what the model passed as the recipient identifier, unmodified —
    # kept even on early refusal so a bad identifier is visible in the audit.
    recipient_input: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_slack_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The message body as requested (pre-attribution), always populated.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # The exact text handed to chat.postMessage (attribution prefix + body).
    # NULL unless outcome == "sent".
    sent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    # Short machine-readable code, e.g. "requester_not_authorized",
    # "recipient_lookup_failed". NULL when outcome == "sent".
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Slack's message ts on a successful send. NULL otherwise.
    slack_ts: Mapped[str | None] = mapped_column(Text, nullable=True)
