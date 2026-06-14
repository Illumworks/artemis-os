"""Add 2-hour staleness guard to v_floating_artemis_active_runs.

Phantom "stuck" badge counts arise when agent_runs / workflow_runs rows are left
in status 'running' or 'queued' after a crash or timeout.  The view previously
counted ALL such rows regardless of age.  This migration adds
``AND started_at > now() - interval '2 hours'`` to both branches so that runs
older than 2 hours are automatically excluded from the active count.

Revision ID: 0089
Revises: 0088
Create Date: 2026-06-14
"""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Recreate the view with the 2-hour staleness guard on both branches.
    op.execute("""
        CREATE OR REPLACE VIEW v_floating_artemis_active_runs AS
            SELECT
                run_id,
                'agent'     AS run_type,
                agent_id    AS subject_id,
                status,
                started_at,
                NULL::TIMESTAMPTZ AS completed_at,
                owner_user_id
            FROM agent_runs
            WHERE status IN ('running', 'queued')
              AND started_at > now() - interval '2 hours'

            UNION ALL

            SELECT
                CAST(id AS TEXT) AS run_id,
                'workflow'        AS run_type,
                workflow_id       AS subject_id,
                status,
                started_at,
                completed_at,
                owner_user_id
            FROM workflow_runs
            WHERE status IN ('running', 'queued')
              AND started_at > now() - interval '2 hours'
    """)


def downgrade() -> None:
    # Restore the original view from 0009 (no staleness guard).
    op.execute("""
        CREATE OR REPLACE VIEW v_floating_artemis_active_runs AS
            SELECT
                run_id,
                'agent'     AS run_type,
                agent_id    AS subject_id,
                status,
                started_at,
                NULL::TIMESTAMPTZ AS completed_at,
                owner_user_id
            FROM agent_runs
            WHERE status IN ('running', 'queued')

            UNION ALL

            SELECT
                CAST(id AS TEXT) AS run_id,
                'workflow'        AS run_type,
                workflow_id       AS subject_id,
                status,
                started_at,
                completed_at,
                owner_user_id
            FROM workflow_runs
            WHERE status IN ('running', 'queued')
    """)
