# Artemis OS — Database Setup

PostgreSQL 15+ with JSONB. Provision this before building any agent.

## Connection

`.env` requires:
```
ARTEMIS_DB_URL=postgresql://artemis:CHANGE_ME@localhost:5432/artemis_os
```

## Migrations

Use Alembic. Initial migration creates all tables below. Subsequent migrations are append-only — never drop columns from `signal_queue`, `signal_briefs`, or `ruleset_versions`.

## Tables

### signal_queue

Append-only queue of every signal emitted by any scout. The single source of truth for what scouts have detected.

```sql
CREATE TABLE signal_queue (
    signal_id           TEXT PRIMARY KEY,
    discovered_at       TIMESTAMPTZ NOT NULL,
    discovered_by       TEXT NOT NULL,
    discovery_mode      TEXT NOT NULL CHECK (discovery_mode IN ('scheduled', 'event', 'batch')),
    priority            TEXT NOT NULL CHECK (priority IN ('hot', 'standard', 'enrichment')),
    status              TEXT NOT NULL DEFAULT 'pending_qualification',
    signal              JSONB NOT NULL,
    fingerprint         TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signal_queue_status ON signal_queue (status) WHERE status IN ('pending_qualification', 'in_qualification');
CREATE INDEX idx_signal_queue_district ON signal_queue ((signal->'geography'->>'district_id'));
CREATE INDEX idx_signal_queue_discovered_by ON signal_queue (discovered_by);
CREATE INDEX idx_signal_queue_fingerprint ON signal_queue (fingerprint);
```

Status values (lifecycle):
- `pending_qualification` — scout wrote it, Qualifier hasn't seen it
- `rejected_hard_filter` — Qualifier Phase 1 rejected
- `in_qualification` — Qualifier Phase 2 running
- `rejected_low_fit` — Qualifier Phase 2 found no ruleset scoring > 0.4
- `qualified` — Phase 3 found one or more campaign types > 0.7
- `brief_composed` — Brief Composer wrote a signal_briefs row
- `pending_human_review` — in Signals Inbox
- `rejected_by_human`
- `snoozed`
- `approved` — campaign_workspace row created
- `in_content_preparation`
- `sent_to_writing_studio`
- `content_preparation_failed`

### signal_briefs

Output of 2.4 Brief Composer Agent. Human-readable inbox cards for Gate 1.

```sql
CREATE TABLE signal_briefs (
    brief_id            TEXT PRIMARY KEY,
    signal_id           TEXT NOT NULL REFERENCES signal_queue(signal_id),
    headline            TEXT NOT NULL,
    why_flagged         TEXT NOT NULL,
    evidence            TEXT NOT NULL,
    fit_scores          JSONB NOT NULL,
    suggested_campaign  TEXT NOT NULL,
    related_history     JSONB,
    urgency             JSONB NOT NULL,
    actions_taken       TEXT,
    snooze_until        TIMESTAMPTZ,
    rejected_reason     TEXT,
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'pending_human_review',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signal_briefs_status ON signal_briefs (status);
CREATE INDEX idx_signal_briefs_signal ON signal_briefs (signal_id);
```

### campaign_workspaces

Created when a signal brief is approved at Gate 1. The container that Content team operates on.

```sql
CREATE TABLE campaign_workspaces (
    workspace_id        TEXT PRIMARY KEY,
    brief_id            TEXT NOT NULL REFERENCES signal_briefs(brief_id),
    signal_id           TEXT NOT NULL REFERENCES signal_queue(signal_id),
    campaign_type       TEXT NOT NULL,
    district_id         TEXT NOT NULL,
    approved_by         TEXT NOT NULL,
    approved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    campaign_brief      JSONB,
    asset_bundle        JSONB,
    writing_studio_drafts JSONB DEFAULT '[]'::jsonb,
    status              TEXT NOT NULL DEFAULT 'pending_content'
);

CREATE INDEX idx_campaign_workspaces_status ON campaign_workspaces (status);
```

### memory_layer

Dedupe memory used by scouts. One row per `(district_id, reason_code)` combination.

```sql
CREATE TABLE memory_layer (
    district_id         TEXT NOT NULL,
    reason_code         TEXT NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    last_signal_id      TEXT REFERENCES signal_queue(signal_id),
    embedding_hash      TEXT,
    last_material_change_at TIMESTAMPTZ,
    PRIMARY KEY (district_id, reason_code)
);
```

### unresolved_signals

Where scouts log signals they couldn't resolve to a canonical district. Manual review queue.

```sql
CREATE TABLE unresolved_signals (
    unresolved_id       TEXT PRIMARY KEY,
    discovered_by       TEXT NOT NULL,
    raw_payload         JSONB NOT NULL,
    reason              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    resolved_to_district_id TEXT
);
```

### ruleset_versions

Append-only versioned ruleset storage. Every ruleset edit creates a new row.

```sql
CREATE TABLE ruleset_versions (
    ruleset_id          TEXT NOT NULL,
    version             INTEGER NOT NULL,
    yaml_source         TEXT NOT NULL,
    compiled            JSONB NOT NULL,
    hit_rate            NUMERIC(5,4),
    author              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (ruleset_id, version)
);

CREATE INDEX idx_ruleset_versions_active ON ruleset_versions (ruleset_id) WHERE is_active = TRUE;
```

Only one row per `ruleset_id` may have `is_active = TRUE` at any time. The Ruleset Compiler enforces this via transaction.

### districts

NCES-derived district roster. Read-only for agents.

```sql
CREATE TABLE districts (
    district_id         TEXT PRIMARY KEY,
    nces_id             TEXT UNIQUE,
    name                TEXT NOT NULL,
    state               TEXT NOT NULL,
    enrollment          INTEGER,
    grade_range         TEXT,
    superintendent      TEXT,
    is_watch_list       BOOLEAN NOT NULL DEFAULT FALSE,
    metadata            JSONB DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_districts_state ON districts (state);
CREATE INDEX idx_districts_watch_list ON districts (is_watch_list) WHERE is_watch_list = TRUE;
```

Populated via NCES Common Core of Data import script (run once at setup, refresh annually).

### district_aliases

Fuzzy-match lookup table for entity resolution. Maps name variants to canonical district_id.

```sql
CREATE TABLE district_aliases (
    alias_id            SERIAL PRIMARY KEY,
    district_id         TEXT NOT NULL REFERENCES districts(district_id),
    alias_name          TEXT NOT NULL,
    state               TEXT NOT NULL,
    source              TEXT,
    confidence          NUMERIC(3,2) DEFAULT 1.0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_district_aliases_alias ON district_aliases (LOWER(alias_name), state);
```

### territory_config

Single row table. Holds the current territory configuration.

```sql
CREATE TABLE territory_config (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    priority_states     TEXT[] NOT NULL,
    watch_keywords      JSONB NOT NULL,
    deprioritized       JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_by          TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO territory_config (id, priority_states, watch_keywords)
VALUES (1, ARRAY['FL','IN','MD','MO','MI','IL','TX'],
        '{"OBC": [], "dyslexia": [], "biliteracy": []}'::jsonb);
```

### reason_code_registry

The canonical list of reason codes scouts may emit. Append-only.

```sql
CREATE TABLE reason_code_registry (
    code                TEXT PRIMARY KEY,
    description         TEXT NOT NULL,
    source_scout        TEXT,
    seeded              BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deprecated_at       TIMESTAMPTZ
);
```

Seed values: see `schemas/reason-codes.md`.

### proposed_reason_codes

Where scouts log when they want a code that doesn't exist in the registry. Manual review.

```sql
CREATE TABLE proposed_reason_codes (
    proposed_id         SERIAL PRIMARY KEY,
    proposed_code       TEXT NOT NULL,
    proposed_by         TEXT NOT NULL,
    sample_evidence     TEXT NOT NULL,
    signal_id           TEXT REFERENCES signal_queue(signal_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at         TIMESTAMPTZ,
    rejected_at         TIMESTAMPTZ
);
```

## Indexes you will want eventually (not required for v1)

- Trigram index on `district_aliases.alias_name` for fuzzy matching at scale (`pg_trgm` extension).
- Partial index on `signal_queue` by `signal->'urgency'->>'tier'` for hot-signal queries.

## Migration ordering

Codex: create tables in this order to satisfy FK constraints:

1. `districts`
2. `district_aliases`
3. `reason_code_registry`
4. `territory_config`
5. `signal_queue`
6. `memory_layer`
7. `unresolved_signals`
8. `ruleset_versions`
9. `signal_briefs`
10. `campaign_workspaces`
11. `proposed_reason_codes`
