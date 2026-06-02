# Marketing slab grounding — planning ↔ signal spec ↔ Python state

**Date:** 2026-05-20
**Branch:** `lead/j6a-granola-integration`
**Mode:** read-only reconciliation. No code modified.
**Inputs:** claudeck `MARKETING_WORKFLOW_BUILD_SPEC.md` (v1.4, 1,313 lines), `CURRENT-MARKETING-BUILD-CONTEXT.md` (slices 1–18 done in Node), `PLAN-marketing-campaign-{os,ops}.md`, `PLAN-marketing-variant-v1.md`, `PLAN-marketing-okr-dashboard.md`, `decisions/campaign-signal-spec-v1.md` (Josh's seed), `audits/marketing-gap-report-v2.md` (Codex 2026-05-20), and the live Python tree at `artemis/marketing/`.

---

## 1. Executive summary

The Marketing slab is the **first seed dataset** for Artemis's generic agent/workflow platform — not a hardcoded feature. Layer 1 (signal queue, candidates, deliverables, briefs, content assets, rulesets, territory, scout runs, approvals, writing studio bridge) is **structurally present in Python** as 11 tables + 8 route modules ported from Node slices 1–18, but Layer 1 has three soft gaps: (a) no first-class `signal_reason_codes` registry table, (b) Cross-Reference Agent + Brief Composer Agent + Ruleset Compiler are absent, (c) `/advance` is a stub, not a state machine. Layer 2 (the 9 scout/qualifier agent definitions that turn this substrate into a marketing OS) is **not seeded at all** — `rulesets`, `territory_config`, `content_assets`, and `scout_runs` are empty tables, and no agent fixtures reference the 17 reason codes in Josh's spec. The recommended close path is 7 briefs: Layer 1 hardening (reason-code registry, state machine, contract aliases for FE) + Layer 2 seed (territory + reason codes + 9 scout agent definitions via DB fixtures, not Builder, for v1). After this, a user with the Agent-Builder shipped today can build the *next* OS by re-running the seed step against their own domain.

---

## 2. Layer 1 infrastructure — target vs current

### 2.1 Reason-code registry
- **Target (BUILD_SPEC §3.1, §6.3 I-10, signal-spec §2):** Append-only table of reason codes (17 entries from Josh: POLICY_LIT_MANDATE … LEADER_TRANSITION_INTERIM). Every signal's `reason_codes[].code` must FK into this. Admin/Ruleset surface for retiring codes.
- **Current Python:** **No dedicated table.** `signal_queue.reason_codes` is a free-form JSONB column (`models.py:67`). The Node port shipped a `signal_reason_codes` table in slice 13 (Signal Criteria / Scout Ruleset Lite — `CURRENT-MARKETING-BUILD-CONTEXT.md:478`) but that table was **dropped during Python migration**. `rulesets.weighted_signals` is JSONB and can reference reason codes by string, but nothing validates them.
- **Gap:** add `signal_reason_codes` table + `GET/POST/PATCH /api/signal-criteria/reason-codes` + FK validation on intake. **Scope: S** (~120 LOC: model + migration + 4 routes + seed loader + tests). Invariant I-10 cannot be enforced until this exists.

### 2.2 Territory config
- **Target (signal-spec §1, BUILD_SPEC §8.3):** Seed priority_states (FL, IN, MD, MO, IL, TX), watchlist_districts derived rule, deprioritized lists per family. Per-family hot/standard/unlisted state lists feed the Qualifier's territory multiplier (Phase 3 in `qualifier.py`).
- **Current Python:** `territory_config` table exists (`hot_states`, `standard_states`, `unlisted_multiplier` JSONB by `family`) — `models.py:335`. Route: `GET/PUT /api/signal-criteria/territory/{family}`. **Empty (0 rows.)**
- **Gap:** seed loader only. **Scope: S** (~40 LOC of YAML/JSON + idempotent seed function). No code changes needed beyond a fixtures runner.

### 2.3 Signal queue + scout intake contract
- **Target (BUILD_SPEC §3.1, §8.1):** Scout→signal contract with provenance fields, dedupe (`embedding_hash`, `near_duplicates_checked`), `urgency.{deadline, days_until, tier}`, multi reason_codes per signal, `qualification_json` written by Cross-Reference Agent.
- **Current Python:** `signal_queue` table (20 cols, includes `provenance`, `qualification_json`, `urgency_tier`, `reason_codes`). Routes: `POST /intake` (with dryRun, dedupe via source_url+headline/snippet), `POST` manual create alias, `GET` list/detail, lifecycle (`/qualify`, `/approve`, `/reject`, `/snooze`, `/archive`, `/ask`). `scout_intake.py` has shared normalization. Strong.
- **Gap:** (a) `geography.district_id` canonical FK absent (district roster table doesn't exist — Josh's "200–500 watchlist_districts" derivation is unbacked); (b) no embedding-based dedupe (URL/headline+snippet only); (c) intake does not validate `reason_codes[].code` against a registry (see 2.1). **Scope: M** for (a)+(b) combined (~250 LOC). (c) folds into 2.1.

### 2.4 Qualifier (rules engine + decision log)
- **Target (BUILD_SPEC §2.2, §4.1):** Cross-Reference Agent runs hard filters → score against ALL active rulesets → route to top campaign types. Append-only `qualifier_decisions` per signal with `weighted_signals_fired[]`, `qualitative_rubrics_fired[]`, `ruleset_version`. Boost/suppress/skip rules from signal-spec §4.
- **Current Python:** `qualifier.py` is a **pure deterministic scorer** (`MatchedRule`, `FamilyScore`, hard filter + weighted match + territory multiplier). It's a faithful port of Node `signal-qualifier.js` (slice 18). `POST /signal-queue/{id}/qualify` writes result to `signal_queue.qualification_json`. **No qualifier_decisions table — qualification is overwritten in-place on each call (not append-only).** Boost/suppress logic from signal-spec §4 is not implemented (stacked-signals boost, single-source leader-transition suppress, etc.).
- **Gap:** (a) `qualifier_decisions` append-only audit table; (b) boost/suppress rule layer on top of the deterministic scorer (could ship as config-driven rule list, not LLM); (c) eventually qualitative rubrics (LLM call per rubric) — defer. **Scope: M** for (a)+(b) (~200 LOC). (c) is L (~400) and not MVP-critical.

### 2.5 Campaign candidates lifecycle
- **Target (BUILD_SPEC §5.2):** 15-state campaign machine `created → contacts_pending → contacts_attached → compliance_early_check → brief_assembled → bundle_selected → content_generating → content_review → all_content_approved → compliance_last_mile → approved_for_delivery → delivered → reporting_active → archived`, with `blocked` side-state. Append-only decision events.
- **Current Python:** `campaign_candidates.workspace_state` column exists; `POST /candidates/{id}/advance` only flips `decision_state` (approve/reject/monitor/request_changes) — it does **not** invoke a state machine. `_recomputeCampaignState` derivation from deliverables (Node slice 11) is **not ported**. No decision-event log table. Brief assembler is ported (`brief_assembler.py`, 315 lines) and writes append-only `campaign_briefs` rows.
- **Gap:** (a) port `computeWorkspaceStateFromDeliverables` derivation; (b) full state machine on `/advance` with transition validation; (c) `campaign_decision_events` append-only log. **Scope: L** (~400 LOC). This is the audit's #4 priority.

### 2.6 Scout runs / scout execution infrastructure
- **Target (BUILD_SPEC §2.1, §8.1):** Scout packages (declarative configs with `allowedSourceTypes`, instructions), `POST /api/scouts/runs` harness with dryRun/commit, scout_runs audit table, per-finding error accumulation.
- **Current Python:** `scout_runs` table exists; `GET/POST /api/scouts/runs`, `GET /api/scouts/packages` ported. **`config/scout-packages.json` equivalent is missing** — the route returns whatever is in DB/config, but no fixtures define the 3 scout packages from Node slice 17.
- **Gap:** seed `scout_packages` config + fixtures for the 9 scout names listed in §3. **Scope: S** (~80 LOC of config + loader).

---

## 3. Layer 2 seed content — agent definitions to ship

Catalog of agents that must exist as definitions (DB fixtures or Builder-produced) for the marketing seed. References below point to BUILD_SPEC §2 and PLAN-marketing-campaign-os.md §"Agent Teams".

| # | Agent name | Purpose | Input source | Output (reason codes / signal fields) | Reference |
|---|---|---|---|---|---|
| 1.1 | **Starbridge Researcher** | Legislation + funding signals from Starbridge | Starbridge API (poll + event) | POLICY_LIT_MANDATE, POLICY_EDTECH_TIME_LIMIT, FUNDING_LITERACY_GRANT, FUNDING_DEADLINE_NEAR, FUNDING_HB2_ELIA, TX_HB1416_WAIVER, TX_HB3_DYSLEXIA_COMPLIANCE | BUILD_SPEC §2.1, signal-spec §2 |
| 1.2 | **Regional News Scout** | Board minutes, news, state DoE press, supe transitions | news API + board-minutes scrapers + state DoE pages | DISTRICT_PROFICIENCY_GAP, DISTRICT_STRATEGIC_LITERACY, DISTRICT_MTSS_STRAIN, DISTRICT_DLL_EXPANSION, VENDOR_DISSATISFACTION, PROCUREMENT_ELA_ADOPTION, PROCUREMENT_LITERACY_RFP | BUILD_SPEC §2.1, signal-spec §5 "all states" |
| 1.3a | **LinkedIn Observer — Mode A** | Weekly follower digest → Contact team (deferred) | LinkedIn followers_diff API | (deferred — contact-team output) | BUILD_SPEC §2.1 (MVP-5+) |
| 1.3b | **LinkedIn Observer — Mode B** | Event-driven leader posts → Signals Inbox | LinkedIn posts_by_user | LEADER_TRANSITION_FORMAL, LEADER_TRANSITION_INTERIM, VENDOR_DISSATISFACTION | BUILD_SPEC §2.1 |
| — | **Board Minutes Scout** (variant of 1.2) | District-level board minute parsing keyed off watchlist_districts | board_minutes.fetch | DISTRICT_*, PROCUREMENT_*, FUNDING_HB2_ELIA | signal-spec §1 ("Board Minutes Scout keys off this list") |
| — | **Leadership Transition Scout** (variant of 1.2/1.3b) | Two-source-confirmed hire detection | district press + board votes + LinkedIn | LEADER_TRANSITION_FORMAL, LEADER_TRANSITION_INTERIM (two-source rule §4.2) | signal-spec §1, §4.2 |
| 2.1 | **Cross-Reference Agent (Qualifier)** | Phase 1 hard filters → Phase 2 score against all rulesets → Phase 3 route. Boost/suppress/skip from signal-spec §4. | signal_queue (event-driven) | writes `qualification_json`; emits `qualified` state | BUILD_SPEC §2.2, §4.1, signal-spec §4 |
| 2.2 | **Ruleset Manager Agent** | Chat panel for Josh — propose/simulate/commit rule changes | Ruleset chat UI | mutates `rulesets.weighted_signals`, `rulesets.hard_filters`; bumps version | BUILD_SPEC §2.2, §4.9 |
| 2.4 | **Brief Composer Agent** | Convert enriched signal → Inbox card for Gate 1 | qualified_signal_queue | Inbox Card (BUILD_SPEC §3.3) | BUILD_SPEC §2.2 |
| 5.1 | **Campaign Brief Assembler** (component, not LLM) | Already ported as `brief_assembler.py` | candidate + signal + history | Campaign Brief §3.5 | BUILD_SPEC §2.5 — **shipped** |
| 5.2 | **Asset Selector Agent** | Pick 1 primary + 1-2 supporting assets from Content Registry | content_assets, brief | Asset Bundle §3.6 | BUILD_SPEC §2.5 |
| 5.3 | **Writing Studio Adapter** (component) | Invoke WS, handle events | Internal interface §7.1 | Deliverable §3.7 | BUILD_SPEC §2.5 — **shipped (C4)** |
| WS.1 | **Writing Agent** | The drafting model | brief + bundle + rules + memory | Draft content | BUILD_SPEC §2.8 |

**Effective new agent definitions to ship for MVP-3+MVP-4:** 1.1, 1.2, 1.3b, 2.1, 2.2, 2.4, 5.2 + (Board Minutes + Leadership Transition as variants of 1.2/1.3b or as their own packages) — **9 agent definitions**. (2.3 Ruleset Compiler is a deterministic component, not an agent.)

System prompts are not specified here — they'll be drafted by Lead or via the Agent-Builder when each agent's brief is written.

---

## 4. Josh-spec → seed-data mapping

| signal-spec §  | Content | Target table / config | Status |
|---|---|---|---|
| §1 Territory config — priority_states FL/IN/MD/MO/IL/TX | `territory_config` table per family | `artemis.marketing.models.TerritoryConfig` | **MISSING DATA** (0 rows) |
| §1 watchlist_districts (200–500 derived) | Needs `district_roster` table (not in Python) | — | **MISSING — needs Layer 1 work** |
| §2 Reason code registry (17 codes) | `signal_reason_codes` table | — | **MISSING — needs Layer 1 work** (see 2.1) |
| §3 Campaign type mapping (5 families, watch keywords) | `rulesets.weighted_signals` per family + `rulesets.metadata.watch_keywords` | `artemis.marketing.models.Ruleset` exists | **MISSING DATA** (0 rows; Node had `campaign_ruleset_versions` versioning — Python only has flat `rulesets`) |
| §4.1 Hard skip list (HMH partner, single-school, <5K enrollment) | Per-ruleset `hard_filters` JSON | `rulesets.hard_filters` JSONB column exists | **MISSING DATA** + **HMH partner flag requires SF account integration (deferred MVP)** |
| §4.2 Suppress (stale, speculation, single-source leader, paywalled) | Qualifier rule layer | — | **MISSING CODE** — `qualifier.py` does not implement these (see 2.4) |
| §4.3 Boost (stacked signals, leader+curriculum, TX approval) | Qualifier rule layer | — | **MISSING CODE** — same as above |
| §5 Per-state nuance (FL OBC, TX HB anchors, IN/MD cross-state, vendor language) | Per-scout prompt deltas in agent `system_prompt` | — | **MISSING — agent definitions don't exist yet** |
| §5 Named competitors (iReady, Lexia, UCSF Multitudes, Amplify) | Watch keywords for Regional News + LinkedIn Observer | `rulesets.metadata.watch_keywords` or per-scout config | **MISSING DATA** |

**Note on append-only ruleset versioning.** Node slice 13 shipped `campaign_ruleset_versions` (draft → active → superseded) per BUILD_SPEC §5.5. Python's `rulesets` table is flat (`version_tag`, `state` columns). Invariant I-21 (ruleset versions append-only) is structurally not enforceable in current Python. Folds into the Layer 1 hardening brief.

---

## 5. Reconciliation with Agent-Builder (O1)

**Question:** Can the v1 Agent-Builder produce the 9 scout/qualifier agents conversationally, or do they ship as DB fixtures?

**Recommendation: DB fixtures for v1 seed, Builder-editable later.**

Reasoning:
1. **The Builder is conversational.** The scout agents reference external tools (`starbridge.search`, `board_minutes.fetch`, `linkedin.posts_by_user`, `news_api.search`) that aren't yet first-class capabilities exposed to the Builder. A user describing "build me a scout that watches Florida legislation" today wouldn't get a wired-up Starbridge tool binding — the Builder doesn't know that capability exists. Tool registration → Builder capability surfacing is a separate workstream.
2. **The agents have invariant-bound output contracts.** Each scout must emit signals that conform to BUILD_SPEC §3.1 *and* validate against the reason-code registry (I-10) *and* carry verbatim quotes (I-9). A Builder-conversation might produce a "good enough" prompt but skip the structured-output discipline. For a seed shipping to demonstrate the product, hand-crafted definitions with tested prompts are safer.
3. **Per-state nuance from signal-spec §5 is non-obvious.** The "FL is leading on OBC framing, watch IN/MD for the cross-state pattern" instruction is encoded as a per-scout prompt delta. A user/Builder roundtrip won't surface these unless the user already knows the spec — at which point they could write the agent directly.
4. **Builder-editable preserves the future.** Ship as DB fixtures with `system_prompt` files in `seed/marketing/` referenced by agent rows. The O2/O3 "Edit with Builder" flow then lets Angela/Jon refine prompts conversationally without re-running the seed. This is the path that supports a different user later shipping a sales OS with the same Layer 1 infrastructure.

**Builder capabilities that would need to land for full conversational seeding (not required for MVP, but flag for roadmap):**
- Tool registration surface so the Builder can wire new agents to `starbridge.search`, `news_api.search`, etc.
- Structured-output schema reference so the Builder asks "what's the output contract?" and validates
- Reason-code registry awareness so the Builder can suggest "you probably want POLICY_LIT_MANDATE here"

None of those block the MVP seed.

---

## 6. Recommended brief sequence

Seven briefs to walkable state. Each is sized so Worker (Sonnet) or Codex can land it in one focused session.

| # | Name | Scope | Owner | Depends on | Why it matters |
|---|---|---|---|---|---|
| **M1** | Reason-code registry table + routes + FK validation | S (~150 LOC) | Worker | — | Unblocks signal-spec §2 seeding and invariant I-10 enforcement; prerequisite for M2 and M5 |
| **M2** | Layer 1 seed loader: territory_config + reason_codes + 5 rulesets (one per family) + scout_packages | M (~250 LOC config + loader) | Codex | M1 | Turns empty tables into a demoable substrate; folds in signal-spec §1, §2, §3 |
| **M3** | Campaign state machine + decision-event log + `/advance` rewrite + workspace-state derivation port | L (~400 LOC) | Lead-led | — | Audit's #4 priority; unblocks `blocked` state, gate enforcement, audit invariants |
| **M4** | Qualifier rule layer: boost/suppress/skip from signal-spec §4 + append-only `qualifier_decisions` table | M (~250 LOC) | Worker | M1, M2 | Closes Layer 1 qualifier gap; lets Phase 3 of `qualifier.py` route correctly |
| **M5** | Marketing seed fixtures part 2: 9 scout/qualifier agent definitions + per-scout prompt files under `seed/marketing/` | M (~300 LOC config + prompts + loader) | Lead writes prompts, Worker wires loader | M1, M2, M4 | The actual Layer 2 seed — turns substrate into Marketing OS |
| **M6** | Frontend contract aliases: signal create alias, signal archive alias, campaign-deliverables query alias, content-asset-links query alias, deliverable delete-by-(campaign,asset) alias | S (~100 LOC + tests) | Codex | — | Closes the contract drift gaps from audits/marketing-gap-report-v2 §3 (independent of others; can land first) |
| **M7** | Writing Studio overview aggregator (`GET /api/writing-studio/overview`) + draft list/detail/update routes | L (~500 LOC) | Worker | — | Highest-leverage audit unblock; lets the Writing Studio UI load (currently first-load fails); independent of M1–M5 — can run in parallel |

**Parallelization:** M6 and M7 can run immediately, in parallel with M1–M5 (no shared files). M1 → M2 → M4 → M5 is the Layer 1+2 chain. M3 is independent of the chain and can run in parallel with M1+M2.

**End state after all 7:** marketing slab walkable end-to-end with Josh's spec encoded, Writing Studio loading, and demo data labeled. Asset Selector Agent and Compliance gates remain deferred per the May 14 workshop decision.

---

## 7. Open questions for Lead

1. **District roster.** Josh's "watchlist_districts (200–500 derived from priority_states + enrollment ≥ 5,000 + skip list)" requires a district table that doesn't exist in Python. Where does the canonical district list come from — is there a CSV from Anne Marie, a NCES export, or do we ship without it and let scouts emit `district_id = null` for v1 and reconcile later? This blocks signal-spec §1 watchlist enforcement and invariant I-19 (Manual Stub vs Discovery Agent identical schema).
2. **Ruleset versioning depth.** Node had `campaign_ruleset_versions` separate from `rulesets`. Python collapsed to a flat `rulesets` table with `version_tag`. Do we re-introduce versioning now (invariant I-21 is structural otherwise) or accept that v1 marketing seed ships with mutable rulesets and version-history is M3-era work?
3. **HMH partner flag source.** Signal-spec §4.1 hard skip requires detecting "district is HMH Into Reading adopter" — that's a Salesforce account flag *or* board adoption record. Both are deferred per the May 14 workshop. Do we (a) ship the hard-filter as a manual operator override in v1, (b) seed a hardcoded HMH-district list, or (c) defer the skip rule until SF integration?
4. **Scout agent runtime.** The current scout package definitions in Node are declarative configs (`allowedSourceTypes`, instructions) — they don't actually execute LLM calls yet. Python `routes/scouts.py` has the harness shape but no live execution. Is M5's deliverable "definitions only, no execution" (operators trigger via manual harness) or does it include an execution path (workflow/chain runner)?
5. **Writing Studio scout integration.** Slices 5–10 (Writing Studio invoke/Gate2/regen/events/adapter) are ported; PLAN-marketing-campaign-os.md mentions "Writing Studio scouts" in passing. Are there additional Writing-Studio-side agents that need to be in the Layer 2 catalog (e.g. brand voice guardian, format selector), or is `WS.1 Writing Agent` the only Writing-Studio agent the seed needs?

---

## Surprises / things worth flagging

1. **Reason-code registry table is gone.** Node slice 13 shipped `signal_reason_codes` as a first-class table; Python ports kept the JSONB column but dropped the registry. Without it, invariant I-10 is unenforceable and signal-spec §2's 17-code list has nowhere to live. This is the single highest-priority Layer 1 fix.
2. **Qualifier is a deterministic port — Josh's boost/suppress rules are nowhere.** `qualifier.py` is exactly Node's scoring algorithm. Signal-spec §4 (the actual qualification *intelligence*) has zero implementation. The audit doesn't flag this because the audit was checking route shape, not Qualifier semantics.
3. **`/advance` is decoration, not a state machine.** It updates `decision_state` and returns. The 15-state campaign machine in BUILD_SPEC §5.2 is documented but unimplemented. Workspace state derivation from deliverables (Node slice 11) didn't make the Python cut.
4. **Empty seed tables look healthy in the audit.** Codex's audit reports rulesets/territory/content_assets/scout_runs as "implemented" because the routes work and the tables exist — but they have 0 rows. The product appears inert in a way route inspection doesn't surface.

---

**Doc length:** ~470 lines (under 600 cap).
