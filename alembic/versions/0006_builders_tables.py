"""builders tables — agents, agent_runs, agent_context, skills, workflows,
workflow_runs, agent_chains, agent_dags.

Phase F2a: Data layer for the Builders surfaces (CRUD + routes).
Execution wiring comes in F2b.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id              BIGSERIAL PRIMARY KEY,
            agent_id        TEXT      UNIQUE NOT NULL,
            name            TEXT      NOT NULL,
            description     TEXT,
            goal            TEXT,
            system_prompt   TEXT,
            tools           JSONB     NOT NULL DEFAULT '[]',
            model           TEXT      NOT NULL DEFAULT 'claude-sonnet-4-6',
            provider        TEXT      NOT NULL DEFAULT 'anthropic',
            max_iterations  INT       NOT NULL DEFAULT 10,
            owner_user_id   BIGINT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agents_agent_id ON agents (agent_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id                  BIGSERIAL PRIMARY KEY,
            run_id              TEXT      UNIQUE NOT NULL,
            agent_id            TEXT      REFERENCES agents(agent_id) ON DELETE SET NULL,
            status              TEXT      NOT NULL DEFAULT 'queued',
            user_message        TEXT,
            shared_context      JSONB,
            started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at        TIMESTAMPTZ,
            cost_input_tokens   BIGINT    NOT NULL DEFAULT 0,
            cost_output_tokens  BIGINT    NOT NULL DEFAULT 0,
            error               TEXT,
            owner_user_id       BIGINT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_status ON agent_runs (agent_id, status)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs (started_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_context (
            id          BIGSERIAL PRIMARY KEY,
            run_id      TEXT      NOT NULL
                            REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            key         TEXT      NOT NULL,
            value       JSONB     NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT  uq_agent_context_run_key UNIQUE (run_id, key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_context_run_id ON agent_context (run_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id           BIGSERIAL PRIMARY KEY,
            slug         TEXT      UNIQUE NOT NULL,
            name         TEXT      NOT NULL,
            description  TEXT,
            instructions TEXT,
            tools        JSONB     NOT NULL DEFAULT '[]',
            kind         TEXT      NOT NULL DEFAULT 'user',
            source_path  TEXT,
            owner_user_id BIGINT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_skills_slug ON skills (slug)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS workflows (
            id           BIGSERIAL PRIMARY KEY,
            workflow_id  TEXT      UNIQUE NOT NULL,
            name         TEXT      NOT NULL,
            description  TEXT,
            steps        JSONB     NOT NULL DEFAULT '[]',
            owner_user_id BIGINT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflows_workflow_id ON workflows (workflow_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id                BIGSERIAL PRIMARY KEY,
            run_id            TEXT      UNIQUE NOT NULL,
            workflow_id       TEXT      REFERENCES workflows(workflow_id) ON DELETE SET NULL,
            status            TEXT      NOT NULL DEFAULT 'queued',
            current_step      INT       NOT NULL DEFAULT 0,
            claude_session_id TEXT,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at      TIMESTAMPTZ,
            total_cost_usd    REAL      NOT NULL DEFAULT 0,
            owner_user_id     BIGINT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_id ON workflow_runs (workflow_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_chains (
            id           BIGSERIAL PRIMARY KEY,
            chain_id     TEXT      UNIQUE NOT NULL,
            name         TEXT,
            description  TEXT,
            steps        JSONB     NOT NULL DEFAULT '[]',
            owner_user_id BIGINT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_chains_chain_id ON agent_chains (chain_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_dags (
            id           BIGSERIAL PRIMARY KEY,
            dag_id       TEXT      UNIQUE NOT NULL,
            name         TEXT,
            description  TEXT,
            nodes        JSONB     NOT NULL DEFAULT '[]',
            owner_user_id BIGINT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_dags_dag_id ON agent_dags (dag_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_context CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS agents CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS workflows CASCADE")
    op.execute("DROP TABLE IF EXISTS skills CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_chains CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_dags CASCADE")
