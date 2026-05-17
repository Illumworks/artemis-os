"""integrations + slack_inbound_messages tables.

Revision ID: 0011
Revises: 0009
Create Date: 2026-05-17

Phase J1: Slack integration — provider credentials store + inbound event cache.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS integrations (
            id                      BIGSERIAL PRIMARY KEY,
            provider                TEXT NOT NULL,
            workspace_id            TEXT NOT NULL,
            display_name            TEXT,
            bot_user_id             TEXT,
            encrypted_credentials   BYTEA NOT NULL,
            scopes                  TEXT[],
            connected_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_verified_at        TIMESTAMPTZ,
            status                  TEXT NOT NULL DEFAULT 'active',
            metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT uq_integrations_provider_workspace UNIQUE (provider, workspace_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_integrations_provider "
        "ON integrations (provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_integrations_status "
        "ON integrations (status)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS slack_inbound_messages (
            event_id            TEXT PRIMARY KEY,
            team_id             TEXT NOT NULL,
            channel_id          TEXT NOT NULL,
            user_id             TEXT NOT NULL,
            text                TEXT,
            ts                  TEXT NOT NULL,
            thread_ts           TEXT,
            routed_to_session_id BIGINT
                REFERENCES floating_artemis_sessions(id) ON DELETE SET NULL,
            received_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_slack_inbound_team_channel "
        "ON slack_inbound_messages (team_id, channel_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_slack_inbound_received "
        "ON slack_inbound_messages (received_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS slack_inbound_messages")
    op.execute("DROP TABLE IF EXISTS integrations")
