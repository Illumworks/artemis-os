# Campaign Initiation + District Entity — Design

**Status:** Design locked except 2 Josh-owned values (D-5, D-6). Briefs follow once those land.
**Created:** 2026-05-31
**Origin:** Jon's question during the post-CC12 marketing-flow review — "when we approve a signal, how does the campaign actually get initiated, named, and given its collateral?" That surfaced (a) campaigns have no identity model and (b) a real oversight: **district size is never known, yet it's a hard business filter** (Amira does not currently serve D4 districts).
**Read-first context:** `docs/marketing-flow-audit-2026-05-30.md` (the audit that led here), `decisions/campaign-signal-spec-v1.md` (Josh's spec — priority states, skip list, campaign types), `docs/PLATFORM-MAP.md` Locked Decisions Ledger.

---

## The problem this solves

Today, when an operator approves a signal at Gate 1:
1. A `campaign_candidates` row exists with **no name, no objective, no owner, no targeting** — just a family slug + source signal id. The "Michigan Field Guide" rich shape in `marketing-os.js` is **mock data** that the real model never grew into.
2. Content agents fire **all four deliverables** (email/social/long-form/landing-page) regardless of whether the signal warrants them.
3. **District size is unknown at every step.** A D4-district signal (smallest, currently unsupported) can walk all the way to Gate 1, and Josh approves/rejects it with no size context on the card. District size is a hard filter that the workflow never modeled — an oversight in the original design.

Grounding facts (verified 2026-05-31):
- Spec specifies **priority states** (FL, IN, MD, MO, IL, TX) and a **hard skip list** (§4.1), both parsed by `josh_spec`.
- Spec mentions a "watchlist of 200–500 districts (enrollment ≥ 5,000)" but it is a **criterion, never a materialized list**. No districts table exists.
- **`signal_queue.district_id` and `.state` are 100% NULL** across all 202 signals — scouts never populate them.
- **No enrollment data, no size tiers (D1–D4) anywhere** in spec, schema, or signals. D1–D4 is Jon's sales mental model, not yet encoded.
- `TerritoryConfig` table + `PUT /territory/{family}` already exist and are edited by the Signal Playbook (hot_states, standard_states, unlisted_multiplier) — proves the editable-config + review pattern.
- CC27 already added `district` / `account` / `person` memory scopes for the future CRM-integration horizon.

---

## Two-layer architecture

```
Layer 1 — District entity + classification  (foundational; qualification + approval consume it)
   signal references a district name
        ↓  District Classifier (name → NCES match)
   districts row upserted: nces_id, name, state, enrollment, tier, supported, on_skip_list
        ↓  pure-function tier classifier (enrollment → D1–D4, bands from DB config)
   qualifier reads tier → soft-flags unsupported (D4); Gate 1 card shows district context
        ↓
Layer 2 — Campaign Initiation  (separate step after Gate 1; consumes Layer 1)
   brief_assembler proposes: name · objective · deliverable mix · target scope (tier-aware)
        ↓
   operator confirms/edits in Initiation form  ← human confirm BEFORE content fires
        ↓
   content work fires ONLY for selected deliverable types (today: outreach_email only)
        ↓
   Gate 2 — draft review (exists today)
```

---

## Locked decisions

| # | Decision | Value |
|---|---|---|
| **CI-1** | Campaign Initiation is a **separate pipeline step** after Gate 1 (not folded into the approve action) | LOCKED |
| **CI-2** | The **extended `marketing.content.brief_assembler`** emits the initiation proposal (reuses the brief it already assembles). Revisit only if D-6 argues for consolidating district + initiation reasoning into one agent. | LOCKED (lean) |
| **CI-3** | Deliverable types are a **registry** (`deliverable_types` table/seed): `slug, label, default_enabled, display_order, active`. Seed `outreach_email` (active, default_enabled), others (`social`, `long_form`, `landing_page`) as `active=false` coming-soon. Adding a type = insert a row, never edit pipeline code. | LOCKED |
| **CI-4** | Targeting = `target_scope_json` tagged union. Wire **`all_districts`, `states`, `district_tier`**. Defer `named_districts` until per-district outreach tracking is needed. | LOCKED |
| **CI-5** | **Pydantic `CampaignInitiationProposal`** (H1–H5 discipline): `name` bounded length; `recommended_deliverable_types` each validated against the registry (self-teaching error lists valid slugs); `target_scope` mode + states validated. LLM **proposes**, operator confirm is the commit, nothing auto-fires. | LOCKED |
| **D-1** | **District is a first-class entity** — `districts` table — classified **before** qualification completes so the tier is available to both the filter and the Gate 1 card. Lossless: districts persist and are reused across signals; never deleted. | LOCKED |
| **D-2** | Size classification = **NCES Common Core of Data enrollment lookup + pure-function tier bands**. No LLM guessing of enrollment (that is exactly the hallucination to clamp). The only fuzzy step is name-resolution (signal "LAUSD" → "Los Angeles Unified"), which is bounded and verifiable. | LOCKED |
| **D-3** | Tier bands are a **single global DB-backed config** (enrollment cutoffs are family-independent), edited in a **"District Sizing" section of the Signal Playbook UI** alongside territory config. Pure classifier reads bands from DB at runtime → retuning needs zero code. Band change → one-button recompute over stored enrollment. | LOCKED |
| **D-4** | D4 = **soft flag, lossless filter**. D4-district signals reach Gate 1, clearly marked "unsupported tier — filtered," Josh can still eyeball them, nothing deleted. When business reopens to D4, flip `supported` — the data is intact. | LOCKED |
| **D-5** | **Enrollment band thresholds**: **D1 ≥ 25,000 · D2 10,000–24,999 · D3 5,000–9,999 · D4 < 5,000.** Adopted as the starting set *because they are editable in-UI* (D-3) — Josh retunes anytime without code. | LOCKED (editable) |
| **D-6** | District Classifier = **dedicated agent**, name-resolution only (signal name → NCES match). No enrollment reasoning in the LLM; tier is the pure function. | LOCKED |

---

## `districts` table (proposed shape)

```
districts
  id              bigserial pk
  nces_id         text unique        -- authoritative NCES district id (null until resolved)
  name            text not null      -- canonical NCES name
  state           text               -- 2-letter
  enrollment      integer            -- from NCES; null if unresolved
  tier            text               -- D1..D4, computed by pure classifier; null if no enrollment
  supported       boolean not null default true   -- false for currently-unsupported tiers (D4)
  on_skip_list    boolean not null default false  -- matches spec §4.1 hard skip list
  classification_source text         -- 'nces' | 'manual' | 'unresolved'
  classified_at   timestamptz
  created_at / updated_at
```

Signal → district link: `signal_queue.district_id` becomes an FK-ish reference resolvable to `districts` (absorbs the geo-fix). Many signals reference one district over time → district is the durable entity.

`target_scope_json` union:
```
{ "mode": "all_districts" }
{ "mode": "states",         "states": ["FL","TX"] }
{ "mode": "district_tier",  "tiers": ["D1","D2","D3"] }   -- now populatable because of D-1/D-2
{ "mode": "named_districts","refs": [...] }                -- deferred (CI-4)
```

`campaign_candidates` additions for initiation: `name`, `objective`, `target_scope_json`, `deliverable_types_json`, `initiated_at`, `initiated_by` (`owner_user_id` already exists).

---

## Proposed brief sequence (drafted after D-5 + D-6 resolve)

**Stream 1 — District entity + classification (foundational):**
1. `districts` table + NCES reference dataset load (public CSV; not a dependency) + pure tier-classifier function (bands read from config)
2. Global tier-bands config + Signal Playbook "District Sizing" editor (D-3) + recompute action
3. District Classifier agent (name → NCES resolution → upsert) + signal→district link (absorbs scout geo-fix)
4. Qualifier consumes tier → soft-flag D4 (D-4); Gate 1 card shows district context

**Stream 2 — Campaign Initiation (consumes Stream 1):**
5. `campaign_candidates` initiation columns + `deliverable_types` registry (CI-3)
6. Initiation pipeline step + extended brief_assembler + `CampaignInitiationProposal` Pydantic (CI-1, CI-2, CI-5)
7. Campaign Initiation UI form (name/objective/owner/mix/tier-aware targeting)

**Stream 3 — cleanup lands naturally last:**
8. CMP1 (remove `CAMPAIGNS` mock) — now replaces mock with real campaign shape, not an empty void
9. MD1 (dashboard mock-count cleanup)

---

## Relationship to existing locked decisions

- **Master-plan D4 — `district_marketing_flags` table** (outstanding, task #77): that locked decision anticipated per-district marketing state. This `districts` entity design likely **subsumes or supersedes** it — `districts.supported` + `on_skip_list` cover the "flag" intent, and the entity is richer. Reconcile when drafting Stream 1 brief 1: either fold `district_marketing_flags` into `districts`, or keep it as a thin per-district-per-campaign join. **Do not draft `district_marketing_flags` separately without checking this.**
- **Master-plan D3 — `campaign_ruleset_versions`** (outstanding, task #76): unrelated; stays its own work.
- **CC27 district/account/person scopes:** the future CRM-integration horizon (Salesforce → district accounts, contacts) plugs into this `districts` entity. This design is the on-ramp.

## District data: source, freshness, and refresh cadence (loaded 2026-05-31)

**Source:** NCES Common Core of Data (CCD), the federal authoritative census of every US public school district, pulled via the **Urban Institute Education Data Portal API** (a clean REST mirror of CCD — keeps our refresh script small + stdlib-only, no new deps). Endpoint: `educationdata.urban.org/.../ccd/directory/{year}/` (directory includes total enrollment).

**Refresh script:** `scripts/refresh_nces_districts.py` → writes `artemis/marketing/data/nces_districts.csv` (columns `nces_id,name,state,enrollment`) → loaded via `artemis.marketing.nces_loader.load_districts_from_csv` (DIST1).

**Currently loaded:** 2024-25 school year (Urban `year=2024`), 13,462 districts (regular local + component, `agency_type` 1,2). Tier distribution at the locked bands:
- D1 (≥25k): 284 · D2 (10–25k): 620 · D3 (5–10k): 988 · **D4 (<5k): 11,511 (unsupported)**
- **~86% of US districts are D4** — outside Amira's serviceable market today. Only ~1,892 districts are in supported D1–D3 tiers. This is the load-bearing reason district classification exists: most signals referencing small districts should soft-flag as unsupported.

**WHEN TO UPDATE (the cadence):**
- CCD is an **annual** collection. Urban `year` = fall of the school year (`year=2024` = 2024-25).
- NCES releases the **preliminary directory in spring** after the school year; enrollment fills in over the following months. By late spring / early summer the prior school year is ~99% populated.
- **Refresh once a year, late spring / early summer:** bump `--year`, re-run the script, re-load, then call `repository.recompute_all_tiers()` (or the DIST2 "recompute" button) so stored districts pick up enrollment changes + any band edits.
- **Lossless:** the loader UPSERTS by `nces_id`; never deletes. A district that closes simply stops updating; its row persists. (Reopening to D4 is a `supported` flip, never a re-import.)
- Optional automation: an annual scheduled reminder (not yet set — pending Jon's ok, since it's standing config).

**Known loader follow-up (banked):** when `nces_id` is present, the loader must upsert STRICTLY by `nces_id` — it currently also matches on name+state, which collapses distinct districts that legitimately share a name (e.g. multiple "Buckeye Local" in OH). 2024-25 load: 13,462 CSV rows → 13,403 rows (~59 distinct districts lost, mostly small D4). Fix + reload to recover.

## Stream 1 status: BUILT (2026-05-31), not yet ACTIVATED

DIST1–DIST5 all merged (alembic 0056); 13,403 NCES districts loaded; tier classification, soft-flag D4, Gate 1 card serializer, and the Signal Playbook freshness panel all live.

**Activation dependency (the gap between built and working):** all existing signals carry NO geography (`district_id` / `state` / `resolved_district_id` all NULL across 202 rows). DIST3's resolver runs on intake but needs an input — a district name/state on the signal — and **scouts don't emit one yet.** Until scouts populate district geography, the district layer stays dark: Gate 1 cards show "District: unresolved" and D4 soft-flagging never fires. This is tracked as the scout-geo task (#99) and is THE thing that turns the whole district layer on. The DIST5 freshness panel IS browser-visible now (it reads the districts table directly, not signals).

## Stream 2: multi-signal grouping + campaign lineage (locked 2026-06-01)

A campaign is NOT one signal → one brief. Real campaigns aggregate **multiple
corroborating signals** about the same opportunity (multi-source confirmation — a
superintendent transition + a DLL program launch + a board vote for the same district
should be ONE campaign, not three). `brief_assembler.py` already accepts
`signals: list[SignalContext]` and collects reason codes from all of them — but the
persistence model only records a single `campaign_candidates.source_signal_id`, so the
relationship is used at assembly time and then forgotten. Stream 2 makes it first-class.

### How signals group into a campaign (the locked design)

**Deterministic spine, LLM proposes, operator confirms — the LLM is NOT the grouper.**

- **Grouping engine = deterministic** by `resolved_district + campaign_family` within a
  recency window. Explainable ("grouped because: same district + family"), hallucination-free,
  and built on the district layer. The LLM never silently auto-attaches signals.
- **The LLM writes the campaign FROM an already-formed cluster** — proposes name, objective,
  which signals are strongest evidence, deliverable mix (`brief_assembler` reads the whole
  cluster). It may also surface **reviewable refinement suggestions** ("signal X is in this
  cluster but is really a funding-deadline angle — consider splitting"), but those are
  proposals the operator sees, never silent moves.
- **Operator confirms** at the initiation step (can add/remove signals).

### Cluster-or-create (runs at qualification time)

1. Signal qualifies + resolves to a district.
2. Is there an **OPEN** candidate (not yet initiated, not rejected) for this
   `district + campaign_family` within the window? → **attach** the signal to it.
   Otherwise → **create** a new candidate and attach.
3. The candidate **accumulates** corroborating signals over time (multi-source confirmation).
4. At initiation the operator sees the candidate + its N signals; LLM proposes; operator
   confirms → active campaign.

### The four grouping knobs (LOCKED, Jon 2026-06-01)

| # | Knob | Decision |
|---|---|---|
| G-1 | Grouping key | **resolved_district + campaign_family** (a district can run OBC *and* biliteracy simultaneously, so family matters) |
| G-2 | Recency window | **90 days**, but **config-editable** (NOT hardcoded) — lives in the Signal Playbook UI alongside the tier bands + territory config so Josh/Jon retune without code |
| G-3 | Unresolved / state-level signals | **standalone candidates** (no district to cluster on); operator can manually bundle |
| G-4 | Re-open vs new | **fresh candidate** — clustering attaches only to OPEN candidates; once a candidate is initiated/rejected it's "claimed," so the next signal starts a new instance. Campaigns stay **discrete, measurable units** (clear start/end/outcome) rather than immortal accumulators. |

### Campaign lineage (continuity / institutional memory)

A fresh candidate carries a **lineage link** (predecessor reference + the
district+family lineage key). At initiation it does real work:
1. **Surfaces history** — "Fort Bend ISD / biliteracy: 2 prior campaigns," openable.
2. **Exposes prior collateral** — the actual drafts, assets, and brief from last time,
   ready to **view / clone / adapt** into the new campaign.
3. **Grounds the LLM proposal** — brief_assembler is given the predecessor's brief +
   collateral as context, so it proposes "build on what we sent last spring," not from scratch.

So initiation is never a blank page when the district has history. New campaign, but it
stands on its predecessors. **Collateral reuse exists in v1** (`campaign_deliverables`,
Writing Studio drafts, `content_assets` are all present). **Outcome/results do NOT** — see below.

### Schema additions (Stream 2 substrate, CI1)

- **`campaign_candidate_signals`** join table — many-to-many signal↔candidate, with a
  `is_primary` flag (the lead signal) + `attached_at`. Makes "which signals constitute this
  campaign" first-class + queryable. (Keeps the legacy `source_signal_id` = the primary for
  back-compat, or migrate it into the join.)
- **Lineage:** `campaign_candidates.predecessor_id` (nullable FK to a prior candidate) OR rely
  on the queryable `district + family` lineage key. Lean: a `predecessor_id` for the direct
  "previous campaign" + the district+family key for the full chain.
- **Clustering config:** the 90-day window as an editable config value (Signal Playbook),
  mirroring `district_tier_bands`.

### How it shapes the CI briefs

- **CI1** — `campaign_candidate_signals` join + lineage link + clustering config + the
  cluster-or-create repository logic (called at qualification).
- **CI2** — brief_assembler reads the full cluster + is grounded on the predecessor's brief +
  collateral; LLM emits reviewable refinement suggestions.
- **CI3** — initiation UI surfaces the cluster's signals + prior campaigns + their collateral
  with view/clone/adapt affordances.

### 🔮 FUTURE: campaign outcome / effectiveness tracking (NOT built — documented seams)

Today the system **sends** collateral but never records **what happened** (opens, responses,
conversions, disposition). "View past results" in the full sense is a future capability —
the **loop-closer**. Documenting the insertion points now so we don't hunt later:

- **WHERE the data lives (to add):** a new `campaign_outcomes` table keyed by
  `campaign_candidate_id` (+ optionally `deliverable_id`) — fields like `outcome_kind`
  (sent / opened / responded / meeting_booked / closed_won/lost), `value`, `occurred_at`,
  `source` (manual | salesforce | churnzero | email_tracking). Lossless/append-only.
- **WHERE it gets captured (the seam):** the deliverable lifecycle in
  `artemis/marketing/writing_studio/` — the `DeliverableState` machine's terminal "sent/
  delivered" transition is the first write; subsequent outcomes come from an **integration**
  (Salesforce / ChurnZero / Gong — already on the strategic roadmap as the CRM bridge) OR a
  manual operator entry. The CRM-integration scopes (`account`, `person`, `district` — added
  in CC27) are the on-ramp.
- **WHERE it surfaces (the seam):** the CI3 lineage panel ("prior campaigns + **results**")
  reads `campaign_outcomes`; a future campaign-effectiveness view; the freshness/reporting
  surface. The Stream-2 lineage link is the join point — design it now to carry an outcome
  reference later.
- **Sequence:** build + test Stream 2 (initiation + grouping + lineage with collateral reuse)
  FIRST; add outcome tracking AFTER the first iteration is exercised, once we know what
  outcomes are worth capturing. Tracked as a banked future stream.

## Status: fully locked (2026-05-31; Stream-2 grouping + lineage added 2026-06-01)

All decisions resolved. D-5 adopted (editable bands D1≥25k / D2 10–25k / D3 5–10k / D4 <5k). D-6 locked (dedicated name-resolution agent). Briefs cleared to draft. The NCES-lookup + pure-function-tier approach keeps the whole district-sizing path hallucination-free by construction, which is the stated bar.
