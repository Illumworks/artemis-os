# Marketing Pipeline — Canonical Reference

**Status:** living document. Updated when pipeline structure changes materially.
**Last updated:** 2026-05-22
**Source of truth for runtime:** `artemis/pipelines/seeds/marketing_pipeline.py` (the seed loader). This doc explains the WHY behind the seeded JSON.

---

## Why this document exists

The marketing pipeline is the first seeded Pipeline in Artemis OS. It's also the proof case for D6 (Pipeline as unified orchestration primitive). After running a few cycles in production, we'll want to adjust the structure based on real signal flow, real qualifier behavior, real human bottlenecks at Gate 1.

This doc captures the **intent** behind every node and edge so future edits don't accidentally break the team's design. The Figma boards (Scout team / Qualifier team / Content team) are the original source; this doc is the implementation reading.

---

## High-level flow

```
Trigger (scheduled, every 4h)
   ↓
9 Scouts (parallel fan-out from trigger)
   ↓ (each writes to signal_queue)
Cross-Reference Agent (Phase 1 → Phase 2 → Phase 3)
   ↓
Brief Composer Agent
   ↓
Gate 1: Signals Inbox (human approval — Josh / Angela)
   ↓ (approved briefs → campaign workspace)
Campaign Brief Assembler
   ↓
Asset Selector Agent
   ↓
Writing Studio Adapter
   ↓
4 deliverable types (parallel fan-out): Email / Social / Long-Form / Landing-Page
   ↓ (fan-in)
Gate 2: Approval Drawer (Writing Studio review)
   ↓ (approved drafts → released to send)
[terminal]
```

**21 nodes total**, ~31 edges. Single workflow (not split — Jon's call). One trigger fans out, one Gate 1 in the middle, one Gate 2 at the end.

---

## Team-by-team breakdown

### Scout team — Detect (9 agents + 1 trigger)

**Purpose:** continuously query 9 distinct data sources for signals (legislation, news, board minutes, leadership transitions, etc.). Each scout writes structured signal payloads to `signal_queue` with assigned reason codes from Josh's 17-code registry.

| Node | Role |
|---|---|
| `trigger_scheduled` | Every 4 hours (cron `0 */4 * * *`, America/Chicago). Entry point. |
| `scout_starbridge_researcher` | Legislative + funding via Starbridge API (bench-test for 6-12 months) |
| `scout_regional_news` | Local education news via RSS / search APIs |
| `scout_linkedin_observer` | Mode B only — leader posts about role changes, vendor frustration, literacy priorities |
| `scout_legislative` | State legislation tracking (POLICY_LIT_MANDATE, POLICY_EDTECH_TIME_LIMIT, TX HB 1416 / HB 3) |
| `scout_federal_funding` | Federal grant windows + deadlines (FUNDING_LITERACY_GRANT, FUNDING_DEADLINE_NEAR) |
| `scout_state_doe` | State DoE press + approved vendor lists (VENDOR_APPROVED_LIST) |
| `scout_procurement` | Active RFPs in priority states (PROCUREMENT_LITERACY_RFP, PROCUREMENT_ELA_ADOPTION) |
| `scout_board_minutes` | District board minutes parsing (DISTRICT_*, FUNDING_HB2_ELIA, VENDOR_DISSATISFACTION, TX_HB1416_WAIVER) |
| `scout_leadership_transition` | New superintendent / CAO / curriculum director announcements (LEADER_TRANSITION_FORMAL/INTERIM) |

**Shared dependencies (not modeled as nodes; live as services):**
- Territory Config (priority_states, watch_keywords, deprioritized lists)
- Reason Code Registry (Josh's canonical 17 codes — `signal_reason_codes` table)
- Memory Layer (`(district, reason_code)` last-seen for dedupe)
- Connectors (Starbridge API key, LinkedIn auth, news feed credentials)

**Edges:** trigger → each of 9 scouts (9 fan-out edges)

### Qualifier team — Qualify (2 agents in pipeline + 2 off-pipeline)

**Purpose:** evaluate each signal against rulesets, route to top campaign type(s), produce Josh-readable brief.

| Node | Role |
|---|---|
| `qualifier_cross_reference` | Cross-Reference Agent. **Label includes "(Phase 1→2→3)" for clarity.** Internally three phases: <br>• Phase 1: hard filters (deterministic — priority_state, enrollment ≥ 5000, contact stub) <br>• Phase 2: score against ALL rulesets (LLM rubric per ruleset) <br>• Phase 3: route to top campaign type(s) (deterministic) <br>Plus M4's qualifier_rule_layer applies 12 hard-skip / suppress / boost rules globally. |
| `qualifier_brief_composer` | Brief Composer Agent. Turns qualified signals into concise campaign briefs for Gate 1 inbox. |

**Off-pipeline qualifier agents** (seeded as agents but NOT in the marketing pipeline):
- `qualifier_ruleset_manager` — operator-invoked via chat to refine rulesets
- `qualifier_ruleset_compiler` — converts YAML rulesets to executable form

**Rulesets seeded (referenced by Cross-Reference, not pipeline nodes):**
- OBC (Outcomes-Based Contract)
- Biliteracy / DLL
- Dyslexia / structured literacy

**Edges:** 9 scouts → cross_reference (fan-in, 9 edges) → brief_composer → gate_1

### Gate 1 — Signals Inbox

| Node | Role |
|---|---|
| `gate_1_signals_inbox` | **Human gate.** Josh / Angela approve, reject, snooze, or ask for more context. <br>Config: `approval_kind: signal_brief`, `timeout_hours: 72`, `on_timeout: escalate`, `escalation_to: jon@amiralearning.com` |

Approval delivery: **Slack DM** (per Jon's 2026-05-22 call) via existing J1/J8 integration.

**Edges:** brief_composer → gate_1 → brief_assembler

### Content team — Use Assets (3 agents + 4 deliverable invocations + 1 gate)

**Purpose:** transform approved briefs into ready-to-send content across 4 deliverable types.

| Node | Role |
|---|---|
| `content_brief_assembler` | Campaign Brief Assembler. Deterministically builds the immutable campaign brief from approved Gate 1 brief + signal evidence. |
| `content_asset_selector` | Asset Selector Agent. Picks ONE asset bundle (specific creatives, lead magnets, calls-to-action) appropriate for the campaign type. |
| `content_writing_studio_adapter` | Writing Studio Adapter. Deterministic adapter; carries approved campaign inputs across the Writing Studio boundary. |
| `deliverable_email` | Per-type invocation. Writing Studio shapes the email deliverable. |
| `deliverable_social` | Per-type invocation. Social variant. |
| `deliverable_long_form` | Per-type invocation. Long-form deliverable (blog / whitepaper). |
| `deliverable_landing_page` | Per-type invocation. Landing page variant. |
| `gate_2_approval_drawer` | **Human gate.** Final review of all 4 deliverables. Fan-in: waits for all 4 deliverable nodes before opening for approval. |

Per Jon's call: 4 separate deliverable nodes (visual clarity) + 1 Gate 2 (lean approval surface, all-or-nothing).

**Edges:** gate_1 → brief_assembler → asset_selector → writing_studio_adapter → 4 deliverables (fan-out, 4 edges) → gate_2 (fan-in, 4 edges)

---

## Trigger and gate defaults

### Trigger (`trigger_scheduled`)

```json
{
  "cron": "0 */4 * * *",
  "timezone": "America/Chicago",
  "start_date": null,
  "end_date": null
}
```

Adjust via the canvas (cron preset picker). Common edits:
- Daily at 9am: `0 9 * * *`
- Weekdays at 9am: `0 9 * * 1-5`
- Every hour during work day: `0 9-17 * * 1-5`

### Gate 1 (`gate_1_signals_inbox`)

```json
{
  "approval_kind": "signal_brief",
  "approvers": ["josh@amiralearning.com", "angela@amiralearning.com"],
  "timeout_hours": 72,
  "on_timeout": "escalate",
  "escalation_to": ["jon@amiralearning.com"]
}
```

### Gate 2 (`gate_2_approval_drawer`)

```json
{
  "approval_kind": "content_draft",
  "approvers": ["josh@amiralearning.com", "angela@amiralearning.com"],
  "timeout_hours": 72,
  "on_timeout": "escalate",
  "escalation_to": ["jon@amiralearning.com"],
  "wait_for_all_upstream": true
}
```

`wait_for_all_upstream: true` is the fan-in semantic — PIPE4 executor waits for all 4 deliverable nodes to complete before firing the gate.

---

## Reason code routing

Josh's 17 canonical codes (per `decisions/campaign-signal-spec-v1.md`):

| Code | Default urgency | Primary emitters |
|---|---|---|
| POLICY_LIT_MANDATE | hot at PASSED_CHAMBER/ENACTED, standard at INTRODUCED | starbridge, legislative, state_doe |
| POLICY_EDTECH_TIME_LIMIT | standard, hot if statewide K–3 | legislative, regional_news |
| FUNDING_LITERACY_GRANT | hot if ≤30d, standard 30–90d | starbridge, federal_funding, state_doe |
| FUNDING_DEADLINE_NEAR | hot ≤30d | starbridge, federal_funding, procurement |
| FUNDING_HB2_ELIA | enrichment (context only) | board_minutes |
| VENDOR_APPROVED_LIST | hot | starbridge, state_doe |
| VENDOR_DISSATISFACTION | standard, hot if non-renewal vote | regional_news, board_minutes, linkedin_observer |
| DISTRICT_STRATEGIC_LITERACY | standard | regional_news, board_minutes |
| DISTRICT_PROFICIENCY_GAP | standard, hot if paired | regional_news, board_minutes |
| DISTRICT_DLL_EXPANSION | standard | board_minutes |
| DISTRICT_MTSS_STRAIN | standard | board_minutes |
| PROCUREMENT_ELA_ADOPTION | standard, hot when RFP posts | procurement, board_minutes |
| PROCUREMENT_LITERACY_RFP | hot if ≤14d, standard 15–45d | starbridge, procurement |
| TX_HB1416_WAIVER | hot | legislative, board_minutes |
| TX_HB3_DYSLEXIA_COMPLIANCE | hot | legislative, board_minutes |
| LEADER_TRANSITION_FORMAL | hot for 90d post-hire | regional_news, leadership_transition |
| LEADER_TRANSITION_INTERIM | standard | linkedin_observer, leadership_transition |

**Source of truth at runtime:** `agents.reason_codes_emitted` JSONB column per agent (per `briefs/agent-reason-codes-injection.md`). Runtime injects this list into the LLM system message. Updating Josh's emissions is a SQL/UI edit, not a markdown sweep.

---

## Qualifier rules (Josh's §4 — applied globally via M4)

**Hard skip (signal killed):**
- HMH partner districts (channel conflict)
- Single-school opportunities (below motion fit)
- Districts < 5,000 enrollment

**Suppress (downgrade or hold):**
- Stale signal (same district + code in last 30d, unless material change)
- Speculation not action (board discussion without vote → standard not hot)
- Single-source leader transition (LinkedIn-only → hold 7d, retry, downgrade)
- Paywalled evidence (downgrade one tier, flag `evidence_quote_partial`)
- TX biliteracy v0.1 deprioritized

**Boost (upgrade tier):**
- Stacked signals (2 codes same district, 30d → upgrade one tier)
- Leader transition + curriculum signal → hot
- Texas approval signals (TX_HB1416_WAIVER, TX_HB3_DYSLEXIA_COMPLIANCE) → always hot
- bill_stage = PASSED_CHAMBER / ENACTED on POLICY_LIT_MANDATE → hot

These rules apply at the Cross-Reference Agent node regardless of which scout emitted the signal.

---

## Known limitations / explicit deferrals (v1)

- **NO outreach send orchestration.** Pipeline ends at Gate 2 approval. Actual sending is a separate concern (Writing Studio export → email tool / social tool / etc.).
- **NO Contact enrichment.** Contact stub returns True for priority districts; real enrichment is a future brief.
- **NO Compliance team.** Brand voice lives in Writing Studio rulesets.
- **NO Track / Learn feedback loop.** PIPE4 audit log captures runs; no automated learning yet.
- **LinkedIn Observer Mode A disabled.** Only Mode B (Leader Monitor) is wired.
- **No multi-state biliteracy.** TX biliteracy explicitly suppressed for v0.1.
- **HMH partner detection** depends on `salesforce_account.is_hmh_partner` flag OR district board adoption record. Salesforce integration is future work; flag for now is operator-mutable per D4.
- **Cost cap behavior at runtime** (PIPE4): stops execution when cumulative cost hits cap; marks run `partial_complete`; logs reason. Applies to all provider modes.
- **Approval delivery in PIPE4:** Slack DM via J1/J8 integration. No email v1.

---

## How to safely edit this pipeline

After 1-2 production cycles, expect adjustments. **Safe edit checklist:**

1. **Open the pipeline canvas** (Operations → Pipelines → Marketing Pipeline)
2. **Use the AI Assistant panel** (right side of canvas) to propose changes — describes intent in natural language; AI shows ghost nodes/edges on canvas with Accept/Reject
3. **Before saving structural changes** (adding/removing nodes), screenshot or export the current pipeline JSON for rollback
4. **Run a manual test** via the Run button after every change; check `pipeline_runs` history for any node-level errors
5. **For trigger schedule changes**, use the cron preset picker (Daily / Weekly / etc.); avoid raw cron unless you're explicitly editing in Custom mode
6. **For new reason codes** (Josh updates the registry):
   - Update the `signal_reason_codes` table (registry is the canonical source)
   - Update each affected scout's `reason_codes_emitted` JSONB column (via Agent Card multi-select)
   - No pipeline edit needed — runtime injects current list automatically
7. **For new deliverable types** (e.g., add "podcast script"):
   - Add a new `deliverable_*` agent_invocation node parallel to existing 4
   - Add edge from writing_studio_adapter → new node
   - Add edge from new node → gate_2_approval_drawer (fan-in)

**Risky edits to avoid:**
- Removing Gate 1 or Gate 2 (breaks team approval workflow)
- Changing Cross-Reference node ID (breaks M4's hardcoded callsites if any)
- Disabling all scouts simultaneously (pipeline runs but produces no signals)
- Changing trigger to fire more frequently than 1h (Starbridge bench-test credit budget concerns)

---

## What this pipeline does NOT do

For clarity on scope (so future asks don't bend the pipeline into shapes it's not designed for):

- **Does NOT send emails / social posts / etc.** Pipeline ends at Gate 2 approval. Sending is downstream.
- **Does NOT do contact discovery / lead enrichment.** Stub returns True for priority districts.
- **Does NOT measure campaign effectiveness.** No tracking pixels, no open rates, no attribution. Output handed to Writing Studio for delivery.
- **Does NOT learn from past runs automatically.** Self-improvement on individual agents (via Builder + trajectory summaries) is per-agent; pipeline-level learning is a future feature.
- **Does NOT handle multi-state campaigns differently.** Geography is signal metadata; routing is uniform.

---

## Cross-references

- `docs/marketing-ops-v1/PIPELINE.md` — original conceptual flow (team-grounded)
- `docs/marketing-ops-v1/agents/` — per-agent blueprints (system prompts, tools, urgency tiers)
- `decisions/campaign-signal-spec-v1.md` — Josh's canonical 17 reason codes + qualifier rules
- `decisions/memory-v2-architecture.md` — memory layer architecture
- `briefs/m1-reason-code-registry.md` — reason code registry implementation
- `briefs/m3-campaign-state-machine.md` — state transition rules
- `briefs/m4-qualifier-rule-layer.md` — boost/suppress/skip logic
- `briefs/m5-marketing-agent-seed.md` — 16-agent DB seed
- `briefs/pipe5-marketing-pipeline-seed.md` — Pipeline JSON seed (was 16 nodes pre-reconciliation; 21 post)
- `briefs/marketing-pipeline-figma-reconciliation.md` — the 21-node update
- `artemis/pipelines/seeds/marketing_pipeline.py` — runtime source of truth (JSON)

---

## Living edits

Update this doc when:
- Pipeline structure changes materially (nodes added/removed, edges rerouted)
- Trigger or gate defaults change
- Josh's reason code registry updates
- Qualifier rules added/removed
- Connectors change (new credential requirements per scout)
- Slack DM delivery mechanism changes (PIPE4 implementation choices)

This doc is the **mental model**. The JSON is the source of truth. Both should stay aligned.
