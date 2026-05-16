"""floating_artemis — sessions, messages, voice_corpus, page_context, active_runs view.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-16

Phase G1: Floating Artemis backend data layer.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Sessions table — one persistent conversation context per operator session.
    op.execute("""
        CREATE TABLE IF NOT EXISTS floating_artemis_sessions (
            id              BIGSERIAL PRIMARY KEY,
            session_id      TEXT UNIQUE NOT NULL,
            owner_user_id   BIGINT,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_at       TIMESTAMPTZ,
            title           TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fa_sessions_owner "
        "ON floating_artemis_sessions (owner_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fa_sessions_closed "
        "ON floating_artemis_sessions (closed_at) WHERE closed_at IS NULL"
    )

    # 2. Messages table — ordered conversation history with token cost tracking.
    op.execute("""
        CREATE TABLE IF NOT EXISTS floating_artemis_messages (
            id                          BIGSERIAL PRIMARY KEY,
            session_id                  TEXT NOT NULL
                REFERENCES floating_artemis_sessions(session_id) ON DELETE CASCADE,
            role                        TEXT NOT NULL,
            content                     JSONB NOT NULL,
            cost_input_tokens           BIGINT NOT NULL DEFAULT 0,
            cost_output_tokens          BIGINT NOT NULL DEFAULT 0,
            cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
            cache_read_input_tokens     BIGINT NOT NULL DEFAULT 0,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fa_messages_session_created "
        "ON floating_artemis_messages (session_id, created_at)"
    )

    # 3. Voice corpus — characteristic phrases drawn from personality profile.
    op.execute("""
        CREATE TABLE IF NOT EXISTS floating_artemis_voice_corpus (
            id              BIGSERIAL PRIMARY KEY,
            owner_user_id   BIGINT,
            line            TEXT NOT NULL UNIQUE,
            context_tag     TEXT,
            first_used_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            use_count       BIGINT NOT NULL DEFAULT 0,
            source          TEXT NOT NULL DEFAULT 'seed'
                CHECK (source IN ('seed', 'observed', 'operator_pinned')),
            active          BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fa_voice_corpus_active "
        "ON floating_artemis_voice_corpus (active, source)"
    )

    # 4. Page context — current UI surface Artemis is aware of per session.
    op.execute("""
        CREATE TABLE IF NOT EXISTS floating_artemis_page_context (
            id          BIGSERIAL PRIMARY KEY,
            session_id  TEXT NOT NULL
                REFERENCES floating_artemis_sessions(session_id) ON DELETE CASCADE,
            page        TEXT NOT NULL,
            ref_id      TEXT,
            set_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fa_page_context_session "
        "ON floating_artemis_page_context (session_id, set_at DESC)"
    )

    # 5. View: union of running/queued agent + workflow runs.
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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_floating_artemis_active_runs")
    op.execute("DROP TABLE IF EXISTS floating_artemis_page_context")
    op.execute("DROP TABLE IF EXISTS floating_artemis_voice_corpus")
    op.execute("DROP TABLE IF EXISTS floating_artemis_messages")
    op.execute("DROP TABLE IF EXISTS floating_artemis_sessions")
