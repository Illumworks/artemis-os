"""workflow_context — extend agent_context to support workflow_run_id.

Adds a nullable ``workflow_run_id`` FK column alongside the existing ``run_id``
column. A CHECK constraint enforces that exactly one of the two is non-null.
The ``run_id`` FK is made nullable to allow workflow-context rows (run_id=NULL).

Also adds:
  - Index on workflow_run_id for fast lookups.

Phase F2b: Execution wiring for sequential workflows.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the NOT NULL constraint on run_id so workflow rows can leave it NULL.
    op.execute("ALTER TABLE agent_context ALTER COLUMN run_id DROP NOT NULL")

    # 2. Drop the existing FK on run_id so we can recreate it with nullable semantics.
    #    (The FK itself allows NULLs once the column is nullable, but we rename for clarity.)
    op.execute("ALTER TABLE agent_context DROP CONSTRAINT IF EXISTS fk_agent_context_run")
    op.execute("""
        ALTER TABLE agent_context
            ADD CONSTRAINT fk_agent_context_run
            FOREIGN KEY (run_id)
            REFERENCES agent_runs(run_id)
            ON DELETE CASCADE
    """)

    # 3. Add workflow_run_id FK column.
    op.execute("""
        ALTER TABLE agent_context
            ADD COLUMN IF NOT EXISTS workflow_run_id BIGINT
                REFERENCES workflow_runs(id)
                ON DELETE CASCADE
    """)

    # 4. Index for fast workflow context lookups.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_context_workflow_run_id "
        "ON agent_context (workflow_run_id)"
    )

    # 5. Exactly-one check constraint.
    op.execute("""
        ALTER TABLE agent_context
            ADD CONSTRAINT chk_agent_context_one_owner CHECK (
                (run_id IS NOT NULL AND workflow_run_id IS NULL) OR
                (run_id IS NULL AND workflow_run_id IS NOT NULL)
            )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE agent_context DROP CONSTRAINT IF EXISTS chk_agent_context_one_owner")
    op.execute("DROP INDEX IF EXISTS idx_agent_context_workflow_run_id")
    op.execute("ALTER TABLE agent_context DROP COLUMN IF EXISTS workflow_run_id")
    # Restore run_id to NOT NULL (only safe if no workflow rows remain — downgrade is destructive)
    op.execute("DELETE FROM agent_context WHERE run_id IS NULL")
    op.execute("ALTER TABLE agent_context ALTER COLUMN run_id SET NOT NULL")
