"""memory_m2_validity_and_conflicts — confidence, supersedes, evidence_count + conflict table.

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-20

Memory M2:
  - memory_observations: add confidence (float, CHECK 0-1, default 0.5),
      supersedes (BIGINT FK self), evidence_count (int, default 1)
  - memory_entities: add valid_from, valid_until, confidence, supersedes, evidence_count
  - memory_conflicts: new table for surfacing value/temporal/relational contradictions
  - Partial index: memory_observations(scope_id, entity_key, valid_until) WHERE valid_until IS NULL
      (scope_id TEXT on observations is the hot-path "currently valid" filter)
  - Backfill: existing rows get confidence=0.5, evidence_count=1, valid_from=created_at
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. memory_observations — M2 additive columns ──────────────────────────
    op.add_column(
        "memory_observations",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "memory_observations",
        sa.Column("supersedes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "memory_observations",
        sa.Column("evidence_count", sa.Integer(), nullable=True),
    )

    # Backfill before applying NOT NULL / CHECK constraints
    op.execute(
        "UPDATE memory_observations SET confidence = 0.5 WHERE confidence IS NULL"
    )
    op.execute(
        "UPDATE memory_observations SET evidence_count = 1 WHERE evidence_count IS NULL"
    )
    op.execute(
        "UPDATE memory_observations SET valid_from = created_at WHERE valid_from IS NULL"
    )

    # Apply NOT NULL now that rows have values
    op.alter_column("memory_observations", "confidence", nullable=False, server_default="0.5")
    op.alter_column("memory_observations", "evidence_count", nullable=False, server_default="1")

    # CHECK constraints
    op.execute(
        "ALTER TABLE memory_observations "
        "ADD CONSTRAINT ck_obs_confidence CHECK (confidence >= 0 AND confidence <= 1)"
    )
    op.execute(
        "ALTER TABLE memory_observations "
        "ADD CONSTRAINT ck_obs_valid_order "
        "CHECK (valid_until IS NULL OR valid_until > valid_from)"
    )

    # FK: supersedes → observations.id (self-referential; nullable)
    op.create_foreign_key(
        "fk_obs_supersedes",
        "memory_observations",
        "memory_observations",
        ["supersedes"],
        ["id"],
        ondelete="SET NULL",
    )

    # Partial index: hot-path "currently valid" filter
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_obs_scope_valid_now "
        "ON memory_observations(scope_kind, scope_id, valid_until) "
        "WHERE valid_until IS NULL"
    )

    # ── 2. memory_entities — M2 additive columns ──────────────────────────────
    op.add_column(
        "memory_entities",
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_entities",
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_entities",
        sa.Column("entity_evidence_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "memory_entities",
        sa.Column("entity_supersedes", sa.BigInteger(), nullable=True),
    )

    # Backfill entities
    op.execute(
        "UPDATE memory_entities SET valid_from = first_seen_at WHERE valid_from IS NULL"
    )
    op.execute(
        "UPDATE memory_entities SET entity_evidence_count = 1 WHERE entity_evidence_count IS NULL"
    )

    op.alter_column(
        "memory_entities", "entity_evidence_count", nullable=False, server_default="1"
    )

    # FK for entity supersedes
    op.create_foreign_key(
        "fk_entity_supersedes",
        "memory_entities",
        "memory_entities",
        ["entity_supersedes"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── 3. memory_conflicts — new table ───────────────────────────────────────
    op.create_table(
        "memory_conflicts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("observation_a_id", sa.BigInteger(), nullable=False),
        sa.Column("observation_b_id", sa.BigInteger(), nullable=False),
        sa.Column("conflict_type", sa.Text(), nullable=False),
        sa.Column(
            "detected_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_memory_conflicts"),
        sa.ForeignKeyConstraint(
            ["observation_a_id"],
            ["memory_observations.id"],
            name="fk_conflict_obs_a",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_b_id"],
            ["memory_observations.id"],
            name="fk_conflict_obs_b",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "conflict_type IN ('incompatible_values', 'incompatible_temporal', "
            "'incompatible_relational')",
            name="ck_conflict_type",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN "
            "('a_wins', 'b_wins', 'both_valid_different_scope', 'manual_review_needed', 'auto')",
            name="ck_conflict_resolution",
        ),
    )

    # UNIQUE on sorted (min, max) pair — prevents (a,b) and (b,a) duplicates
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_conflict_pair "
        "ON memory_conflicts(LEAST(observation_a_id, observation_b_id), "
        "GREATEST(observation_a_id, observation_b_id))"
    )

    op.create_index(
        "idx_conflicts_scope_unresolved",
        "memory_conflicts",
        ["scope_id", "resolution"],
    )
    op.create_index(
        "idx_conflicts_obs_a",
        "memory_conflicts",
        ["observation_a_id"],
    )
    op.create_index(
        "idx_conflicts_obs_b",
        "memory_conflicts",
        ["observation_b_id"],
    )


def downgrade() -> None:
    # Drop conflicts table + indexes
    op.execute("DROP INDEX IF EXISTS uq_conflict_pair")
    op.drop_index("idx_conflicts_obs_b", table_name="memory_conflicts")
    op.drop_index("idx_conflicts_obs_a", table_name="memory_conflicts")
    op.drop_index("idx_conflicts_scope_unresolved", table_name="memory_conflicts")
    op.drop_table("memory_conflicts")

    # Drop memory_entities M2 columns
    op.drop_constraint("fk_entity_supersedes", "memory_entities", type_="foreignkey")
    op.drop_column("memory_entities", "entity_supersedes")
    op.drop_column("memory_entities", "entity_evidence_count")
    op.drop_column("memory_entities", "valid_until")
    op.drop_column("memory_entities", "valid_from")

    # Drop memory_observations M2 columns + constraints + index
    op.execute("DROP INDEX IF EXISTS idx_obs_scope_valid_now")
    op.drop_constraint("fk_obs_supersedes", "memory_observations", type_="foreignkey")
    op.execute(
        "ALTER TABLE memory_observations DROP CONSTRAINT IF EXISTS ck_obs_valid_order"
    )
    op.execute(
        "ALTER TABLE memory_observations DROP CONSTRAINT IF EXISTS ck_obs_confidence"
    )
    op.drop_column("memory_observations", "evidence_count")
    op.drop_column("memory_observations", "supersedes")
    op.drop_column("memory_observations", "confidence")
