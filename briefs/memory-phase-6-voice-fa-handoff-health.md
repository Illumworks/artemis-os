# Memory Phase 6 — Voice + Floating Artemis handoff + Health panel + dormant code cleanup

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/memory-phase-6-voice-fa-health-cleanup`
**Browser smoke owner:** Lead, post-merge — verify the pulse line reads in Artemis voice; "Ask Artemis about this" opens FA panel with memory pre-loaded; Health tab shows maintenance status.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 + dormant code deletion. Two tickets in one — light feature work plus a cleanup that removes ~1,500 lines of dead code from `memory-shell.js`.
**Priority:** MEDIUM — final phase of the active redesign. Ships after Phases 2, 1, 3, 4.
**Parent plan:** `briefs/memory-ui-redesign.md`
**Companion audit:** `audits/memory-ux-audit.md`
**Depends on:** Phases 2, 1, 3, 4 merged. Cleanup step depends on Lead confirming nothing in the dormant code is still load-bearing.

---

## Why this exists

Per the audit, the page reads like a database admin tool. Phase 6 adds the Artemis voice in the right places (pulse line, provenance copy, empty states), wires a "Ask Artemis about this" handoff into Floating Artemis, and surfaces system Health (last maintenance, consolidation activity, queue depth) so Jon can see when the memory pipeline is or isn't running.

Locked decision (2026-06-06): **header label stays "Memory"** — the pulse line carries the voice, not the title.

Also: ~1,500 lines of dormant code in `public/js/features/memory-shell.js` from the pre-M6 design (sections, wings, archive admin, optimize, delete, direct-edit, evidence modal, neighborhood drawer). Some of it is incompatible with the lossless rule. After Phases 1-4 cover the same capabilities (where they should be covered), the dormant code becomes deletable. Phase 6 is the right moment to retire it.

---

## Scope

### Part A — Voice copy pass

Edit `public/js/features/memory-shell.js`. Five copy changes; no logic changes:

1. **Hero pulse line** (set in Phase 1, refined here):
   - Current: "247 memories · 12 new today · 3 need attention · 6 scopes"
   - Phase 6: leave as-is. (Locked decision: keep this functional.)
2. **Empty state — no memories at all**:
   - "Memory is still populating. New observations will appear here as agents run and signals qualify."
   - → "Artemis hasn't picked anything up yet. As agents run and signals qualify, observations will land here."
3. **Empty state — no memories in selected scope**:
   - New copy: "Artemis hasn't picked anything up in **{scope_label}** yet."
4. **Empty state — filters return zero**:
   - "No memories match the current filters" stays; no voice needed (functional message).
5. **Provenance prose** (set in Phase 2):
   - Current: "She picked this up from {label} on {date}."
   - Keep, but verify it reads naturally for all source kinds.
6. **Lineage timeline header** (set in Phase 2):
   - Currently has no header.
   - → "How she got here" above the timeline.
7. **Authority row**:
   - Currently: "confidence: 80% (from operator), user_confirmed: yes"
   - → "She's 80% sure about this (operator-confirmed)." If `confidence_origin = 'system'`: "Confidence: 80% (system estimate)."

### Part B — Floating Artemis handoff

Add a new action button to the detail panel's action row (alongside Phase 3's Pin/Confirm/Retire/Supersede): **💬 Ask Artemis about this**.

On click:

1. Build a context payload:
   ```js
   {
     "memory_id": <obs.id>,
     "content": <obs.content>,
     "scope": "<scope_kind>:<scope_id>",
     "provenance": "<resolved provenance label from Phase 2>",
     "captured_at": "<obs.created_at>"
   }
   ```
2. Write to `localStorage` under key `artemis-fa-seeded-memory`.
3. Call `setState("view", "floating-artemis")` to navigate to FA panel.

Edit `artemis/floating_artemis/chat.py` (or wherever the FA system prompt assembly lives — verify in `artemis/floating_artemis/` first):

- On first turn after navigation, if `localStorage` has `artemis-fa-seeded-memory`: prepend a system message of form:
  > "The user is asking about this memory: [content]. (Picked up by [provenance], scope: [scope].)"
- After consumption, the localStorage key is cleared by the FA frontend so it doesn't leak into subsequent turns.

If FA chat assembly is server-side and doesn't read from localStorage: instead, when the user navigates to FA with the seed key set, the FA frontend's first turn submission includes a `seeded_context` field that the server-side chat handler injects into the prompt.

**Worker discretion**: pick the cleanest path that matches the existing FA context-loading pattern. Document the chosen path in the PR description.

### Part C — Health panel (fourth tab)

Add a fourth tab to the existing tabs row in `renderM6Shell`: **Knowledge · Evidence · People & Things · Health**.

(People & Things is Phase 5 — if deferred, the tab is not present yet. Phase 6 just adds Health as the rightmost tab.)

**Health tab renders three sections:**

1. **Maintenance** (uses existing `POST /api/memory/maintain` + new GET):
   - New endpoint: `GET /api/memory/maintenance/status` — returns `{"last_run_at": ISO, "last_run_counts": {...}, "next_scheduled_at": ISO}`. Pull from APScheduler if accessible; fall back to "Unknown — check logs" if not.
   - UI: "Last decay ran **{when_relative}**. Next scheduled **{next_relative}**. [Run now] button → POST /maintain, then re-fetch status.
2. **Consolidation activity** (24h):
   - Query: count of observations in `memory_observations` where the most recent evidence row has `source_kind = 'consolidation'` AND `created_at > now() - 24h`. If consolidation logs land elsewhere, adjust accordingly.
   - UI: "**N** consolidations in the last 24h."
3. **Pipelines** (read-only status):
   - Embedding queue (uses existing `GET /api/memory/embeddings/status`): "Embedding queue: {queued} queued, {processing} processing, {completed_today} done today, last error: {last_error or 'none'}."
   - Graph extractor (new — small derived metric): count of `memory_observations` where `graph_status IS NULL` (= unprocessed); count where `graph_status LIKE 'fail%'` (= failed). UI: "Graph extractor: {unprocessed} unprocessed observations, {failed} failed extractions."

The graph extractor metric serves a dual purpose: it's the metric that gates Phase 5 (People & Things). When unprocessed = 0 and entities + relations > 0, Phase 5 is ready to greenlight.

### Part D — Dormant code cleanup

Delete from `public/js/features/memory-shell.js`:

- Lines 35–148 (the dormant `MEMORY_SECTION_DEFS`, `MEMORY_FILTERS`, `CATEGORY_LABELS`, `CATEGORIES`, `memoryState`, `resetMemoryState`)
- Lines 537–1980 inclusive of: `handleMemoryShellAction`, `loadEvidenceForObservation`, `getActiveProjectPath`, `getProjectLabel`, `setMemoryArchiveStatus`, `downloadJson`, `chooseArchiveFile`, `buildMemoryModel`, `normalizeMemoryRow`, all the bucket/risk/durability helpers, `getSectionDef`, `getVisibleRows`, `rowMatchesWing`, `getSelectedRow`, `renderCurrentMemoryShell`, `renderMemoryShell`, `buildWings`, `buildRooms`, `renderMemoryNav`, `renderMemoryQueue`, `renderMemoryArchiveAdmin`, `renderMemoryRow`, `renderMemoryEmpty`, `renderMemoryDetail`, `renderAddForm`, `renderEditForm`, `buildMemoryActions`, `countFilters`, `renderEvidenceSection`, `renderEvidenceRow`, `showEvidenceDetailModal`, `showNeighborhoodDrawer`, `renderNeighborhoodEntity`, `showOptimizePreviewModal`
- Lines 2161–2181: the search debounce helper (if Phase 1's search uses a different debouncer)

Delete corresponding orphaned imports from `core/api.js`:
- `updateMemoryApi`, `deleteMemoryApi`, `createMemoryApi`, `optimizeMemoryApi`, `applyOptimizationApi`, `createSkillApi`, `exportMemoryArchiveApi`, `createMemorySqliteBackupApi`, `dryRunMemoryArchiveImportApi`, `applyMemoryArchiveImportApi`, `fetchMemoryEvidenceApi`, `fetchMemoryDrawerApi` *(if no Phase 1-4 consumer uses it)*

**Worker discipline**: before deleting an imported function from `core/api.js`, grep the entire repo for usages. If any non-test caller exists outside `memory-shell.js`, leave the import. If it's `memory-shell.js`-only, delete the export too.

Delete dormant CSS classes from `public/css/panels/memory.css` for any selector that no longer matches the remaining markup (the file has 217 classes; expect ~80 to become unused after the JS cleanup).

### Part E — Tests

`artemis/routes/tests/test_memory_health_endpoints.py` (new file):

1. **`GET /api/memory/maintenance/status` returns last_run_at + next_scheduled_at.** Fixture: insert a maintenance log row. Verify return.
2. **Embedding queue status returns the expected stub shape.** Existing endpoint; just confirm Phase 6 doesn't break it.

`artemis/floating_artemis/tests/test_memory_handoff.py` (new file):

3. **FA seeded with memory context prepends the system message.** Mock localStorage / seeded_context payload; verify the FA chat handler prepends correctly.

No additional frontend tests — Lead does eyes-on smoke for tab + handoff.

---

## Files owned

- EDIT: `public/js/features/memory-shell.js` (voice pass + FA handoff button + Health tab; massive deletion of dormant code)
- EDIT: `public/css/panels/memory.css` (remove unused selectors)
- EDIT: `public/js/core/api.js` (remove unused exports)
- EDIT: `artemis/routes/memory.py` (new `/maintenance/status` GET)
- EDIT: `artemis/memory/repository.py` (helper for maintenance status if needed)
- EDIT: `artemis/floating_artemis/chat.py` (or `floating_artemis/tools/core.py`; Worker picks) — accept seeded memory context
- NEW: `artemis/routes/tests/test_memory_health_endpoints.py`
- NEW: `artemis/floating_artemis/tests/test_memory_handoff.py`

---

## Acceptance criteria

1. **No schema changes.** **Paste.**
2. `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/routes/tests/test_memory_health_endpoints.py artemis/floating_artemis/tests/test_memory_handoff.py -v` — all pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **No regressions from cleanup.** Re-run the full memory test suite: `uv run pytest artemis/memory/tests artemis/routes/tests/test_memory_shell_routes.py artemis/routes/tests/test_memory_curate_endpoints.py` — all pass. **Paste.**
5. **Manual smoke (Lead does this post-merge):**
   - Open Memory page; verify empty states (per scope, per filter) read in Artemis voice.
   - Click "Ask Artemis about this" on any observation; verify FA opens; verify the first response shows Artemis is aware of the seeded memory ("yes, that's the one she picked up from…").
   - Open Health tab; verify all three sections render with non-zero numbers.
   - Click "Run now" under Maintenance; verify maintenance fires and the timestamp updates.
   - **Paste DOM snippets for the Health tab + FA handoff.**
6. `git diff --stat` shows large net deletion from `memory-shell.js`. **Paste.**
7. `git log --oneline -1` on `worker/memory-phase-6-voice-fa-health-cleanup`. **Paste.**

---

## Hard constraints

- **Verify before deleting.** Every removed function in the dormant code block must be confirmed unreferenced via grep across the full repo BEFORE the deletion lands.
- **Cleanup is a separate commit.** Voice + FA + Health is one commit; cleanup is the next. Easier to revert the cleanup if a missed reference surfaces.
- **No new visual languages.** Health tab uses existing card primitives. FA handoff button matches Pin/Confirm row.
- **FA handoff doesn't leak across sessions.** localStorage key is cleared after first turn.
- **Maintenance "Run now" is idempotent.** Posting again while a run is in-flight is a no-op (return 202 Accepted + status link).
- **Local-only git.** Worker commits on `worker/memory-phase-6-voice-fa-health-cleanup`; terminal-Lead merges after Lead approves AND completes the full-suite regression check (acceptance #4).
