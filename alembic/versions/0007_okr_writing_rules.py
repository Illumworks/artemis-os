"""OKR Studio + Writing Studio rules tables

Revision ID: 0007
Revises: 0005
Create Date: 2026-05-16

Tables added:
  OKR Studio:
    okr_objectives, okr_key_results, okr_activity, okr_next_up,
    okr_update_previews

  Writing Studio rules + scaffolding (NOT drafts):
    writing_profiles, writing_folders, writing_rules,
    writing_examples, writing_sources

Intentional improvements over the Node/SQLite schema:
- TIMESTAMPTZ for all timestamps (Node uses INTEGER unix-seconds)
- JSONB for all JSON columns (Node uses TEXT)
- BIGSERIAL PKs
- owner_user_id BIGINT NULL on top-level rows (writing_profiles, okr_objectives)
  for multi-user readiness

Explicitly NOT migrated (per decisions/rebuild-phased-plan.md Phase H):
  writing_drafts, writing_draft_versions, writing_draft_thread_messages,
  writing_training_candidates, writing_deliverable_links, writing_draft_events
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── OKR Objectives ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS okr_objectives (
            id               BIGSERIAL PRIMARY KEY,
            title            TEXT NOT NULL,
            description      TEXT,
            progress         INTEGER NOT NULL DEFAULT 0,
            tone             TEXT NOT NULL DEFAULT 'sage',
            owner            TEXT,
            weight           TEXT,
            cycle            TEXT,
            sort_order       INTEGER NOT NULL DEFAULT 0,
            rolls_up_to      TEXT,
            archived_at      TIMESTAMPTZ,
            archive_reason   TEXT,
            source_year      INTEGER,
            owner_user_id    BIGINT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_okr_objectives_cycle
            ON okr_objectives(cycle)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_okr_objectives_owner
            ON okr_objectives(owner)
    """)

    # ── OKR Key Results ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS okr_key_results (
            id               BIGSERIAL PRIMARY KEY,
            objective_id     BIGINT NOT NULL REFERENCES okr_objectives(id) ON DELETE CASCADE,
            title            TEXT NOT NULL,
            prog             INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'notstarted',
            done_bullets     JSONB NOT NULL DEFAULT '[]',
            gaps_bullets     JSONB NOT NULL DEFAULT '[]',
            note             TEXT,
            sort_order       INTEGER NOT NULL DEFAULT 0,
            archived_at      TIMESTAMPTZ,
            archive_reason   TEXT,
            source_year      INTEGER,
            target_text      TEXT,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_okr_krs_objective
            ON okr_key_results(objective_id, sort_order)
    """)

    # ── OKR Activity ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS okr_activity (
            id                  BIGSERIAL PRIMARY KEY,
            text                TEXT NOT NULL,
            kr_id               BIGINT REFERENCES okr_key_results(id) ON DELETE SET NULL,
            kr_label            TEXT,
            raw_text            TEXT,
            mapping_confidence  FLOAT,
            cleaned_at          TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_okr_activity_created
            ON okr_activity(created_at DESC)
    """)

    # ── OKR Next Up ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS okr_next_up (
            id               BIGSERIAL PRIMARY KEY,
            ref              TEXT NOT NULL DEFAULT '—',
            text             TEXT NOT NULL,
            prio             TEXT NOT NULL DEFAULT 'med',
            sort_order       INTEGER NOT NULL DEFAULT 0,
            dismissed_at     TIMESTAMPTZ,
            source           TEXT NOT NULL DEFAULT 'manual',
            action_type      TEXT NOT NULL DEFAULT 'advice',
            dispatch_target  TEXT,
            dispatch_params  JSONB,
            generated_at     TIMESTAMPTZ,
            rationale        TEXT
        )
    """)

    # ── OKR Update Previews (ephemeral, skip if empty) ───────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS okr_update_previews (
            id               BIGSERIAL PRIMARY KEY,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            raw_input        TEXT,
            input_format     TEXT NOT NULL DEFAULT 'text',
            diff_json        JSONB,
            committed_at     TIMESTAMPTZ
        )
    """)

    # ── Writing Profiles ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS writing_profiles (
            id                       BIGSERIAL PRIMARY KEY,
            name                     TEXT NOT NULL,
            description              TEXT,
            status                   TEXT NOT NULL DEFAULT 'active',
            default_model_provider   TEXT,
            default_model_id         TEXT,
            system_prompt            TEXT,
            owner_user_id            BIGINT,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_profiles_status
            ON writing_profiles(status)
    """)

    # ── Writing Folders ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS writing_folders (
            id               BIGSERIAL PRIMARY KEY,
            sync_id          TEXT,
            profile_id       BIGINT REFERENCES writing_profiles(id),
            parent_folder_id BIGINT REFERENCES writing_folders(id) ON DELETE SET NULL,
            name             TEXT NOT NULL,
            description      TEXT,
            campaign_id      TEXT,
            metadata_json    JSONB,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_folders_profile
            ON writing_folders(profile_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_folders_campaign
            ON writing_folders(campaign_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_folders_parent
            ON writing_folders(parent_folder_id)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_writing_folders_sync
            ON writing_folders(sync_id) WHERE sync_id IS NOT NULL
    """)

    # ── Writing Rules ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS writing_rules (
            id                   BIGSERIAL PRIMARY KEY,
            profile_id           BIGINT REFERENCES writing_profiles(id),
            rule_type            TEXT NOT NULL DEFAULT 'voice',
            title                TEXT NOT NULL,
            body                 TEXT NOT NULL,
            source_candidate_id  BIGINT,
            status               TEXT NOT NULL DEFAULT 'active',
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_rules_profile
            ON writing_rules(profile_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_rules_status
            ON writing_rules(status)
    """)
    # Natural-key uniqueness: (profile_id, rule_type, title) when not archived
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_writing_rules_profile_type_title_active
            ON writing_rules(profile_id, rule_type, title)
            WHERE status != 'archived'
    """)

    # ── Writing Examples ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS writing_examples (
            id                   BIGSERIAL PRIMARY KEY,
            profile_id           BIGINT REFERENCES writing_profiles(id),
            title                TEXT NOT NULL,
            body                 TEXT NOT NULL,
            example_type         TEXT NOT NULL DEFAULT 'reference',
            asset_type           TEXT,
            channel              TEXT,
            source_candidate_id  BIGINT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_examples_profile
            ON writing_examples(profile_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_examples_type
            ON writing_examples(example_type)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_writing_examples_profile_title_type
            ON writing_examples(profile_id, title, example_type)
    """)

    # ── Writing Sources ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS writing_sources (
            id                  BIGSERIAL PRIMARY KEY,
            profile_id          BIGINT REFERENCES writing_profiles(id),
            source_key          TEXT NOT NULL,
            title               TEXT NOT NULL,
            source_type         TEXT NOT NULL DEFAULT 'reference',
            file_name           TEXT,
            original_content    TEXT NOT NULL,
            normalized_content  TEXT NOT NULL,
            content_hash        TEXT,
            metadata_json       JSONB,
            imported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(profile_id, source_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_sources_profile
            ON writing_sources(profile_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_writing_sources_type
            ON writing_sources(source_type)
    """)


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS writing_sources")
    op.execute("DROP TABLE IF EXISTS writing_examples")
    op.execute("DROP TABLE IF EXISTS writing_rules")
    op.execute("DROP TABLE IF EXISTS writing_folders")
    op.execute("DROP TABLE IF EXISTS writing_profiles")
    op.execute("DROP TABLE IF EXISTS okr_update_previews")
    op.execute("DROP TABLE IF EXISTS okr_next_up")
    op.execute("DROP TABLE IF EXISTS okr_activity")
    op.execute("DROP TABLE IF EXISTS okr_key_results")
    op.execute("DROP TABLE IF EXISTS okr_objectives")
