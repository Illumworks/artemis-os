"""marketing-OS tables — signal_queue, scout_runs, campaign_candidates, campaign_briefs,
content_assets, content_asset_links, campaign_deliverables, rulesets, territory_config, approvals

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16

Intentional improvements over the Node/SQLite schema:
- TIMESTAMPTZ for all timestamps (Node uses INTEGER unix-seconds or TEXT datetime())
- JSONB for all JSON columns (Node uses TEXT)
- BIGSERIAL PKs (except scout_runs.id which is a structured TEXT)
- owner_user_id BIGINT NULL on user-facing tables (signal_queue, campaign_candidates,
  content_assets) for multi-user readiness
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── signal_queue ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS signal_queue (
            id                               BIGSERIAL PRIMARY KEY,
            source_type                      TEXT NOT NULL DEFAULT 'manual',
            source_url                       TEXT,
            source_id                        TEXT,
            headline                         TEXT NOT NULL,
            summary                          TEXT NOT NULL DEFAULT '',
            campaign_family                  TEXT NOT NULL,
            urgency_tier                     TEXT NOT NULL DEFAULT 'standard',
            discovered_by                    TEXT NOT NULL DEFAULT 'manual',
            district_id                      TEXT,
            state                            TEXT,
            reason_codes                     JSONB NOT NULL DEFAULT '[]',
            provenance                       JSONB,
            qualification_json               JSONB,
            signal_status                    TEXT NOT NULL DEFAULT 'in_inbox',
            snoozed_until                    TIMESTAMPTZ,
            rejected_reason                  TEXT,
            owner_user_id                    BIGINT,
            created_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_queue_status_tier
            ON signal_queue(signal_status, urgency_tier)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_queue_family_status
            ON signal_queue(campaign_family, signal_status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_queue_district
            ON signal_queue(district_id)
    """)

    # ── scout_runs ────────────────────────────────────────────────────────────
    # id is a structured TEXT: scout_run_YYYYMMDD_<type>_<uuid8>
    op.execute("""
        CREATE TABLE IF NOT EXISTS scout_runs (
            id                  TEXT PRIMARY KEY,
            scout_type          TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'pending',
            dry_run_summary     JSONB,
            created_signal_ids  JSONB NOT NULL DEFAULT '[]',
            errors              JSONB NOT NULL DEFAULT '[]',
            started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at        TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_scout_runs_type_started
            ON scout_runs(scout_type, started_at DESC)
    """)

    # ── campaign_candidates ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS campaign_candidates (
            id                              BIGSERIAL PRIMARY KEY,
            source_signal_id                BIGINT REFERENCES signal_queue(id) ON DELETE SET NULL,
            campaign_family                 TEXT NOT NULL,
            stage                           TEXT NOT NULL DEFAULT 'human_gate_1',
            decision_state                  TEXT NOT NULL DEFAULT 'pending_review',
            workspace_state                 TEXT NOT NULL DEFAULT 'created',
            ruleset_version_at_qualification TEXT,
            metrics_json                    JSONB,
            deliverables                    JSONB,
            owner_user_id                   BIGINT,
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_candidates_decision_state
            ON campaign_candidates(decision_state)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_candidates_family
            ON campaign_candidates(campaign_family)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_candidates_updated
            ON campaign_candidates(updated_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_candidates_source_signal
            ON campaign_candidates(source_signal_id)
    """)

    # ── campaign_briefs ───────────────────────────────────────────────────────
    # Append-only: re-assembly creates version N+1
    op.execute("""
        CREATE TABLE IF NOT EXISTS campaign_briefs (
            id              BIGSERIAL PRIMARY KEY,
            candidate_id    BIGINT NOT NULL REFERENCES campaign_candidates(id) ON DELETE CASCADE,
            content         JSONB NOT NULL DEFAULT '{}',
            generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            generated_by    TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_briefs_candidate
            ON campaign_briefs(candidate_id, generated_at DESC)
    """)

    # ── content_assets ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS content_assets (
            id              BIGSERIAL PRIMARY KEY,
            asset_type      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'draft',
            summary         TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}',
            owner_user_id   BIGINT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_assets_status
            ON content_assets(status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_assets_type
            ON content_assets(asset_type)
    """)

    # ── content_asset_links ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS content_asset_links (
            id              BIGSERIAL PRIMARY KEY,
            candidate_id    BIGINT NOT NULL REFERENCES campaign_candidates(id) ON DELETE CASCADE,
            asset_id        BIGINT NOT NULL REFERENCES content_assets(id) ON DELETE CASCADE,
            link_role       TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_content_asset_links_candidate_asset
                UNIQUE (candidate_id, asset_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_asset_links_candidate
            ON content_asset_links(candidate_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_content_asset_links_asset
            ON content_asset_links(asset_id)
    """)

    # ── campaign_deliverables ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS campaign_deliverables (
            id              BIGSERIAL PRIMARY KEY,
            candidate_id    BIGINT NOT NULL REFERENCES campaign_candidates(id) ON DELETE CASCADE,
            deliverable_id  TEXT,
            campaign_id     TEXT,
            status          TEXT NOT NULL DEFAULT 'generating',
            metadata        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_deliverables_candidate
            ON campaign_deliverables(candidate_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_campaign_deliverables_status
            ON campaign_deliverables(status)
    """)

    # ── rulesets ──────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS rulesets (
            id                  BIGSERIAL PRIMARY KEY,
            family              TEXT NOT NULL,
            version_tag         TEXT NOT NULL,
            hard_filters        JSONB NOT NULL DEFAULT '[]',
            weighted_signals    JSONB NOT NULL DEFAULT '[]',
            qualitative_rubrics JSONB NOT NULL DEFAULT '[]',
            state               TEXT NOT NULL DEFAULT 'draft',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_rulesets_family_version UNIQUE (family, version_tag)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rulesets_family_state
            ON rulesets(family, state)
    """)

    # ── territory_config ──────────────────────────────────────────────────────
    # One row per family (unique constraint enforced)
    op.execute("""
        CREATE TABLE IF NOT EXISTS territory_config (
            id                  BIGSERIAL PRIMARY KEY,
            family              TEXT NOT NULL UNIQUE,
            hot_states          JSONB NOT NULL DEFAULT '[]',
            standard_states     JSONB NOT NULL DEFAULT '[]',
            unlisted_multiplier REAL NOT NULL DEFAULT 0.85,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── approvals ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            id              BIGSERIAL PRIMARY KEY,
            kind            TEXT NOT NULL,
            subject_id      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            decided_by      TEXT,
            decided_at      TIMESTAMPTZ,
            decision_payload JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approvals_status_created
            ON approvals(status, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_approvals_kind_subject
            ON approvals(kind, subject_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approvals")
    op.execute("DROP TABLE IF EXISTS territory_config")
    op.execute("DROP TABLE IF EXISTS rulesets")
    op.execute("DROP TABLE IF EXISTS campaign_deliverables")
    op.execute("DROP TABLE IF EXISTS content_asset_links")
    op.execute("DROP TABLE IF EXISTS content_assets")
    op.execute("DROP TABLE IF EXISTS campaign_briefs")
    op.execute("DROP TABLE IF EXISTS campaign_candidates")
    op.execute("DROP TABLE IF EXISTS scout_runs")
    op.execute("DROP TABLE IF EXISTS signal_queue")
