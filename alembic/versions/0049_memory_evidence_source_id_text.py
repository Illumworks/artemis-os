"""memory_evidence.source_id: BigInteger → TEXT (CC28)

Revision ID: 0049
Revises: 0048
Create Date: 2026-05-30

Changes:
  - memory_evidence.source_id: BigInteger → TEXT (CC28)
  - Existing numeric source_id values are converted to their string representations
    (e.g. BigInt 182 → TEXT "182"). Lossless — no rows deleted.
  - The unique constraint uq_evidence_obs_source is recreated on the new TEXT column.

Rationale:
  Before this migration, non-numeric source IDs (skill slugs, pipeline_run UUIDs,
  FA session IDs) were SHA-256 hashed to fit in BigInteger. That hash cannot be
  round-tripped to the original value, breaking queries like "give me evidence for
  skill X". This migration unblocks Salesforce/ChurnZero/Gong integrations that
  need string-typed CRM record IDs as evidence.

  Rows written before this migration with hashed source_ids (obs ids 29–31, MC3/MC4/MC5
  smokes) retain their hashed values stringified (e.g. "56773593525409192"). Per the
  lossless invariant they are NOT modified.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Add new TEXT column (nullable initially for migration) ─────────────
    op.add_column(
        "memory_evidence",
        sa.Column("source_id_text", sa.Text(), nullable=True),
    )

    # ── 2. Populate: stringify existing BigInt values ─────────────────────────
    # Safe cast: CAST(bigint AS text) is always defined in Postgres.
    # SHA-256 hashed values (stored as large positive ints) become their decimal
    # string representations — lossless, no information lost.
    op.execute("UPDATE memory_evidence SET source_id_text = CAST(source_id AS TEXT)")

    # ── 3. Make NOT NULL now that all rows are populated ──────────────────────
    op.alter_column("memory_evidence", "source_id_text", nullable=False)

    # ── 4. Drop old unique constraint (references BigInt column) ─────────────
    op.drop_constraint("uq_evidence_obs_source", "memory_evidence", type_="unique")

    # ── 5. Drop old BigInt source_id column ───────────────────────────────────
    op.drop_index("idx_memory_evidence_source", table_name="memory_evidence")
    op.drop_column("memory_evidence", "source_id")

    # ── 6. Rename new TEXT column to source_id ────────────────────────────────
    op.alter_column("memory_evidence", "source_id_text", new_column_name="source_id")

    # ── 7. Recreate source index and unique constraint on TEXT column ─────────
    op.create_index(
        "idx_memory_evidence_source",
        "memory_evidence",
        ["source_kind", "source_id"],
    )
    op.create_unique_constraint(
        "uq_evidence_obs_source",
        "memory_evidence",
        ["observation_id", "source_kind", "source_id"],
    )


def downgrade() -> None:
    # Reverse: TEXT → BigInteger. This is best-effort: will fail if any non-numeric
    # source_id values exist (e.g. skill slugs or UUIDs written after CC28).
    op.drop_constraint("uq_evidence_obs_source", "memory_evidence", type_="unique")
    op.drop_index("idx_memory_evidence_source", table_name="memory_evidence")

    op.add_column(
        "memory_evidence",
        sa.Column("source_id_int", sa.BigInteger(), nullable=True),
    )
    # CAST(text AS bigint) will fail on non-numeric strings — downgrade is not safe
    # if non-numeric source_ids exist. Documented limitation.
    op.execute("UPDATE memory_evidence SET source_id_int = CAST(source_id AS BIGINT)")
    op.alter_column("memory_evidence", "source_id_int", nullable=False)
    op.drop_column("memory_evidence", "source_id")
    op.alter_column("memory_evidence", "source_id_int", new_column_name="source_id")

    op.create_index(
        "idx_memory_evidence_source",
        "memory_evidence",
        ["source_kind", "source_id"],
    )
    op.create_unique_constraint(
        "uq_evidence_obs_source",
        "memory_evidence",
        ["observation_id", "source_kind", "source_id"],
    )
