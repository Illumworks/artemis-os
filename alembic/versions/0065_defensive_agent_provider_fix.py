"""Defensive fix — repoint broken agents to claude-code + set fallback providers.

Fixes three agents that had silent-fail provider configs:

1. WS Integration Agent (id=1, agent_id='ws-rid-agent'):
   provider='anthropic' + NULL fallback → fails on every call (no ANTHROPIC_API_KEY).
   Set provider='claude-code', fallback_provider='anthropic'.

2. Smoke Test Agent (id=2, agent_id='smoke-agent'):
   Same issue. Set provider='claude-code', fallback_provider='anthropic'.

3. Mock Post Gate (id=172, agent_id='mock.post.gate'):
   provider='claude-code' but fallback_provider=NULL → no fallback if CLI unavailable.
   Set fallback_provider='anthropic'.

All UPDATEs are idempotent (guarded by WHERE clauses on agent_id/name) and
lossless (no DELETEs, no schema changes — config columns only).

Source: briefs/defensive-fix-bundle.md fix #1 and #2.

Revision ID: 0065
Revises: 0064
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0065"
down_revision: str = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # Fix 1 & 2: Smoke Test Agent + WS Integration Agent
    # Guard: only update rows that still have provider='anthropic' (idempotent).
    bind.execute(
        sa.text("""
            UPDATE agents
               SET provider          = 'claude-code',
                   fallback_provider = 'anthropic'
             WHERE agent_id IN ('smoke-agent', 'ws-rid-agent')
               AND provider = 'anthropic'
        """)
    )

    # Fix 3: Mock Post Gate — set fallback_provider when still NULL.
    # Guard: only update rows where fallback_provider IS NULL (idempotent).
    bind.execute(
        sa.text("""
            UPDATE agents
               SET fallback_provider = 'anthropic'
             WHERE agent_id = 'mock.post.gate'
               AND fallback_provider IS NULL
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Restore Smoke Test Agent + WS Integration Agent to original state.
    bind.execute(
        sa.text("""
            UPDATE agents
               SET provider          = 'anthropic',
                   fallback_provider = NULL
             WHERE agent_id IN ('smoke-agent', 'ws-rid-agent')
               AND provider = 'claude-code'
               AND fallback_provider = 'anthropic'
        """)
    )

    # Restore Mock Post Gate fallback to NULL.
    bind.execute(
        sa.text("""
            UPDATE agents
               SET fallback_provider = NULL
             WHERE agent_id = 'mock.post.gate'
               AND fallback_provider = 'anthropic'
        """)
    )
