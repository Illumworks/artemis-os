# CI1 — Campaign initiation substrate + deliverable-type registry (Stream 2)

> **⚠ UPDATED 2026-06-01 — multi-signal grouping + lineage (authoritative spec in
> `docs/campaign-initiation-and-district-design.md` § "Stream 2: multi-signal grouping +
> campaign lineage").** CI1 substrate now ALSO includes:
> 1. **`campaign_candidate_signals`** many-to-many join (signal↔candidate, `is_primary` flag,
>    `attached_at`) — a campaign records ALL its constituent signals, not just one.
> 2. **Cluster-or-create** logic: a newly-qualified signal attaches to an OPEN candidate
>    (not initiated, not rejected) for the same `resolved_district + campaign_family` within a
>    **90-day** window; else creates a new candidate. Runs at qualification time.
> 3. **`campaign_candidates.predecessor_id`** lineage link (fresh candidate per campaign, but
>    knows its predecessor for the same district+family).
> 4. **Clustering window = editable config** (Signal Playbook, mirroring `district_tier_bands`)
>    — NOT hardcoded.
> Locked knobs G-1..G-4 + lineage are in the design doc. Build alongside the columns/registry below.

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `low`. Pure substrate: columns + a small registry table + seed + repository helpers + tests. No design judgment.
**Target branch:** `worker/ci1-initiation-substrate`
**Fires:** AFTER Stream 1 lands (DIST1–DIST4 merged). Touches `marketing/models.py` + `campaign_candidates` — coordinate so it doesn't overlap any in-flight DIST migration.
**Browser smoke owner:** Lead post-merge (verify seed + columns).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300.
**Priority:** HIGH — opens Stream 2 (campaign identity).

---

## Why this exists

Per `docs/campaign-initiation-and-district-design.md` CI-3 + the campaign-identity gap: `campaign_candidates` has no name/objective/targeting/mix. And deliverable types are hardcoded in `marketing_pipeline.py`. CI1 lays the substrate: initiation columns + a **deliverable-type registry** so types are data (start with `outreach_email`, expand by inserting rows — Jon's requirement).

---

## Scope

### Part A — Migration (run `alembic current` first; down_revision = head; revision = next int; paste it)

**`campaign_candidates` additions** (all nullable — populated at initiation, not creation):
```
name                  text          -- LLM-proposed, operator-confirmed
objective             text          -- one-line LLM proposal
target_scope_json     jsonb         -- tagged union (see below)
deliverable_types_json jsonb        -- array of deliverable-type slugs selected for this campaign
initiated_at          timestamptz
initiated_by          bigint        -- user id
-- owner_user_id already exists
```

**`deliverable_types` registry table:**
```
id            bigserial pk
slug          text unique not null   -- 'outreach_email','social','long_form','landing_page'
label         text not null          -- 'Outreach Email'
default_enabled boolean not null default false
active        boolean not null default false   -- false = "coming soon", not selectable yet
display_order integer not null
created_at / updated_at
```
**Seed in the migration:**
| slug | label | default_enabled | active | order |
|---|---|---|---|---|
| outreach_email | Outreach Email | true | true | 1 |
| social | Social Post | false | false | 2 |
| long_form | Long-Form | false | false | 3 |
| landing_page | Landing Page | false | false | 4 |

(Only `outreach_email` is active — matches "we only do outreach emails to start, expand later.")

### Part B — Models + repository

- `DeliverableType` model + `CampaignCandidate` field additions in `marketing/models.py`.
- Repository helpers:
  - `list_deliverable_types(session, active_only=True)` → ordered.
  - `initiate_campaign(session, candidate_id, *, name, objective, owner_user_id, target_scope, deliverable_type_slugs, initiated_by)` → validates slugs against the registry (active only), validates `target_scope` shape, sets columns + `initiated_at`. Returns the updated candidate. **Idempotency:** if already initiated, raise a clear ValueError (don't silently re-initiate).
  - `get_candidate(session, id)`.

### Part C — target_scope_json validation helper

A Pydantic discriminated union `TargetScope` (used here + reused by CI2):
```
{"mode":"all_districts"}
{"mode":"states","states":["FL","TX"]}          # states validated against US states
{"mode":"district_tier","tiers":["D1","D2","D3"]} # tiers validated D1..D4
{"mode":"named_districts","district_ids":[...]}   # deferred — accept shape, mark experimental
```
Self-teaching errors (H1) on invalid mode / unknown state / invalid tier.

### Part D — Tests

`artemis/marketing/tests/test_ci1_initiation_substrate.py`:
1. Migration seeds 4 deliverable types; only `outreach_email` active+default.
2. `list_deliverable_types(active_only=True)` → 1 row; `active_only=False` → 4.
3. `initiate_campaign` sets all fields + `initiated_at`; re-fetch reflects.
4. `initiate_campaign` with an **inactive** slug ('social') → self-teaching error listing active slugs.
5. `initiate_campaign` twice → second raises (idempotency).
6. `TargetScope` validation: valid each mode; invalid state ('XX') → self-teaching; invalid tier ('D9') → self-teaching.

---

## Files owned

- NEW: `alembic/versions/00XX_*.py` (paste `alembic current` for down_revision)
- EDIT: `artemis/marketing/models.py` (+DeliverableType, +CampaignCandidate fields)
- EDIT: `artemis/marketing/repository.py` (+3 helpers)
- NEW: `artemis/marketing/initiation_schemas.py` (TargetScope + helpers) — or add to existing schemas.py
- NEW: `artemis/marketing/tests/test_ci1_initiation_substrate.py`

---

## Acceptance criteria

1. `alembic current` (down_revision proof) + `upgrade head`. **Paste.**
2. Seeded types: `psql -c "SELECT slug,active,default_enabled FROM deliverable_types ORDER BY display_order;"`. **Paste.**
3. `pytest .../test_ci1_initiation_substrate.py -v` — 6 pass. **Paste.**
4. **Lossless:** additive columns only; no existing column dropped. **Confirm.**
5. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **Deliverable types are data.** Adding a type later = insert a row + flip `active`. No pipeline-code edit. (CI2/CI3 read the registry.)
- **Self-teaching errors** on inactive-slug + invalid target_scope.
- **Idempotent initiation** — second initiate raises, never silently overwrites.
- **Coordinate migration number** (paste `alembic current`).
- **Local-only git.**

---

## Report-back format

```
CI1 — initiation substrate report
1. Commit / branch
2. alembic current + migration number
3. LOC per file
4. Seeded deliverable_types query output
5. Test pass count (esp. inactive-slug #4 + idempotency #5)
6. Lossless confirmation
7. check.sh summary
8. Surprises
```
