"""integration_configs — per-provider credential storage.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-17

Phase J1b: encrypted provider credentials stored in DB, DB value wins over .env.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS integration_configs (
            id              BIGSERIAL PRIMARY KEY,
            provider        TEXT NOT NULL UNIQUE,
            encrypted_payload BYTEA NOT NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by      TEXT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_integration_configs_provider "
        "ON integration_configs (provider)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration_configs")
