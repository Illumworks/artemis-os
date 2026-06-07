# Memory UI redesign — phased plan

**Date:** 2026-06-06
**Author:** Opus 4.7 (1M) — terminal-Lead
**Companion doc:** `audits/memory-ux-audit.md` (read first for context)
**Status:** PLAN — all five open questions resolved 2026-06-06 with Jon. Per-phase Worker briefs land at `briefs/memory-phase-*.md`; this doc is the master plan they reference.

## Locked decisions (2026-06-06)

| # | Decision | Rationale |
|---|---|---|
| 1 | **Phase 2 (Provenance & lineage) ships first**, then Phase 1 (Rows speak), then 3 → 4 → 6 | Provenance is the most emotionally compelling first impression — opening any memory tells you where Artemis picked it up from. |
| 2 | **Phase 3 ships both Pin and Confirm** | They answer different questions: Pin = cosmetic float-to-top; Confirm = "I vouch for this" (sets `user_confirmed = true`). |
| 3 | **Retire requires a free-text reason** | Reason stored as evidence on the retirement. Six months later you can always tell why a memory left the active set. |
| 4 | **Phase 5 (People & Things) is deferred** | Coverage check on 2026-06-06: 0 entities, 0 relations, 238 observations with `graph_status IS NULL`. Graph extractor never fired on the live consolidation flow. Phase 5 becomes a prerequisite ticket (see `briefs/memory-phase-5-prereq-graph-extractor-audit.md`). Phases 1, 2, 3, 4, 6 proceed; Phase 5 is greenlit once entities + relations populate. |
| 5 | **Header stays "Memory"** — voice lives in the pulse line below ("247 memories · 12 new today · 3 need attention") | System-noun title matches the rail item; pulse line carries Artemis voice without forcing it into the title. |
**Audience:** Jon (decision-maker) + future Worker (Codex / Sonnet sub-agent) when a phase is greenlit.

---

## North star

Memory should feel like **a room you can walk into**, not a database dump. Today it's a SELECT with a sidebar. The redesign turns it into a place where Jon can:

- **See** what Artemis remembers, with hierarchy + recency + confidence
- **Trust** it (provenance trail, supersession lineage, who/what wrote it)
- **Curate** it within the lossless contract (pin, confirm, retire, supersede, resolve conflicts)
- **Notice** when it grows, when it drifts, when it disagrees with itself, when consolidation runs

The Artemis voice ("she remembers", "she picked this up from") is the connective tissue. Numbers without narrative is exactly what we have today.

---

## Hard constraints (read these first)

These don't change between phases. Everything below respects them:

1. **Lossless.** No DELETE button. No content-mutation button. Retire = set `valid_until`, supersede = explicit FK write. The dormant Delete + direct-Edit in the current file must not be revived.
2. **All actions are evidence-bearing.** When Jon clicks "Mark confirmed" or "Retire" or "Resolve conflict", the action itself is logged as the evidence (resolver = "operator", resolved_at = now, reason = freetext). The backend already does this for `resolve_conflict`.
3. **Read-only by default, write-on-confirm.** Destructive-ish actions (retire, supersede, resolve) get a single confirm step, not a chain of dialogs.
4. **Reuse existing tokens.** Visual style stays in the `memory-shell-*` and `m6-*` CSS vocabulary already in `public/css/panels/memory.css` (217 classes, both dormant and active). Don't introduce a new design language; refine the one Artemis already has.
5. **Phased delivery.** Each phase ships independently and is useful on its own. No phase blocks the others if Jon wants to reorder.

---

## Information architecture (target end-state)

This is what the page should be once all phases land. Each phase brings us closer.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  HEADER — "What Artemis remembers"                                       │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ Pulse: 247 memories · 12 new today · 3 need attention · last       │  │
│  │ consolidation 2h ago                                              │  │
│  │ [sparkline of last 14 days, observations/day]                      │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  TABS: [ Knowledge ]  [ Evidence ]  [ People & Things ]  [ Health ]     │
│                                                                          │
│  ┌──────────────┬──────────────────────────────┬──────────────────────┐ │
│  │ SCOPE TREE   │ LIST                         │ DETAIL               │ │
│  │              │                              │                      │ │
│  │ ▼ Workspace  │ Filters: Category · Recency  │  Quote               │ │
│  │   ▼ Projects │ · Status · Search            │                      │ │
│  │     Amira    │                              │  Provenance trail    │ │
│  │   ▼ Agents   │ [Row] cat·scope·preview·     │                      │ │
│  │     Scout    │ score·age·evidence·conflict? │  Lineage timeline    │ │
│  │     Daily    │ [Row]                        │                      │ │
│  │     ...      │ [Row]                        │  Evidence (resolved) │ │
│  │   ▼ Brands   │ ...                          │                      │ │
│  │     Amira    │                              │  Entities mentioned  │ │
│  │              │ ────────────────────────     │                      │ │
│  │              │ Showing 50 of N · ⟨ ⟩         │  Conflicts (if any) │ │
│  │              │                              │                      │ │
│  │              │                              │  Actions:            │ │
│  │              │                              │  📌 Pin · ✓ Confirm  │ │
│  │              │                              │  ⊘ Retire · 💬 Ask   │ │
│  └──────────────┴──────────────────────────────┴──────────────────────┘ │
│                                                                          │
│  CONFLICTS DRAWER (slides up when conflicts exist)                       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Phases

The phases are ordered by *value per line of code*. Phase 1 is the smallest, most impactful change. Phase 5 is voice/polish.

Each phase below has: **what it adds**, **what it touches**, **whether new backend is needed**, **acceptance check**.

---

### Phase 1 — "Make rows speak" (smallest valuable change)

**The fix:** Today every row looks identical. After Phase 1, you can tell at a glance whether a memory is a confident decision or a stale warning.

**What it adds:**
- Each row in the list shows: category badge (color-coded: warning = red, discovery = amber, decision = blue, convention = green), a small score gauge (0–100), recency label ("2h ago", "yesterday", "Mar 14"), and an evidence-count chip ("3 sources").
- Filter chips above the list: **Category** (All / Warning / Discovery / Decision / Convention), **Recency** (Today / This week / This month / All), **Status** (Active / Superseded / All). Each chip shows a count.
- A search box (debounced 300ms; client-side filters the loaded page; matches `content` substring).
- Sort dropdown: Recent (default) / Most cited / Highest score.
- Hero stays for now, but adds two derived numbers: **"N new today"** and **"N need attention"** (= unresolved conflicts + warnings with score > 0.8). Both come from the existing `/stats` shape + one small derived endpoint or client-side calc.

**What it touches:**
- `public/js/features/memory-shell.js` — extend `renderM6ListPanel`, add filter UI, plumb category/score into the row template. ~150 LOC.
- `public/css/panels/memory.css` — category-badge variants (4 colors), score-gauge component, chip-row. Reuse existing tokens. ~80 LOC.
- `artemis/memory/repository.py` — extend `list_observations` to project `category`, `score`, `evidence_count`, and accept new query params `category`, `recency_from`, `status`. ~40 LOC.

**Backend changes:** Yes, but small. Extending an existing endpoint, no new tables.

**Acceptance:**
- A row visually distinguishes warning vs decision at a glance.
- Clicking a category chip narrows the list and the count line updates.
- Search filters in <100ms for the current page.
- Hero shows "12 new today, 3 need attention" with non-zero correctness against a seeded DB.

**Estimated LOC:** ~300 backend+frontend.

---

### Phase 2 — "Tell me where it came from" (provenance + lineage)

**The fix:** The detail panel today shows `drawer #4731 · preview...` — opaque. After Phase 2, it tells you the story: who wrote it, what produced it, what it superseded, what superseded it.

**What it adds (in the detail panel only):**

- **Provenance block** above the quote:
  > "Picked up by **scout signal qualifier** on **June 4 at 09:21**, while qualifying signal *'Houston ISD adopts new ELA framework'*."

  Resolution rules:
  - if evidence `source_kind = drawer` and the drawer's `source_kind = floating_artemis_turn` → "from your conversation with Artemis at HH:MM"
  - if `source_kind = agent_run` → "from agent **{agent.name}** on run *{run.purpose}*"
  - if `source_kind = signal_queue` → "from scout signal *{signal.headline}*"
  - if `source_kind = legacy_memory` → "imported from a legacy memory archive"
  - fallback → "from {source_kind} #{source_id}"

- **Lineage timeline** below the quote:
  - Walks `GET /api/memory/observations/{id}/history` (already exists).
  - Visual: a vertical timeline. Most-recent at top (this observation), older ancestors below, with timestamps and a 60-char preview each.
  - If the current observation has been superseded, show the descendent at the very top with "Replaced by → #{newer_id}: *preview*" and a clickable jump.

- **Evidence list (resolved)**: replace `drawer #4731 · preview` with `From your conversation with Artemis · "...preview..."` — same resolution rules as provenance.

- **Authority badges** in the meta row: `user_confirmed: yes/no`, `confidence: X% (from {confidence_origin})`.

**What it touches:**
- `public/js/features/memory-shell.js` — `renderM6DetailPanel`, add `renderProvenance`, `renderLineage`, `resolveSource`. ~200 LOC.
- `artemis/memory/repository.py` — extend `get_observation_detail` to return resolved source previews for `agent_run`, `signal_queue`, `floating_artemis_turn`, instead of just drawer/observation. ~80 LOC.
- A small server-side resolver helper in `artemis/memory/source_resolution.py` (new file). ~60 LOC.

**Backend changes:** Yes — source resolution helper + extending detail endpoint. No new tables.

**Acceptance:**
- Opening any observation produced by a Scout signal shows "from scout signal '<headline>'" with the actual headline.
- Opening an observation with a 4-step supersession chain renders 4 timeline entries.
- The "Replaced by →" link works (selects the newer observation).

**Estimated LOC:** ~340.

---

### Phase 3 — "Let me curate" (lossless-safe maintenance actions)

**The fix:** Today there is zero affordance to act on a memory. After Phase 3, Jon has four lossless-safe actions and conflicts have a resolution UI.

**What it adds:**

Four actions in the detail panel:

1. **Pin** — adds a `pinned_at` timestamp (new column, default null). Pinned observations float to the top of the list within their scope and get a small pin icon on the row. No effect on retrieval/recall — purely a UI surface.
2. **Mark confirmed** — sets `user_confirmed = true` and `confidence_origin = 'operator'`. Already in the schema, no new column needed.
3. **Retire** — sets `valid_until = now()` and logs the actor as evidence ("Retired by operator at HH:MM, reason: '<free text>'"). Lossless: the row stays, retrieval excludes it.
4. **Supersede with…** — opens a small composer where Jon types a replacement claim. On save: writes a new observation, sets `supersedes = <old_id>`, `superseded_by` on the old → new. The new observation gets evidence linked to the old, plus the operator-as-author evidence.

A **Conflicts drawer** at the bottom of the page, collapsed by default, with a red dot when unresolved conflicts exist:
- Lists each unresolved row from `/api/memory/conflicts`.
- Each conflict opens a side-by-side view (A vs B) with content + score + age.
- Action buttons: **A wins**, **B wins**, **Both valid (different scope)**, **Need human (mark for later)** — all four resolutions the backend already accepts.
- After resolution, the conflict slides out of the drawer with a confirmation toast.

**What it touches:**
- New backend endpoint: `POST /api/memory/observations/{id}/pin` (toggle) — ~30 LOC.
- New backend endpoint: `POST /api/memory/observations/{id}/confirm` — ~30 LOC.
- New backend endpoint: `POST /api/memory/observations/{id}/retire` (takes `reason`) — ~50 LOC.
- New backend endpoint: `POST /api/memory/observations/{id}/supersede` (takes `content` + `reason`) — wraps existing `write_observation` + `supersede_observation`. ~80 LOC.
- Alembic migration to add `memory_observations.pinned_at` nullable timestamp.
- Frontend: action buttons + composer modal + conflicts drawer. ~250 LOC.

**Backend changes:** Yes — 4 new endpoints, 1 new column. All lossless-compatible.

**Acceptance:**
- Pinning a memory floats it to the top of its scope's list.
- Marking confirmed flips the badge and persists across reloads.
- Retiring an observation sets `valid_until` and the row disappears from default views but stays under "Status: All".
- Superseding writes a new observation linked back to the old one; both appear in the lineage timeline.
- Conflicts drawer surfaces all unresolved conflicts and the four resolution buttons match backend behavior.

**Estimated LOC:** ~440 + 1 migration.

---

### Phase 4 — "Where in the system does this live?" (scope hierarchy + recently-added feed)

**The fix:** The flat scope dropdown becomes a real tree. Adds a sense of place. Also adds a "what just landed" feed since recency-by-scope is the question Jon will ask most often.

**What it adds:**

- **Scope tree (left rail)** replaces the dropdown:
  - Top level: scope kinds (Workspace, Project, Agent, Brand, Global, Floating Artemis).
  - Second level: scope_ids under each kind, with `(drawer / observation)` counts.
  - Click a node → narrows the list to that scope (and descendants where applicable).
  - "All scopes" remains as the root selection.
  - Active node highlighted; counts update live when actions move memories around.
- **"Recently added" pseudo-scope** at the top of the tree:
  - Selects "All scopes" + sort=Recent + recency=Today.
  - Effectively the "what just landed" feed.
- **Multi-scope display** — when an observation belongs to >1 scope (via `memory_observation_scopes`), the row shows a small `+N scopes` chip; the detail shows all scopes with weight.

**What it touches:**
- `public/js/features/memory-shell.js` — replace dropdown with tree component; tree state stays in `m6State`. ~150 LOC.
- `public/css/panels/memory.css` — tree styles, expand/collapse, indent rails. ~80 LOC.
- `artemis/memory/repository.py` — extend `list_scopes` to return parent/child relationships, and extend `list_observations` to support `include_descendants=true`. ~60 LOC.

**Backend changes:** Yes — extend list endpoints. No new tables.

**Acceptance:**
- "Recently added" returns observations sorted by `created_at desc` regardless of scope.
- Clicking "Workspace > Projects > Amira" narrows to that project; clicking "Projects" alone shows the union of all project-scoped memory.
- A memory belonging to both `agent:scout` and `brand:amira` shows both chips and resolves to both subtrees.

**Estimated LOC:** ~290.

---

### Phase 5 — "Show me the people and things" (entity browser)

**The fix:** Entities and relations already get extracted by the graph extractor; the page never shows them. Phase 5 surfaces the graph as a third tab.

**What it adds:**

- **People & Things tab** (third tab after Knowledge and Evidence):
  - Entity grid grouped by `entity_kind` (Person, Project, Brand, Campaign, Channel, Post, Other).
  - Each entity card: canonical name, kind badge, mention count, aliases (if any), last-seen date.
  - Click → entity drawer with: full mention list, 1-hop neighborhood (predicate + object), "filter Knowledge to memories that mention this" handoff.
- **Entity chips inside the detail panel** (cross-cuts Phase 2): a row shows entities mentioned in this observation; clicking a chip opens the entity drawer.

**What it touches:**
- `public/js/features/memory-shell.js` — entity tab renderer + entity drawer. ~200 LOC. (Note: most of this exists in dormant code at lines 1954–2079 and can be ported.)
- Reuses existing APIs: `fetchMemoryEntitiesApi`, `fetchEntityNeighborhoodApi`.

**Backend changes:** No.

**Acceptance:**
- Visiting People & Things with a non-empty graph shows entities grouped by kind with mention counts.
- Clicking an entity expands its 1-hop neighborhood with predicate labels.
- "Filter Knowledge to this" handoff narrows the Knowledge tab.

**Estimated LOC:** ~200 (mostly porting the dormant entity-drawer code, which is the lossless-compatible portion of the dormant UI).

---

### Phase 6 — "Make Artemis show up" (voice + Floating Artemis handoff)

**The fix:** The page reads like a database admin tool. After Phase 6 it reads like Artemis showing you what she remembers, and you can hand any memory back to her in chat for a follow-up.

**What it adds:**

- Header copy: "Memory" → "What Artemis remembers" (or "Artemis's memory" — Jon picks).
- Pulse line in Artemis voice: "She's working from **247 observations** across **6 scopes**. **12 new today**. **3 need a look.**"
- Empty state per scope: "Artemis hasn't picked anything up in **{scope}** yet."
- Provenance copy in Artemis voice: "She picked this up from..." instead of "Sourced from..."
- **"Ask Artemis about this"** button in the detail panel actions. Opens Floating Artemis with the memory pre-loaded as context — uses the existing FA-handoff pattern (`localStorage` seed + `setState("view", "floating-artemis")` from `home.js`).
- **Health panel** (fourth tab, low priority): last decay run timestamp + manual trigger button, consolidation queue depth, embedding queue depth, graph extractor retry queue.

**What it touches:**
- Copy passes across all renderers in `memory-shell.js`.
- FA handoff wiring — uses existing `floating_artemis.js` entry points; needs a one-line seed format agreement.
- Health panel calls existing `/api/memory/embeddings/status` + (new) `/api/memory/maintenance/status` returning last-run timestamp.

**Backend changes:** Tiny — one new GET for maintenance status. ~20 LOC.

**Acceptance:**
- Header copy is Artemis-voiced and tested with Jon.
- "Ask Artemis about this" opens FA panel with the memory's content + provenance in scope; FA reads from that context on the next turn.
- Health panel shows when decay last ran and lets Jon trigger it on demand.

**Estimated LOC:** ~120.

---

## Phase summary

| Phase | Headline | New backend | LOC | Risk | Order |
|---|---|---|---|---|---|
| 2 | Provenance + lineage in detail panel | extend `/observations/{id}` | ~340 | low | **1st** |
| 1 | Make rows speak (category + score + filters + search) | extend `/observations` | ~300 | low | 2nd |
| 3 | Pin / confirm / retire (with reason) / supersede + conflicts drawer | 4 new endpoints + 1 column | ~440 + migration | medium (write paths) | 3rd |
| 4 | Scope tree + recently-added feed | extend `/scopes`, `/observations` | ~290 | low | 4th |
| 6 | Voice + FA handoff + Health panel | tiny | ~120 | low | 5th |
| 5 | People & Things tab (entity browser) | **DEFERRED** — prereq: graph extractor audit + backfill (briefs/memory-phase-5-prereq-graph-extractor-audit.md) | ~200 | low | TBD |
| **Active total** | | **5 new + 4 extended endpoints, 1 column** | **~1490** | | |

For comparison, the dormant code in `memory-shell.js` today is ~1,500 LOC of stale UI that doesn't render.

---

## What we are NOT doing

Explicitly out of scope, to keep the redesign honest:

- **No revival of Delete or direct content-Edit.** They violate the lossless rule.
- **No revival of "Optimize" modal.** LLM-driven pruning is an automated background job (the incremental consolidator), not a UI button. Surfacing its activity is fine; letting a user trigger it ad-hoc on whole scopes is risky.
- **No revival of archive Export/Import UI.** That's an ops concern, not a user concern; if needed, it lives in a Settings or Operations panel, not on the Memory page.
- **No new visual language.** Stick to existing tokens.
- **No mobile-specific layouts.** The page is desktop-first like the rest of the app.

---

## Open questions — resolved

All five resolved on 2026-06-06. See the "Locked decisions" table at the top of this doc. Per-phase briefs reference back to those decisions.

---

## Process notes

- Each phase wants its own Worker brief (we'd lift the relevant section above into a `briefs/memory-phase-N-…md` with concrete acceptance checks, test names, and file lists).
- All phases land on `worker/memory-phase-N-…` branches per the local-only git convention in `CLAUDE.md`.
- Phase 3 is the only one with write paths; Lead browser-smokes it post-merge with a seeded DB before declaring done.
- After all phases, the dormant code in `memory-shell.js` (sections, wings, archive admin, optimize, delete, direct-edit, evidence-modal, neighborhood-drawer) can be deleted in a cleanup commit. The audit doc and this brief are the rationale.
