"""Backfill deliverable draft body into canonical versions[0].content.

The writing_studio.enqueue tool previously wrote the agent-generated body only
to ``metadata.draftBody``.  The composer (_latest_draft_content / the WS detail
route) reads from ``metadata.live_content`` → ``metadata.versions[0].content``,
so these deliverables rendered as empty in the editor.

This migration copies ``draftBody`` into a ``versions`` array for every
campaign_deliverables row that has a non-empty ``draftBody`` but no ``versions``
entry yet.  The ``draftBody`` field is retained for backwards compatibility (the
Slack gate-card path still reads it).

Revision ID: 0079_backfill_deliverable_draft_body_versions
Revises: 0078_multi_bot_slack_agent_ids
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0079"
down_revision = "0078_multi_bot_slack_agent_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Copy draftBody → versions[0].content for rows that are missing versions."""
    op.execute(
        sa.text(
            """
            UPDATE campaign_deliverables
            SET metadata = jsonb_set(
                metadata,
                '{versions}',
                jsonb_build_array(
                    jsonb_build_object(
                        'id',             'v1',
                        'version_number', 1,
                        'content',        metadata->>'draftBody',
                        'created_at',     to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                        'source',         'pipeline_generated'
                    )
                ),
                true
            )
            WHERE
                metadata ? 'draftBody'
                AND (metadata->>'draftBody') <> ''
                AND NOT (metadata ? 'versions')
            """
        )
    )


def downgrade() -> None:
    """Remove versions that were stamped with source='pipeline_generated' and not edited since.

    This is a best-effort rollback: rows where the operator subsequently saved a
    new version (source != 'pipeline_generated') are left untouched so that work
    is not lost.
    """
    op.execute(
        sa.text(
            """
            UPDATE campaign_deliverables
            SET metadata = metadata - 'versions'
            WHERE
                metadata ? 'versions'
                AND jsonb_array_length(metadata->'versions') = 1
                AND (metadata->'versions'->0->>'source') = 'pipeline_generated'
                AND metadata ? 'draftBody'
            """
        )
    )
