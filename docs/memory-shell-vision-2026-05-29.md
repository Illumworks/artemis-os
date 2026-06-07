# Memory Shell — Full Design Vision (from Node reference)

**Date:** 2026-05-29
**Author:** Lead (captured from Jon's screenshot of the Node version's intended Memory UI)
**Status:** LOCKED DESIGN (2026-05-29 EOD). All 13 architectural questions resolved. MC1-MC5 + MW1-MW4 streams ready to draft.

**Corrected mental model (Jon's clarification):** Memory is the **observation layer**, not the **decision layer**. Three sources of memory writes:
1. **Floating Artemis** writes directly (trusted authoring surface — no approval gate in front of FA)
2. **System events** auto-write (M1 trajectory summaries, M5 signal genealogy)
3. **Approval carryovers** auto-write when domain-specific approvals fire (Gate 1, definition_proposals, Skill promotion, etc.)

The Node screenshot's "Needs Review" wing as an approval workflow inside Memory is **explicitly NOT the model.** The approval happened elsewhere; Memory reflects it.

**Two streams (ordered):**
- **Stream A — Memory Carryover (MC1-MC5)** — backend write paths from domain approval surfaces. Fires first. ~380 LOC.
- **Stream B — Memory Wings UI (MW1-MW4)** — display improvements. Fires after MC accumulates real data. ~820 LOC.

---

## What this doc is

Jon showed Lead a screenshot of the Node version's Memory shell — the "wings/rooms frontend" the keystone plan referenced as Slice 4 (banked as "unverified" in the Node app). It is materially richer than what M6 just shipped. This doc captures the design before we lose the context and maps it to current Python rebuild state.

---

## The Node version's Memory shell (what Jon showed)

**3-column layout:**

### Left: Governance / Memory sections

Six "wings" (the keystone plan's word for memory categories that operators reason in terms of):

| Wing | Purpose | Count badge |
|---|---|---|
| **Needs Review** | Approval inbox — new, risky, or under-specified memory that needs human approval before becoming durable | (high count, the operator's daily landing) |
| **Artemis Knows** | Durable facts + conventions already in use by the assistant | (slow-growing) |
| **Projects** | Project-scoped memory for decisions, facts, constraints | (project-bounded) |
| **Working** | Short-lived context still in motion for the active project/session | (short-lived) |
| **Agents** | What each agent is allowed to remember + reuse across runs | (per-agent permissioning) |
| **Skills/Rules** | Promoted patterns + guardrails that survive as reusable behavior | (rarely-changing) |

Below the wings: **Browse by Scope** — All / Project / etc. Plus stats (review queue size, avg relevance).

### Middle: Approval Inbox (the active wing's contents)

Title: "Needs Review" (or whichever wing is selected)
Subtitle: short explanation of what this wing means

**Archive + Restore card** at the top: Export JSON, SQLite backup, Validate import, Apply import.

**Search bar** below.

**Scope filter chips:** All · Artemis · Agents · Projects · Skills

**Memory rows** below: each shows
- Author tag (ARTEMIS — i.e. who proposed this memory)
- Risk badge (HIGH RISK / REVIEW SCOPE)
- Date
- Title
- 1-2 line preview
- Category · Scope · Source breadcrumbs

### Right: Detail pane

Selected memory's full content + structured metadata:

- **APPROVAL INBOX** breadcrumb
- Memory title
- Full content (with markdown rendering — `[public/js/core/api.js]` links etc.)
- **Category** (Warning, Discovery, Fact, Convention, etc.)
- **Scope** (Project · claudeck-artemis)
- **Source** (claudeck-artemis — i.e. where it came from)
- **Durability** (Temporary, Durable, Permanent, etc.)
- **Age** (timestamp)
- **Relevance** (numeric score, 2.0 in the screenshot)
- **Why It Surfaced** (LLM-generated explanation of why this row needs attention)
- **Shared Primitive** (notes about whether this row underpins shared infrastructure)
- **Evidence** (list with weight × source — DRAWER references etc.)

**Action buttons:**
- **Approve to Artemis Knows** — promote from Needs Review to durable assistant-known
- **Keep in Working** — leave in short-term context
- **Promote to Skills/Rules** — elevate to a reusable guardrail
- **Edit** — modify content
- **Entities** — manage extracted entities
- **Delete** — (rare; lossless invariant means this is supersession not deletion)

**Stats banner at top:** "60 TOTAL · 49 NEED REVIEW · 11 APPROVED · 0 ACCESSED TODAY"

---

## Mapping to current Python rebuild state

| Vision feature | Current state | Gap |
|---|---|---|
| Wings sidebar with 6 categories | M6 has Observations/Drawers tabs + scope dropdown | Whole taxonomy + sidebar needs building |
| Needs Review default landing | M6 shows All observations | Need an approval/durability state on observations |
| Approval/promotion lifecycle | Observations written direct-to-durable | **Major architectural gap** — no promotion state machine |
| Risk badges (HIGH RISK / REVIEW SCOPE) | None | Need to either compute or store risk classification |
| Category labels | Stored in `memory_observations.category` (substrate exists per P3) | UI doesn't surface |
| Durability attribute | Not in schema today | Schema addition needed |
| Source attribute | Stored as `memory_drawers.source` for drawers; observations have no equivalent | Need source field on observations |
| Age + Relevance display | Created_at + score in schema | UI doesn't surface |
| Why It Surfaced | Not in schema | Need an `attention_reason` field or computed-at-read explanation |
| Shared Primitive note | Not in schema | Free-text metadata field |
| Evidence list with weights | Schema has weight column ✅, UI shows DRAWER chips | Closer to complete |
| Promotion buttons (Approve to / Keep / Promote) | None | Each needs a route + state transition |
| Edit | None | Need observation edit route (still lossless — edit = supersession) |
| Entities button | Substrate exists (`memory_entities` from P3) | UI never wired |
| Archive + Restore | Substrate exists (`backup.py`) | UI never wired |
| Browse by Scope | Dropdown today | UI works; could be richer (chip-row) |
| Stats banner | M6's "1 DRAWERS · 3 OBSERVATIONS · 3 EVIDENCE LINKS · 3 SCOPES" | Different metrics — Node uses workflow stats, M6 uses storage stats. Both useful; could merge. |

---

## The corrected architectural model (Jon's clarification)

**Memory is the observation layer. Approvals happen elsewhere and carry over.**

Three sources of memory writes:

1. **Floating Artemis writes (trusted, direct-to-durable).** FA has authority. Its emissions are facts about conversations + explicit `_write_memory` tool calls. No human approval gate in front of FA — its authority comes from being the operator's assistant.

2. **System events (automatic).** M1 trajectory summaries, M5 signal genealogy. These fire on real runtime events (an agent_run completed, a signal qualified). Direct-to-durable. They describe what happened, not what was proposed.

3. **Approval carryovers (NEW — Memory Carryover stream).** Every domain-specific approval surface in the platform writes a memory observation when an approval fires. The approval happened upstream; memory reflects the result.

**Approval surfaces today that should carry over (empirically inventoried):**

| Surface | Endpoint / location | Memory write trigger |
|---|---|---|
| Definition proposals (Builder) | `POST /api/builder/proposals/{id}/approve` (exercised today via Proposal #4) | When `engine.commit()` fires |
| Signal Gate 1 (Marketing brief approval) | `POST /api/marketing/signal_queue/{id}/approve` | When operator approves a brief |
| Generic marketing approvals (Gate 2+) | `POST /api/marketing/approvals/{id}` | When operator approves a campaign artifact |
| Skill promotion | `POST /api/builder/skills/{slug}/approve` | When operator approves a skill |
| FA marketing approvals | `floating_artemis/tools/marketing.py:94` (FA approving signals on user's behalf) | When FA approves via tool call (still considered FA-authored) |
| Pipeline human gates | `artemis/pipelines/node_executors/human_gate_executor.py` | When a human-gate decision lands |
| Dev project permissions | `POST /api/dev_projects/sessions/{id}/permissions/{pid}/approve` | (lower priority — sandbox scoped) |

**What this means for the in-flight briefs:**

- **M3+M4 (in flight) — NO CHANGE NEEDED.** FA writes direct-to-durable per the original brief. No retrofit. Original design was correct under the corrected model.
- **M2 (in flight) — NO CHANGE NEEDED.** Builder reads all memory observations (durable by definition). No filtering by "approval status" needed.
- **M1 + M5 — NO CHANGE NEEDED.** System-event writes are facts, not proposals. Direct-to-durable is correct.

**What this means for Memory Wings (the originally-planned UI stream):**

- DROP the "Needs Review" wing as an approval gate.
- DROP MW2 (promotion state machine + routes).
- KEEP the wings as taxonomic categories: Working / Durable / Skills+Rules / Projects / Agents.
- Wings tell you what KIND of memory this is, not what STATE it's in.
- Display-level "attention items" could still exist (high-risk observations the operator should look at) but they're flags on a row, not a separate workflow stage.

---

## Proposed brief sequences (TWO streams)

### Stream A — Memory Carryover (MC1-MC5) — backend write paths

Write to memory when domain approvals fire. Surgical backend work. Can fire in parallel after Round 2 lands.

**MC1 — Definition proposals approve → memory observation (~80 LOC)**

In `artemis/builder/repository.py:approve_proposal` (or wherever the engine.commit path lands), after approval succeeds, write a memory observation:
- Scope: `agent:<agent_id>` (or `skill:<skill_id>` for skill kinds)
- Content: `"{operator_or_system} approved definition proposal #{id} for {agent_id}: {summary of change}. Citations: runs {run_ids}."`
- Evidence: link to `definition_proposals:{id}` + the cited `agent_run` rows
- Source kind: `definition_proposal` (extending CC23's Literal)

**MC2 — Signal Gate 1 approval → memory observation (~80 LOC)**

In `artemis/marketing/routes/signal_queue.py:approve` (and the generic `approvals.py` route), after approval, write observation:
- Scope: `workspace:marketing`
- Content: `"Operator approved brief for signal #{id} at Gate 1 on {date}. Headline: {headline}. Reason codes: {codes}."`
- Evidence: link to existing M5 drawer + signal_queue row + approval row

**MC3 — Skill promotion → memory observation (~80 LOC)**

In `artemis/routes/builders/skills.py:approve`, after the skill status flips to "approved", write observation:
- Scope: `workspace:platform` (or `skill:<slug>` per agent grounding)
- Content: `"Skill '{slug}' promoted to approved status by {actor}. Purpose: {description}. Used by: {agent_count} agents."`
- Evidence: link to skill row

**MC4 — Pipeline human-gate decisions → memory observation (~80 LOC)**

In `artemis/pipelines/node_executors/human_gate_executor.py`, after a human-gate node resolves (status=succeeded), write observation:
- Scope: `pipeline:<pipeline_id>`
- Content: `"Pipeline {id} gate at node {node_id} decided: {decision} by {actor}. Context: {decision_payload summary}."`
- Evidence: link to pipeline_run + node decision rows

**MC5 — Floating Artemis tool-driven approvals → memory observation (~60 LOC)**

In `artemis/floating_artemis/tools/marketing.py:94` (and similar FA approval helpers), when FA approves a signal on the user's behalf, also write observation:
- Scope: `agent:floating-artemis` (FA is the author)
- Content: `"FA approved signal #{id} on behalf of {user} during chat session {session_id}. Reason: {user_directive}."`
- Evidence: link to signal_queue row + floating_artemis_messages row

**Total: ~380 LOC across 5 surgical briefs. Can fire in parallel after Round 2.**

### Stream B — Memory Wings UI (MW1-MW4, revised — no promotion machine)

Display improvements + taxonomic categorization. UI work.

**MW1 — Multi-scope schema + minimal metadata additions (~100 LOC + migration)**

New table:
```sql
CREATE TABLE memory_observation_scopes (
  observation_id BIGINT REFERENCES memory_observations(id),
  scope_kind TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  weight FLOAT NOT NULL DEFAULT 1.0,
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (observation_id, scope_kind, scope_id),
  FOREIGN KEY (scope_kind, scope_id) REFERENCES memory_scopes(scope_kind, scope_id)
);
```

Add to `memory_observations`:
- `wing TEXT NOT NULL DEFAULT 'durable'` (one of `working | durable`)
- `confidence_origin TEXT` (the source path: 'm1', 'm5', 'mc_definition_proposal', 'mc_signal_gate1', 'mc_skill_promotion', 'mc_pipeline_gate', 'mc_fa_marketing', 'fa_write_memory', 'fa_conversation')

NOT ADDED (explicitly killed):
- ❌ `attention_band` (use existing score/hit_count)
- ❌ `attention_reason` (use evidence chain + confidence_origin)
- ❌ `risk_band` (not needed without approval gate)
- ❌ `durability` (single wing field is enough — `working` vs `durable`)
- ❌ `shared_primitive_note` (note via evidence chain instead)

Migration 0048. Backfill: each existing observation gets one row in the join table mirroring its current `scope_id`, `is_primary=true`. The `wing` column defaults to `durable` for backfilled rows.

**No promotion state machine.** Wings are categories, not workflow states. The only auto-state-change is FA conversation drawers promoting from `working` to `durable` when `hit_count >= 3` (D1 auto-promotion rule) — handled by a small async task or read-time check.

**MW2 — Wings UI sidebar replacing M6's tabs (~250 LOC)**

Replace M6's Observations/Drawers tabs with a 5-wing sidebar. Counts per wing. Click selects the wing; main pane filters. Browse by Scope below the wings.

Move M6's existing tab structure to a sub-filter within each wing if needed.

**MW3 — Attention-band badges + Detail-pane richer metadata (~200 LOC)**

- Row-level attention badges (routine / notable / HIGH) with color coding
- Detail pane renders: category, scope, source, durability, age, relevance, attention_reason, shared_primitive_note, evidence (with weights), entities (clickable to entity neighborhood)
- "Attention items" filter as a secondary chip (cross-wing) — surface things flagged HIGH across all wings

**MW4 — Archive + Restore UI + Entities management (~250 LOC)**

Frontend for existing `backup.py` substrate. Export JSON, SQLite backup, Validate import, Apply import.

Entities button: opens modal showing extracted entities (from P3's `memory_entities` table) with neighborhood query.

**Total: ~820 LOC across 4 briefs. MW1 must land first; MW2-MW4 mostly independent.**

### Combined: ~1200 LOC across 9 briefs (was 6 briefs in the old plan). Architecturally cleaner since we dropped the promotion state machine entirely.

---

## How M3+M4 changes if we adopt this design

**Under the corrected model: M3+M4 does NOT need to change.**

FA writes direct-to-durable, which is exactly the original M3+M4 design. No retrofit. The Node "Needs Review" wing as an approval gate was a misread; the corrected model treats FA as a trusted writer.

When MW1 lands later, existing FA drawers get a backfill: `wing="working"` for conversational drawers (short-lived context) or `wing="durable"` if the operator explicitly tagged them via the `_write_memory` tool. Trivial backfill, not a design change.

---

## What this means strategically

**The Node design is the destination.** M6 is the floor (substrate + minimal inspector). Memory Wings (MW1-MW6) is the operator-grade surface.

**Sequencing:**
1. Finish Round 2 (M2 + M3+M4) — memory becomes a real platform substrate
2. Then write MW1-MW6 briefs and fire in waves — operator-grade UX
3. Memory Wings completes the keystone plan Slice 4 — the goal stated in the original keystone plan

**Banked as future work.** This doc preserves the vision so when Round 2 + production smoke land + we're ready to invest in operator UX, the brief sequence is pre-thought.

---

## Decisions locked (2026-05-29 EOD)

All 13 questions resolved. Reasoning tied to end goals: personal-instance distribution, Salesforce/ChurnZero/Gong integration, general-purpose agent OS, lossless invariant, subscription-only.

### Tactical defaults

**D1 — Wing assignments per source:**

| Source | Default wing | Auto-promotion rule |
|---|---|---|
| M1 trajectory observations | `durable` | — |
| M5 qualified-signal observations | `durable` | — |
| MC approval carryovers | `durable` | — |
| FA `_write_memory` tool calls | `durable` | — |
| FA conversation drawers | `working` | **Promote to `durable` when `hit_count >= 3`** (referenced 3+ times by other reads) |

**D2 — Attention bands KILLED.** No `attention_band` column. Use existing `score`, `confidence`, `hit_count`, `source_quality`. UI sort options: Recent / Most Referenced / Highest Confidence / Score.

**D3 — Carryover writes are MULTI-SCOPE.** Each approval writes the observation to its primary scope AND `workspace:platform` (the cross-cutting audit trail). Example: Builder approves `kind="agent"` proposal → writes to `agent:<agent_id>` AND `workspace:platform`. Two scope rows per observation via the multi-scope join table (see D6).

**D4 — Sort options replace attention filter.** Filter chips: Recent / Most Referenced / Highest Confidence / Score. No "HIGH ATTENTION" badge.

**D5 — Entities in MW3 detail pane as chips.** Defer browse-by-entity modal until `memory_entities` has real population (P3 graph extraction verified producing data).

### Architectural decisions

**D6 — Observations are multi-scope (many-to-many via join table).** Single most important architectural decision. End-goal driver: Salesforce/ChurnZero/Gong integration means an observation legitimately belongs to multiple scopes (`district:LAUSD` + `campaign:reading_growth` + `workspace:marketing` + `account:salesforce-XYZ` simultaneously). New table `memory_observation_scopes (observation_id, scope_kind, scope_id, weight, is_primary)`. Backfill trivial — 1 row per existing observation.

**D7 — Builder reads increment `hit_count` + update `accessed_at`.** Memory referenced by the platform's own self-improvement loop is by definition more valuable. Verify `search_observations` does this today; if not, ~5 LOC fix during MC1 work.

**D8 — Conflicts surface in MW3 detail pane as banners.** `memory_conflicts` substrate (P1 Slice 2) exists, never surfaced. New UI: detail pane shows `⚠️ Conflicts with observation #Y` banner. Resolution actions: Supersede with X / Supersede with Y / Both true in context (scope_distinct=true flag). ~200 LOC added to MW3.

**D9 — No automatic aging. Operator-driven expiration via `valid_until` field.** Lossless invariant is load-bearing. Use existing `valid_until` column to manually expire. UI default-hides `WHERE valid_until < now()`; "Show expired" toggle exposes.

**D10 — Scope IS the privacy boundary.** Personal-instance distribution → each employee has `scope_kind="personal"` for private memory. Workspace scopes are shared. Global scopes universal. FA respects scope hierarchy. No new column. Defer explicit `visibility` field until multi-user multi-tenant lands.

### Bonus decisions

**D11 — Superseded observations default-hide in UI.** `WHERE superseded_by IS NULL` default; "Show superseded" toggle exposes. M6's listing gets this filter.

**D12 — Confidence score per-source defaults codified:**

| Source | confidence default |
|---|---|
| M1 trajectory | `1.0` if all 3 fields non-null, `0.7` if 2, `0.4` if 1, `0.2` if 0 |
| M5 qualified signal | `min(1.0, max_ruleset_score / 100)` |
| MC approval carryover | `1.0` (operator approved = max confidence) |
| FA `_write_memory` | `1.0` (user explicitly stated) |
| FA conversation drawer | `0.5` (context, not declaration) |

**D13 — Backward compat trivial.** Existing 3 observations + 1 drawer migrate cleanly to multi-scope (1 row each in the new join table mirroring current scope_id). No data loss. Existing single-scope code paths continue to work via the join table.

---

## Why this doc exists

When Jon showed the Node screenshot, the natural impulse was to immediately write a brief. The right move is to capture the vision durably + bank — because:

- M2 and M3+M4 are still in flight; landing those first gives Memory Wings real data to design against
- The wings model has architectural implications (approval lifecycle) that need Jon's call before we write code
- ~1200 LOC across 6 briefs is a multi-week stream; it deserves a plan, not a rush

After Round 2 lands + we've seen real production memory accumulate, this doc becomes the spec for the next stream.
