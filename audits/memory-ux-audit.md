# Memory page UX audit

**Date:** 2026-06-06
**Auditor:** Opus 4.7 (1M)
**Scope:** the Memory page in the app UI + the backend write paths that feed it
**Reference implementation excluded:** Python repo only (Node prototype not consulted)
**Companion doc:** `briefs/memory-ui-redesign.md` is the proposed plan

---

## TL;DR

Memory is wired correctly on the back. The page on the front is so minimal it doesn't show 90% of what the system already knows.

There's also a large second UI hidden in the same file — six "section" lanes, an entity graph drawer, an evidence modal, an archive admin, an "optimize" modal, edit/delete/promote actions — about 1,500 lines of dormant code that **never renders** because the function that builds its data model (`buildMemoryModel`) is never called. Some of that dormant code (Delete, direct content edit) would actually violate the lossless contract in `CLAUDE.md` and shouldn't be revived as-is.

What ships today: a header with four numeric chips, a tabs toggle (Observations / Drawers), a scope dropdown, a flat list, and a detail panel showing the content + evidence links. No search, no filters beyond scope, no actions, no provenance beyond "evidence #id", no recency view, no conflicts UI, no maintenance trigger, no entity browsing — even though backend endpoints exist for several of these.

The instinct that something needs to "view, manage, and maintain" memory is right. The data is rich; the page just doesn't expose it. The fix is not to add a lot of new backend — most of what's needed already exists. The fix is to design a real surface around it.

---

## 1. How the page renders today

### The mount
- Rail item in `public/index.html:153` — `data-nav="memory"` button labelled "Memory".
- Entry: `public/js/features/memory-shell.js` → `loadMemoryShell()` (line 274).
- That function fetches three things in parallel:
  - `GET /api/memory/stats`
  - `GET /api/memory/scopes`
  - `GET /api/memory/observations?limit=50&offset=0`
- And then calls `renderM6Shell()` (line 323), which is the only rendering path that actually runs.

### What you actually see
A hero block with the title "Memory" and four chips:
- `N drawers`
- `N observations`
- `N evidence links`
- `N scopes`

Below that, a toolbar with two pieces:
- A tabs toggle: **Observations** | **Drawers**
- A single dropdown labelled **Scope** with options like `agent · marketing.scout.regional_news (12d / 8o)` and "All scopes"

Below that, a two-column layout:
- **Left column (list panel):** a list of clickable rows. Each row shows:
  - Top line: `scope_kind · scope_id` (e.g. `agent · marketing.scout.regional_news`)
  - A 120-character content preview
  - A timestamp like `Jun 4, 09:21`
  - If the observation is superseded, a small "superseded" badge appears on the right
- **Right column (detail panel):** when you click a row, fetches `GET /api/memory/observations/{id}` and shows:
  - "Observation #N" eyebrow
  - Scope label
  - Timestamp (full)
  - The full content as a styled quote
  - If superseded: a one-line note "Superseded by observation #M"
  - "Backed by (N)" evidence list — each item showing `source_kind #source_id` and a short preview, no link to the source itself

That's it. There is no search, no action button, no category badge, no score, no risk indicator, no provenance trail beyond what's already in evidence, no way to follow a supersession chain backward, no way to see entities mentioned, no way to jump to "what was added today".

### Empty state
> "Memory is still populating. New observations will appear here as agents run and signals qualify."

It's gentle but only fires when the database is empty. The much more common state — sparse for one scope but full for another — falls through to a list of rows that look identical regardless of why they exist.

### Pagination
`Showing 50 of N total` line at the bottom of the list. No page-2 button is wired. If a scope has more than 50 observations, you literally cannot see the rest from the UI.

---

## 2. Where the data comes from

This is where the gap between what exists and what's visible gets wide.

### Read endpoints the page does use
- `GET /api/memory/stats` — totals + a `by_scope_kind` breakdown the page **fetches but ignores**.
- `GET /api/memory/scopes` — for the dropdown.
- `GET /api/memory/observations` — list.
- `GET /api/memory/drawers` — list (only on tab switch).
- `GET /api/memory/observations/{id}` — detail with evidence chain.

### Read endpoints the page does NOT use
- `GET /api/memory/conflicts` — there's a whole conflicts table with unresolved rows. Page doesn't show them anywhere.
- `GET /api/memory/observations/{id}/history` — walks the full supersession chain (newest → oldest ancestor). Page only shows the immediate `superseded_by` ID with no link.
- `GET /api/memory/embeddings/status` — stub today, but the surface exists.

### Write endpoints the page does NOT use
- `POST /api/memory/conflicts/{id}/resolve` — conflict resolution with four resolution types (`a_wins`, `b_wins`, `both_valid_different_scope`, `manual_review_needed`). No UI hook.
- `POST /api/memory/maintain` — runs score decay and produces row counts per category. No UI hook.

### Where memory entries are actually born (none of this is visible in the page)
Memory writes happen in seven places across the codebase. The page doesn't tell you any of this:

1. **Scout signal qualifier** (`artemis/tools/signal_queue_ops.py:201`) — when a scout signal passes qualification, an observation lands.
2. **Trajectory summarizer** (`artemis/builder/trajectory_summarizer.py`) — fires after every agent run; writes a structured "what happened" observation as a background task.
3. **Memory carryover observers MC1-MC5** (`artemis/builder/memory_carryover.py`) — when you approve a definition proposal, a Gate-1 signal, a skill promotion, a pipeline gate, or a Floating-Artemis-driven action, an observation lands.
4. **Floating Artemis turn drawer** (`artemis/floating_artemis/memory.py:77`) — every chat turn writes a verbatim drawer.
5. **Floating Artemis `write_memory` tool** (`artemis/floating_artemis/tools/core.py:81`) — when Artemis calls `write_memory` mid-conversation, an observation lands.
6. **Consolidator** (`artemis/memory/consolidator.py:256`) — `apply_consolidation` writes consolidated observations and supersedes the sources.
7. **Graph extractor** (`artemis/memory/graph_extractor.py`) — extracts entities + relations from observations after consolidation completes.

### Background jobs the page doesn't acknowledge
1. **Score decay** — APScheduler job at 03:00 UTC every day. Multiplies `score` by category-specific factors (discovery 0.93, decision 0.97, convention 0.99, warning 1.0, unknown 0.95). Backend endpoint `POST /api/memory/maintain` triggers the same logic on demand. No UI surface for either.
2. **Incremental consolidator** — fires per (scope, category) when 25 observations have accumulated, with a 120-second debounce. Calls an LLM to consolidate, applies proposals, supersedes sources.
3. **Graph extractor with retry** — exponential backoff (0s → 60s → 5m → 30m → 2h) on top of the consolidation pipeline.

A user looking at the page has no way to see "consolidation just ran on this scope", "decay reduced these scores yesterday", "graph extraction is queued behind a failure", or "five observations were superseded in the last hour."

---

## 3. What backend knows about each memory that the page hides

Every observation row has these fields. The list view shows only the bolded ones, and the detail view adds one more (the supersession id):

- id, **scope_kind**, **scope_id**, **content**, content_hash
- category (warning / discovery / decision / convention) — never shown
- score (float) — never shown
- hit_count — never shown
- source_quality — never shown
- user_confirmed (bool) — never shown
- valid_from / valid_until — never shown
- **superseded_by** — only as a badge
- raw_input_id — never shown
- confidence + confidence_origin — never shown
- evidence_count — never shown directly
- graph_status + graph_attempt_count — never shown
- wing — never shown
- **created_at** — shown
- accessed_at — never shown
- multi-scope membership (memory_observation_scopes) — never shown

This is the heart of the problem. The page is asking the database for a dozen useful signals per row and then throwing all but three of them away.

---

## 4. The dormant second UI

`memory-shell.js` is 2,181 lines. The active M6 path is roughly lines 274–535. Lines 35–273 and 537–2181 are a fully-fleshed-out alternative UI that no longer renders. Concretely, it contains:

**A section nav (left rail)** with six lanes — Needs Review, Artemis Knows, Projects, Working, Agents, Skills/Rules — each with descriptions and empty states.

**A wings/rooms scope browser** — a tree where "wings" are project- or agent-level groupings and "rooms" are categories.

**An archive admin card** with four buttons: Export JSON, SQLite backup, Validate import, Apply import.

**An "Optimize" modal** that previews an LLM-driven memory prune (before/after columns, "noise removed" counter).

**Per-row CRUD actions** in the detail pane: Edit content, Delete, Retag category, Promote to Skill.

**An evidence detail modal** that loads a full drawer when an evidence row is clicked.

**An entity neighborhood drawer** that lists entities mentioned by a memory, lets you expand 1-hop neighbors with predicate labels (`works_on`, `belongs_to`, etc.), and supports "filter the list to memories that mention this entity."

**Search box, filter chips by bucket (Artemis / Agents / Projects / Skills), risk/durability badges per row.**

All of this is registered against `handleMemoryShellAction` and `renderCurrentMemoryShell`, but `memoryModel` is never populated because `buildMemoryModel` is never invoked. Hitting any of those buttons today would no-op.

**Two of these features are incompatible with the lossless contract in CLAUDE.md and should not be revived as written:**
- Delete (line 681) calls `deleteMemoryApi(row.id)`. The lossless rule forbids hard deletion of observations.
- Edit (line 783) calls `updateMemoryApi(row.id, content, category)` which would mutate `content_hash`, breaking the dedupe/supersession model.

The rest of the dormant code (sections, wings, search, evidence drilldown, entity neighborhood, archive export, optimize, retag, promote-to-skill) is conceptually compatible with the lossless rule but was paused before the M6 contract landed and now sits at risk of being half-revived in a way that conflicts with the M1 invariants.

A redesign should treat this code as **inspiration**, not as a fork to merge back in.

---

## 5. The information-architecture problem

Three structural choices in the M6 shell are working against Jon's instinct that something should be navigable:

**(a) "Observations vs Drawers" is a storage-layer toggle, not a user-layer toggle.** Drawers are verbatim raw inputs; observations are curated claims. Most users want to read what Artemis "knows," not the raw transcripts that fed it. Demoting drawers to a secondary tab (or hiding them behind a "see source" affordance on the observation) would make the curated layer the front door.

**(b) The scope dropdown is flat.** Options look like `agent · marketing.scout.regional_news (12d / 8o)` — a raw composite string. In reality scopes form a hierarchy (workspace → project → agent → sub-agent) and the page could show that as a tree. Without a tree, Jon has no spatial sense of "where in the system this memory lives."

**(c) The four hero chips are dead numbers.** "247 drawers · 89 observations · 412 evidence links · 6 scopes" is a database snapshot. A "memory pulse" — *added today*, *needs attention*, *last consolidation Xh ago*, *N conflicts pending* — would tell a user where to actually click first.

---

## 6. What's missing that backend already supports

These are things the system already knows or can do; the page just doesn't expose them:

| Capability | Backend status | UI status |
|---|---|---|
| List unresolved conflicts | Endpoint exists (`GET /api/memory/conflicts`) | Not surfaced |
| Resolve a conflict | Endpoint exists (`POST /api/memory/conflicts/{id}/resolve`) | Not surfaced |
| Supersession chain (full lineage) | Endpoint exists (`/observations/{id}/history`) | Not surfaced (only immediate `superseded_by` ID shown) |
| Trigger decay/maintenance | Endpoint exists (`POST /api/memory/maintain`) | Not surfaced |
| Score per observation | In data model | Not surfaced |
| Category (warning/discovery/decision/convention) | In data model | Not surfaced |
| user_confirmed flag | In data model | Not surfaced and no path to set it |
| Multi-scope membership | In `memory_observation_scopes` table | Not surfaced |
| Entities + relations (graph layer) | Tables + APIs exist | Not surfaced |
| "When did this last get read" (accessed_at) | In data model | Not surfaced |
| `by_scope_kind` breakdown | Returned by `/stats` | Fetched and ignored |
| Embedding queue status | Endpoint stubbed | Not surfaced |

---

## 7. What's missing that's not yet built (but would matter)

These would be small additions if the redesign calls for them:

- **"Recently added" feed** — order observations by `created_at desc` with no scope filter; gives a "what just happened in memory" view.
- **Provenance resolver** — when an observation's evidence is `agent_run #N`, render the agent's name and the run's purpose; when it's `signal_queue #M`, render the signal headline; when it's `drawer #K`, render a snippet. Today the UI shows raw IDs.
- **"Pin" / "Mark as confirmed" actions** — `user_confirmed` already exists in the schema; surfacing it as a low-risk action gives Jon a way to curate without violating lossless.
- **"Retire" action** — sets `valid_until = now()` without supersession. Lossless-compatible; the row stays for audit.
- **"Send back to review"** — would need a small writeback that toggles a flag and surfaces in a conflicts-or-review queue (lightweight).
- **Floating-Artemis handoff** — "open this memory in chat with Artemis" deep-link so the user can ask follow-up questions.

---

## 8. Why it feels like "one long list of coded memory that's not clickable"

Three things conspire:

1. **Rows look identical** because none of the categorical or scoring metadata is rendered on the row — every row is the same shape regardless of whether it's a high-confidence convention or a stale warning.
2. **Click DOES work, but the detail panel is also flat** — it shows the same content already visible in the preview, plus an evidence list of raw `source_kind #id` strings. There's no story, no provenance chain, no action to take.
3. **There's no second-level navigation** — once you've used the scope dropdown and the tabs toggle, you're out of moves. Nothing pulls you deeper into the data.

This is fixable without a single backend endpoint addition for the first pass.

---

## 9. Constraints any redesign must respect

These are non-negotiable per `CLAUDE.md` and the M1 design:

- **Lossless.** No DELETE on observations or drawers. Retirement = `valid_until` set; supersession = explicit FK. No public delete endpoint exists, and the dormant Delete button should not be revived.
- **No direct content edit.** Observations are dedupe-keyed on `content_hash`. A redesign cannot expose a "change the words" affordance; if the user wants to refine, they supersede instead.
- **All writes pay the M1 evidence rule.** Any new action that creates an observation needs to also link evidence (or the action itself becomes the evidence — e.g., "Jon confirmed at HH:MM").
- **Read endpoints require token** (`Depends(require_token)`), write endpoints follow the existing auth pattern. Already in place.

---

## 10. Summary of what's wrong, in one paragraph

The page is a working but skeletal viewer over a rich dataset. The intent ("M6: read-only viewer, no writes") was correct for the moment it was built. But it stopped at minimum viable, so it never surfaces category, score, recency, provenance chain, entities, conflicts, maintenance state, or the multi-source write activity behind it. There's also a much richer UI half-built in the same file that's dormant and partially incompatible with the lossless rule. The redesign is mostly an *exposure* job: take what backend already knows, render it with hierarchy, recency, and a small set of lossless-safe maintenance actions, and give Jon a real "memory shell" instead of a viewer pinned to row #1 of a SELECT.

What that looks like concretely is the companion brief: `briefs/memory-ui-redesign.md`.

---

## Appendix A — Graph coverage check (2026-06-06)

Quick read-only check of the graph layer as part of resolving Phase 5 scope:

```
entities_total                  | 0
relations_total                 | 0
observations_with_graph_done    | 0
observations_graph_status_null  | 238
observations_graph_failed       | 0
```

**Interpretation:** 238 observations exist; the graph extractor has not produced a single entity or relation. `graph_status IS NULL` on all 238 means the extractor was never invoked (not "tried and failed"). Either the extractor isn't wired into the live consolidation flow, or it's wired but silently no-ops.

**Consequence for the redesign:** Phase 5 (People & Things entity browser) is deferred until the graph pipeline is producing. A prerequisite ticket lives at `briefs/memory-phase-5-prereq-graph-extractor-audit.md` to investigate and backfill. Phases 1, 2, 3, 4, 6 are unaffected and proceed.
