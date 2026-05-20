"""m3a_tighten_state_check — drop soft CHECK, add strict enum-only CHECK on 5 lifecycle columns.

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-20

Drops the NOT VALID, legacy-permissive CHECK constraints added in migration 0032
and replaces them with tight constraints that allow ONLY the current enum values
(including the four new Gate-1 SignalState members from M3a).

Operator procedure if this migration fails:
    If the upgrade fails with a CHECK violation, at least one row has a value
    outside the enum vocabulary. Run the following to find offending rows:

        SELECT id, signal_status FROM signal_queue
          WHERE signal_status NOT IN (
            'pending_qualification','qualified','rejected_hard_filter',
            'suppressed_stale','approved','rejected_at_gate_1','snoozed','archived'
          );
        SELECT id, decision_state FROM campaign_candidates
          WHERE decision_state NOT IN (
            'created','in_inbox','approved','rejected','snoozed','asked',
            'monitoring','changes_requested'
          );
        SELECT id, workspace_state FROM campaign_candidates
          WHERE workspace_state NOT IN (
            'pending_content','in_content_preparation','sent_to_writing_studio',
            'content_preparation_failed','content_in_review','all_content_approved',
            'revision_needed'
          );
        SELECT id, status FROM campaign_deliverables
          WHERE status NOT IN (
            'queued','generating','draft_ready','approved','revised',
            'rejected','generation_failed'
          );

    Remap those rows manually to the nearest enum value before re-running.
    Do NOT add the legacy string to the enum — fix the data.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ── Exact enum vocabularies (must mirror state_machine.py) ────────────────────

_SIGNAL_STATES = (
    "pending_qualification",
    "qualified",
    "rejected_hard_filter",
    "suppressed_stale",
    # Gate-1 outcomes (M3a extension)
    "approved",
    "rejected_at_gate_1",
    "snoozed",
    "archived",
)

_BRIEF_STATES = (
    "created",
    "in_inbox",
    "approved",
    "rejected",
    "snoozed",
    "asked",
    "monitoring",
    "changes_requested",
)

_WORKSPACE_STATES = (
    "pending_content",
    "in_content_preparation",
    "sent_to_writing_studio",
    "content_preparation_failed",
    "content_in_review",
    "all_content_approved",
    "revision_needed",
)

_DELIVERABLE_STATES = (
    "queued",
    "generating",
    "draft_ready",
    "approved",
    "revised",
    "rejected",
    "generation_failed",
)


def _check_expr(column: str, valid: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in valid)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # ── Update server defaults to enum-valid values ───────────────────────────
    op.execute(
        "ALTER TABLE signal_queue "
        "ALTER COLUMN signal_status SET DEFAULT 'pending_qualification'"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ALTER COLUMN decision_state SET DEFAULT 'created'"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ALTER COLUMN workspace_state SET DEFAULT 'pending_content'"
    )

    # ── Drop the old NOT VALID / legacy-permissive constraints ────────────────
    op.execute("ALTER TABLE signal_queue DROP CONSTRAINT IF EXISTS ck_signal_queue_signal_status")
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

    # ── Add tight enum-only constraints (VALIDATE — will fail on bad rows) ────
    op.execute(
        "ALTER TABLE signal_queue "
        "ADD CONSTRAINT ck_signal_queue_signal_status "
        f"CHECK ({_check_expr('signal_status', _SIGNAL_STATES)})"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ADD CONSTRAINT ck_campaign_candidates_decision_state "
        f"CHECK ({_check_expr('decision_state', _BRIEF_STATES)})"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ADD CONSTRAINT ck_campaign_candidates_workspace_state "
        f"CHECK ({_check_expr('workspace_state', _WORKSPACE_STATES)})"
    )
    op.execute(
        "ALTER TABLE campaign_deliverables "
        "ADD CONSTRAINT ck_campaign_deliverables_status "
        f"CHECK ({_check_expr('status', _DELIVERABLE_STATES)})"
    )


def downgrade() -> None:
    # Restore old server defaults
    op.execute(
        "ALTER TABLE signal_queue ALTER COLUMN signal_status SET DEFAULT 'in_inbox'"
    )
    op.execute(
        "ALTER TABLE campaign_candidates ALTER COLUMN decision_state SET DEFAULT 'pending_review'"
    )
    op.execute(
        "ALTER TABLE campaign_candidates ALTER COLUMN workspace_state SET DEFAULT 'created'"
    )

    # Drop tight constraints
    op.execute("ALTER TABLE signal_queue DROP CONSTRAINT IF EXISTS ck_signal_queue_signal_status")
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

    # Restore the NOT VALID permissive constraints from 0032
    _SIGNAL_STATES_OLD = (
        "pending_qualification", "qualified", "rejected_hard_filter", "suppressed_stale",
        "in_inbox", "approved", "rejected", "snoozed", "archived", "expired",
    )
    _BRIEF_STATES_OLD = (
        "created", "in_inbox", "approved", "rejected", "snoozed", "asked",
        "pending_review", "monitoring", "changes_requested",
    )
    _WORKSPACE_STATES_OLD = (
        "pending_content", "in_content_preparation", "sent_to_writing_studio",
        "content_preparation_failed", "created", "revision_needed",
        "all_content_approved", "content_in_review", "content_in_progress",
    )
    _DELIVERABLE_STATES_OLD = (
        "queued", "generating", "draft_ready", "approved", "revised",
        "rejected", "generation_failed", "ready_for_review", "rejected_at_gate_2",
        "review_pending",
    )
    op.execute(
        "ALTER TABLE signal_queue "
        "ADD CONSTRAINT ck_signal_queue_signal_status "
        f"CHECK ({_check_expr('signal_status', _SIGNAL_STATES_OLD)} "
        "OR signal_status LIKE 'legacy_%') NOT VALID"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ADD CONSTRAINT ck_campaign_candidates_decision_state "
        f"CHECK ({_check_expr('decision_state', _BRIEF_STATES_OLD)} "
        "OR decision_state LIKE 'legacy_%') NOT VALID"
    )
    op.execute(
        "ALTER TABLE campaign_candidates "
        "ADD CONSTRAINT ck_campaign_candidates_workspace_state "
        f"CHECK ({_check_expr('workspace_state', _WORKSPACE_STATES_OLD)} "
        "OR workspace_state LIKE 'legacy_%') NOT VALID"
    )
    op.execute(
        "ALTER TABLE campaign_deliverables "
        "ADD CONSTRAINT ck_campaign_deliverables_status "
        f"CHECK ({_check_expr('status', _DELIVERABLE_STATES_OLD)} "
        "OR status LIKE 'legacy_%') NOT VALID"
    )
