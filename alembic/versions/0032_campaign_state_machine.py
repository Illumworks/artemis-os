"""campaign_state_machine — audit table + CHECK constraints on 4 lifecycle columns.

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-20

Creates:
  - campaign_state_transitions table (append-only audit log)
  - CHECK constraints on signal_queue.signal_status, campaign_candidates.decision_state,
    campaign_candidates.workspace_state, and campaign_deliverables.status

Constraint design:
  The CHECK constraints allow BOTH the new M3 enum values AND the legacy values
  from the pre-M3 state machine (prefixed with 'legacy_' OR as-is for backward
  compatibility with existing routes and tests). The application-level transition()
  function is the primary enforcement point; the CHECK constraints guard against
  completely unknown garbage values.

  Backfill: No rows are modified. Legacy values are accepted as-is.
  Column defaults are NOT changed — the application transition() enforces new values
  on writes it controls; legacy routes continue to use their existing values until
  migrated in a follow-up brief.

  The downgrade drops all constraints and the audit table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ── Valid state sets per column (new M3 + legacy values) ─────────────────────

_SIGNAL_STATES = (
    # New M3 enum
    "pending_qualification",
    "qualified",
    "rejected_hard_filter",
    "suppressed_stale",
    # Legacy pre-M3 values (existing routes still write these)
    "in_inbox",
    "approved",
    "rejected",
    "snoozed",
    "archived",
    "expired",
)

_BRIEF_STATES = (
    # New M3 enum
    "created",
    "in_inbox",
    "approved",
    "rejected",
    "snoozed",
    "asked",
    # Legacy pre-M3 values
    "pending_review",
    "monitoring",
    "changes_requested",
)

_WORKSPACE_STATES = (
    # New M3 enum
    "pending_content",
    "in_content_preparation",
    "sent_to_writing_studio",
    "content_preparation_failed",
    # Legacy pre-M3 values
    "created",
    "revision_needed",
    "all_content_approved",
    "content_in_review",
    "content_in_progress",
)

_DELIVERABLE_STATES = (
    # New M3 enum
    "queued",
    "generating",
    "draft_ready",
    "approved",
    "revised",
    "rejected",
    "generation_failed",
    # Legacy pre-M3 values
    "ready_for_review",
    "rejected_at_gate_2",
    "review_pending",
)


def _check_expr(column: str, valid: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in valid)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # ── 1. Audit table ────────────────────────────────────────────────────────
    op.create_table(
        "campaign_state_transitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "transitioned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_state_transitions"),
    )
    op.create_index(
        "idx_cst_entity_type_id_at",
        "campaign_state_transitions",
        ["entity_type", "entity_id", "transitioned_at"],
    )

    # ── 2. CHECK constraints (NOT VALID — deferred validation of existing rows)
    # Allow new M3 values, legacy values (used by existing routes/tests), and
    # any 'legacy_'-prefixed value (future backfill path).
    op.execute(
        "ALTER TABLE signal_queue "
        "ADD CONSTRAINT ck_signal_queue_signal_status "
        f"CHECK ({_check_expr('signal_status', _SIGNAL_STATES)} "
        "OR signal_status LIKE 'legacy_%') NOT VALID"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ADD CONSTRAINT ck_campaign_candidates_decision_state "
        f"CHECK ({_check_expr('decision_state', _BRIEF_STATES)} "
        "OR decision_state LIKE 'legacy_%') NOT VALID"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ADD CONSTRAINT ck_campaign_candidates_workspace_state "
        f"CHECK ({_check_expr('workspace_state', _WORKSPACE_STATES)} "
        "OR workspace_state LIKE 'legacy_%') NOT VALID"
    )
    op.execute(
        "ALTER TABLE campaign_deliverables "
        "ADD CONSTRAINT ck_campaign_deliverables_status "
        f"CHECK ({_check_expr('status', _DELIVERABLE_STATES)} "
        "OR status LIKE 'legacy_%') NOT VALID"
    )


def downgrade() -> None:
    # Drop CHECK constraints
    op.execute(
        "ALTER TABLE signal_queue "
        "DROP CONSTRAINT IF EXISTS ck_signal_queue_signal_status"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "DROP CONSTRAINT IF EXISTS ck_campaign_candidates_decision_state"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "DROP CONSTRAINT IF EXISTS ck_campaign_candidates_workspace_state"
    )
    op.execute(
        "ALTER TABLE campaign_deliverables "
        "DROP CONSTRAINT IF EXISTS ck_campaign_deliverables_status"
    )

    # Drop audit table
    op.drop_index("idx_cst_entity_type_id_at", table_name="campaign_state_transitions")
    op.drop_table("campaign_state_transitions")
