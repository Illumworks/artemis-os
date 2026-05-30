"""memory_observation_scopes — multi-scope join table + wing + confidence_origin

Revision ID: 0048
Revises: 0047
Create Date: 2026-05-30

Changes:
  - New table memory_observation_scopes: many-to-many join between
    memory_observations and memory_scopes. Each row records one scope
    an observation belongs to. is_primary=TRUE marks the primary scope
    (mirrors the legacy scope_kind/scope_id columns on memory_observations).
  - memory_observations.wing TEXT NOT NULL DEFAULT 'durable'
  - memory_observations.confidence_origin TEXT nullable
  - Backfill: INSERT one is_primary=TRUE row per existing observation
    mirroring its current scope_kind/scope_id. Idempotent (ON CONFLICT DO
    NOTHING). Does NOT touch any existing observation column.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. New columns on memory_observations ────────────────────────────────
    op.add_column(
        "memory_observations",
        sa.Column(
            "wing",
            sa.Text(),
            nullable=False,
            server_default="durable",
        ),
    )
    op.add_column(
        "memory_observations",
        sa.Column("confidence_origin", sa.Text(), nullable=True),
    )

    # ── 2. New join table ─────────────────────────────────────────────────────
    op.create_table(
        "memory_observation_scopes",
        sa.Column("observation_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column(
            "weight",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("observation_id", "scope_kind", "scope_id"),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["memory_observations.id"],
            name="fk_obs_scopes_observation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_kind", "scope_id"],
            ["memory_scopes.scope_kind", "memory_scopes.scope_id"],
            name="fk_obs_scopes_scope",
            ondelete="RESTRICT",
        ),
    )

    # ── 3. Indexes ────────────────────────────────────────────────────────────
    op.create_index(
        "idx_memory_observation_scopes_obs",
        "memory_observation_scopes",
        ["observation_id"],
    )
    op.create_index(
        "idx_memory_observation_scopes_scope",
        "memory_observation_scopes",
        ["scope_kind", "scope_id"],
    )
    op.create_index(
        "idx_memory_observation_scopes_primary",
        "memory_observation_scopes",
        ["observation_id"],
        postgresql_where=sa.text("is_primary = TRUE"),
    )

    # ── 4. Backfill: mirror existing observations → join table ────────────────
    # Idempotent: ON CONFLICT DO NOTHING means re-running is safe.
    # Does NOT modify any existing memory_observations row (lossless invariant).
    op.execute(
        sa.text(
            """
            INSERT INTO memory_observation_scopes
                (observation_id, scope_kind, scope_id, weight, is_primary)
            SELECT id, scope_kind, scope_id, 1.0, TRUE
            FROM memory_observations
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "idx_memory_observation_scopes_primary",
        table_name="memory_observation_scopes",
    )
    op.drop_index(
        "idx_memory_observation_scopes_scope",
        table_name="memory_observation_scopes",
    )
    op.drop_index(
        "idx_memory_observation_scopes_obs",
        table_name="memory_observation_scopes",
    )
    op.drop_table("memory_observation_scopes")
    op.drop_column("memory_observations", "confidence_origin")
    op.drop_column("memory_observations", "wing")
