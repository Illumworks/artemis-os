"""campaign_sends — let a send say honestly that nothing was sent

Revision ID: 0122
Revises: 0121
Create Date: 2026-09-09

`mark_send_sent` has always written ``status='sent'`` while sending nothing. The
column comment admits it — "sent — transport stub has recorded the send (no real
email)" — but ``sent`` is what every other surface reads, and none of them read
the log line underneath. Zero sends exist, so no row needs migrating; this is
about what the next one is allowed to claim.

Two changes:

**``simulated`` joins the status check.** A send that no provider handled is not
``sent`` and is not ``failed`` either — nothing went wrong, nothing went out.

**The transport check is dropped rather than widened.** It pinned the column to
the single literal ``'stub'``, so every future provider would have needed its own
migration to be nameable — a hand-maintained per-case list, which is the shape
this codebase keeps having to unpick. The set of transports now lives in
``artemis/marketing/transport.py``, where an unknown name resolves to no-send.
The column stays NOT NULL, so a row must still say what handled it.
"""

from __future__ import annotations

from alembic import op

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_campaign_sends_status", "campaign_sends", type_="check")
    op.create_check_constraint(
        "ck_campaign_sends_status",
        "campaign_sends",
        "status IN ('queued','sent','simulated','failed','skipped')",
    )
    op.drop_constraint("ck_campaign_sends_transport", "campaign_sends", type_="check")


def downgrade() -> None:
    # Any 'simulated' row would violate the old constraint, so fold it into the
    # value the old schema used for the same situation.
    op.execute("UPDATE campaign_sends SET status = 'sent' WHERE status = 'simulated'")
    op.execute("UPDATE campaign_sends SET transport = 'stub' WHERE transport <> 'stub'")
    op.create_check_constraint(
        "ck_campaign_sends_transport", "campaign_sends", "transport = 'stub'"
    )
    op.drop_constraint("ck_campaign_sends_status", "campaign_sends", type_="check")
    op.create_check_constraint(
        "ck_campaign_sends_status",
        "campaign_sends",
        "status IN ('queued','sent','failed','skipped')",
    )
