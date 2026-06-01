# CI1 — Campaign initiation substrate + multi-signal grouping + lineage (Stream 2)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. Mostly substrate (tables + repo helpers + Pydantic), but the cluster-or-create logic + wiring it into the existing candidate-creation site needs codebase judgment — use the flagship. (Drop to `gpt-5.4-mini`/medium only if you keep the wiring trivial.)
**Target branch:** `worker/ci1-initiation-substrate`
**Fires:** now — Stream 1 (DIST1–DIST6) is merged; migration head is **0056** (verify with `alembic current`; your migration is the next integer).
**Authoritative design:** `docs/campaign-initiation-and-district-design.md` §§ "Locked decisions", "Stream 2: multi-signal grouping + campaign lineage". This brief is self-contained but that doc is the source of truth for the G-1..G-4 knobs.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~450 (5 tables/columns + join + repo logic + Pydantic + tests).
**Priority:** HIGH — opens Stream 2 (campaign identity + grouping).

---

## Why this exists

Two gaps this closes:
1. **Campaign identity** — `campaign_candidates` has no name/objective/targeting/deliverable-mix; deliverable types are hardcoded in `marketing_pipeline.py`.
2. **Multi-signal grouping** — a campaign should aggregate MULTIPLE corroborating signals about the same district+family (a transition + a program launch + a board vote = ONE campaign), but the model only records a single `source_signal_id`. `brief_assembler.py` already takes `signals: list[...]`; the persistence just never recorded the set.

CI1 lays the substrate + the deterministic grouping logic. The LLM proposal (CI2) and UI (CI3) build on top.

---

## Scope

### Part A — Migration (run `uv run alembic current` first; down_revision = head (~0056); revision = next int; PASTE it)

**`campaign_candidates` additions** (all nullable — populated at initiation, not creation):
```
name                   text         -- LLM-proposed, operator-confirmed
objective              text         -- one-line LLM proposal
target_scope_json      jsonb        -- tagged union (Part C)
deliverable_types_json jsonb        -- array of deliverable-type slugs selected for this campaign
initiated_at           timestamptz
initiated_by           bigint       -- user id
predecessor_id         bigint       -- FK -> campaign_candidates(id) ON DELETE SET NULL; lineage (prior campaign, same district+family)
-- owner_user_id already exists
```
Index `predecessor_id`.

**`deliverable_types` registry table** (seed the 4 rows in the migration):
```
id  bigserial pk · slug text unique not null · label text not null
default_enabled boolean not null default false · active boolean not null default false
display_order integer not null · created_at/updated_at timestamptz
```
| slug | label | default_enabled | active | order |
|---|---|---|---|---|
| outreach_email | Outreach Email | true | true | 1 |
| social | Social Post | false | false | 2 |
| long_form | Long-Form | false | false | 3 |
| landing_page | Landing Page | false | false | 4 |
(Only `outreach_email` active — "outreach emails to start, expand later." Adding a type later = insert a row + flip `active`; no pipeline-code edit.)

**`campaign_candidate_signals` join** (many-to-many signal↔candidate):
```
id bigserial pk
candidate_id bigint not null  FK -> campaign_candidates(id) ON DELETE CASCADE
signal_id    bigint not null  FK -> signal_queue(id) ON DELETE CASCADE
is_primary   boolean not null default false   -- the lead signal
attached_at  timestamptz not null default now()
UNIQUE(candidate_id, signal_id)
```
Index `(candidate_id)` + `(signal_id)`.

**Clustering config** — the 90-day window must be EDITABLE (not hardcoded), mirroring how
`district_tier_bands` is stored + edited in the Signal Playbook. Add a small singleton config
(e.g. a `marketing_clustering_config` row, or extend an existing playbook config table) with
`cluster_window_days integer not null default 90`. Seed it at 90.

### Part B — Models + repository

- Models: `DeliverableType`, `CampaignCandidateSignal`, `CampaignCandidate` field additions, clustering-config model — in `artemis/marketing/models.py`.
- Repository helpers (`artemis/marketing/repository.py`):
  - `list_deliverable_types(session, active_only=True)` → ordered.
  - `get_cluster_window_days(session) -> int` (reads config; default 90).
  - **`cluster_or_create_candidate(session, signal) -> CampaignCandidate`** — THE grouping engine:
    - If `signal.resolved_district_id` and `signal.campaign_family`: look for an **OPEN** candidate
      (`initiated_at IS NULL` AND `decision_state != 'rejected'`) for the same
      `resolved_district_id + campaign_family` created within `cluster_window_days`. If found →
      attach the signal (`is_primary=False`) and return it.
    - Else (no open match, OR no resolved district/family) → **create** a new candidate
      (`source_signal_id = signal.id`, `campaign_family` from the signal), set `predecessor_id`
      to the most recent NON-open prior candidate for the same district+family (lineage; NULL if
      none), attach the signal (`is_primary=True`), return it.
    - Idempotent: attaching an already-attached signal is a no-op (UNIQUE guard).
  - `initiate_campaign(session, candidate_id, *, name, objective, owner_user_id, target_scope, deliverable_type_slugs, initiated_by)` — validates slugs against the registry (active only), validates `target_scope`, sets columns + `initiated_at`. **Idempotent:** if already initiated → raise a clear ValueError (don't silently re-initiate).
  - `get_candidate(session, id)`, `get_candidate_signals(session, candidate_id)`.

### Part C — `TargetScope` Pydantic (used here + reused by CI2)

```
{"mode":"all_districts"}
{"mode":"states","states":["FL","TX"]}            # states validated against US states
{"mode":"district_tier","tiers":["D1","D2","D3"]} # tiers validated D1..D4
{"mode":"named_districts","district_ids":[...]}    # accept shape, mark experimental
```
Self-teaching errors (H1) on invalid mode / unknown state / invalid tier. Put it in a new
`artemis/marketing/initiation_schemas.py`.

### Part D — Wire cluster-or-create into the candidate-creation site

Find where `campaign_candidates` rows are created today (search `CampaignCandidate(` /
`create_candidate` / candidate creation in the qualifier or gate path) and route NEW-signal
candidate creation through `cluster_or_create_candidate`. **If the current creation site is
ambiguous or risky to change, STOP and report it** rather than guess — note where it is and
propose the hook; we'll wire it in CI2 if cleaner. (Substrate + the function are the must-have;
the wiring is best-effort with a clear report.)

### Part E — Tests (`artemis/marketing/tests/test_ci1_initiation_substrate.py`)

1. Migration seeds 4 deliverable types; only `outreach_email` active+default.
2. `list_deliverable_types(active_only=True)` → 1; `False` → 4.
3. `cluster_or_create_candidate`: first signal for (district D, family obc) → CREATES a candidate, signal attached `is_primary=True`.
4. Second signal same (D, obc) within window + candidate still OPEN → ATTACHES to the same candidate (no new candidate; `is_primary=False`).
5. Second signal after the first candidate is INITIATED → CREATES a fresh candidate with `predecessor_id` = the initiated one (lineage).
6. Signal with NULL resolved_district → always CREATES standalone (no clustering).
7. `initiate_campaign` sets all fields + `initiated_at`; inactive slug ('social') → self-teaching error; second initiate → raises (idempotency).
8. `TargetScope`: valid each mode; invalid state ('XX') + invalid tier ('D9') → self-teaching.

---

## Files owned

- NEW: `alembic/versions/00XX_*.py`
- EDIT: `artemis/marketing/models.py` (+DeliverableType, +CampaignCandidateSignal, +CampaignCandidate fields, +clustering config)
- EDIT: `artemis/marketing/repository.py` (cluster_or_create_candidate, initiate_campaign, helpers)
- NEW: `artemis/marketing/initiation_schemas.py` (TargetScope)
- POSSIBLE EDIT: the candidate-creation site (Part D — report if risky)
- NEW: `artemis/marketing/tests/test_ci1_initiation_substrate.py`

---

## Acceptance criteria

1. `alembic current` (down_revision proof) + `upgrade head`. **Paste.**
2. Seeded types + clustering config: `psql -c "SELECT slug,active FROM deliverable_types ORDER BY display_order;"` + `psql -c "SELECT cluster_window_days FROM marketing_clustering_config;"` (or wherever stored). **Paste.**
3. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_ci1_initiation_substrate.py -v` — 8 pass (esp. cluster #3-6 + lineage #5). **Paste.**
4. **Lossless:** additive columns/tables only; nothing dropped. **Confirm.**
5. Part D: state where the candidate-creation hook landed (or why deferred). **Report.**
6. `./scripts/check.sh` (j5b Jira flake known-exempt) + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **Deterministic grouping only** — `cluster_or_create_candidate` is pure rules (district+family+window+open-state). NO LLM here (the LLM proposes the campaign in CI2, never groups).
- **Fresh candidate per campaign** — clustering attaches ONLY to open (not-initiated, not-rejected) candidates; initiated/rejected → new candidate + `predecessor_id` lineage.
- **Clustering window is config, not a literal** — read `get_cluster_window_days`.
- **Deliverable types are data**; **self-teaching errors**; **idempotent initiation**; **lossless** (additive only); **local-only git**.

---

## Report-back format

```
CI1 — initiation substrate + grouping report
1. Commit / branch · alembic current + migration number
2. LOC per file
3. Seeded deliverable_types + clustering config query output
4. Test pass count (esp. cluster-or-create #3-6 + lineage #5 + idempotency #7)
5. Part D — where the cluster-or-create hook landed (or why deferred)
6. Lossless confirmation · check.sh summary
7. Surprises — esp. the current candidate-creation site + campaign_family source on signals
```
