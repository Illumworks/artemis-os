# Memory Phase 5 prerequisite — Graph extractor audit + backfill

**Paste-into:** terminal-Lead → Opus or Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/memory-graph-extractor-audit`
**Browser smoke owner:** Lead, post-merge — verify a fresh consolidation triggers entity extraction; spot-check 3 observations and confirm entities appear in `memory_entities`.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** unknown — investigation first, fix scope depends on findings. **Cap at ~400 LOC OR escalate back to Lead with findings.**
**Priority:** MEDIUM — gates Phase 5 (People & Things tab). Phases 1-4, 6 of the Memory page redesign are independent and proceed in parallel.
**Parent plan:** `briefs/memory-ui-redesign.md`
**Companion audit:** `audits/memory-ux-audit.md` (Appendix A documents the empty-graph finding)

---

## Why this exists

Coverage check on 2026-06-06 against the live `artemis_os` database:

```
entities_total                  | 0
relations_total                 | 0
observations_with_graph_done    | 0
observations_graph_status_null  | 238
observations_graph_failed       | 0
```

The graph extractor has never successfully populated entities or relations for the 238 observations that exist. `graph_status IS NULL` on all 238 (not "tried and failed") means **the extractor was never invoked**, not that it ran and produced nothing.

This blocks Phase 5 of the Memory page redesign (People & Things entity browser): if the tab ships with the current data, it renders an empty grid for every scope and reads as a broken feature. Per the locked decision, Phase 5 is deferred until graph coverage is real.

This brief is a two-step ticket:
1. **Audit** — figure out why `graph_extractor.extract_for_observation` isn't firing on the live consolidation pipeline.
2. **Fix + backfill** — repair the trigger and run a one-time pass over the 238 unprocessed observations.

---

## Scope

### Part A — Audit (investigation, ~no LOC)

Trace the path from `apply_consolidation` (in `artemis/memory/consolidator.py`) through to `extract_for_observation` (in `artemis/memory/graph_extractor.py`).

Open questions to answer with code-reads + targeted greps:

1. **Is `graph_extractor.extract_for_observation` actually called from `consolidator.apply_consolidation`?**
   - Search for callers of `extract_for_observation` across the repo. The intended trigger is `notify_consolidation_complete` → graph_extractor (per the audit's write-paths map).
   - Confirm the wiring is present, not just intended in design docs.

2. **Is the wiring async-fire-and-forget or awaited?**
   - If it's awaited, slow consolidation runs could be timing out before extraction.
   - If it's fire-and-forget (`asyncio.create_task`), look for swallowed exceptions.

3. **Are LLM/SDK credentials configured for the graph extractor's Haiku call?**
   - Per the memory note `project-marketing-pipeline-tool-use-blocker`: SDK keys have been empty in `.env` before, with the symptom being silent zero-output. Verify keys exist for the model the graph extractor uses.

4. **Is the incremental consolidator firing at all?**
   - `apply_consolidation` is the entry point. Search for callers; verify the 25-observation-per-(scope, category) threshold + 120s debounce trigger has actually fired since the database was seeded.
   - Look in app logs for "consolidation" entries.

5. **Is there a config flag disabling the graph extractor?**
   - Search settings/config for `GRAPH_EXTRACTOR_DISABLED`, `MEMORY_GRAPH_ENABLED`, or similar.

**Output of Part A**: a written findings section (paste back in the PR description, ~200 words) covering:
- Where the wiring is or isn't
- Which of the 5 questions has the broken link
- A recommendation for Part B's fix scope

### Part B — Fix the trigger

Whichever of these the audit identifies. Common paths:

**If wiring is missing:** add the `asyncio.create_task(extract_for_observation(...))` call inside `apply_consolidation` after the new observations are committed. Reuse the existing retry-with-backoff helper.

**If wiring is present but swallowed:** add proper error logging at WARNING level. Surface the first failure via `app.logger` so future ops can see it; don't silently re-queue.

**If SDK keys are missing:** flag to Jon. Don't add keys; document where they need to land.

**If consolidator isn't firing:** drop into incremental_consolidator.py and figure out why the 25-obs-per-scope+category threshold isn't being crossed. May indicate that observations are too spread across scopes for the threshold to fire at current scale; could justify a lower threshold for early-stage data (e.g. 5 obs in debug mode, behind a settings flag).

**If a config flag is set:** flip it and document the rationale.

### Part C — Backfill the 238 unprocessed observations

After the trigger is fixed, run a one-time pass:

```python
# artemis/memory/backfill_graph.py (new file)
async def backfill_graph_extraction(session, batch_size=10, dry_run=False):
    """Process all observations with graph_status IS NULL.

    Yields per-batch progress; returns final counts.
    """
```

Invocation:
```bash
uv run python -m artemis.memory.backfill_graph --batch-size 10
uv run python -m artemis.memory.backfill_graph --batch-size 10 --dry-run   # for preview
```

The backfill:
- Selects `memory_observations` where `graph_status IS NULL`, in batches.
- Calls `extract_for_observation` for each.
- Sets `graph_status='done'` (or `'failed:<reason>'`) per row.
- Logs progress every batch ("processed batch K, X entities and Y relations so far").
- Tolerates partial failure: a failed batch logs and continues; the entire run doesn't abort.
- Respects the retry-with-backoff helper for transient LLM failures.

### Part D — Tests

`artemis/memory/tests/test_graph_extractor_wiring.py` (new):

1. **`apply_consolidation` triggers `extract_for_observation` for new observations.** Mock the extractor; call apply_consolidation with proposals; verify the extractor was scheduled.
2. **Backfill skips observations already `graph_status='done'`.** Fixture: 2 done + 3 null. Verify only 3 are processed.
3. **Backfill marks `graph_status='failed:<reason>'` on extraction error.** Fixture: 1 obs; mock extractor to raise. Verify the row's graph_status is updated and the run continues.

`artemis/memory/tests/test_backfill_graph.py` (new):

4. **Dry-run mode does not mutate `graph_status`.** Fixture: 5 null obs. Run with `dry_run=True`. Verify all 5 still `IS NULL` afterward.

---

## Files owned

- EDIT: `artemis/memory/consolidator.py` (if wiring fix needed)
- EDIT: `artemis/memory/incremental_consolidator.py` (if threshold/debounce adjustments needed)
- EDIT: `artemis/memory/graph_extractor.py` (if error logging or backoff fix needed)
- NEW: `artemis/memory/backfill_graph.py`
- NEW: `artemis/memory/tests/test_graph_extractor_wiring.py`
- NEW: `artemis/memory/tests/test_backfill_graph.py`

---

## Acceptance criteria

1. **Audit findings written.** Part A's findings (which of the 5 questions has the broken link) pasted into the PR description. **Paste.**
2. **Fix lands.** Whichever fix the audit identified is in the diff. **Paste a diff snippet of the key change.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/memory/tests/test_graph_extractor_wiring.py artemis/memory/tests/test_backfill_graph.py -v` — all pass. **Paste.**
5. **Backfill run on the live DB.** Lead executes:
   ```bash
   uv run python -m artemis.memory.backfill_graph --batch-size 10 --dry-run
   uv run python -m artemis.memory.backfill_graph --batch-size 10
   ```
   - Dry run reports expected count (238).
   - Real run completes (allow up to 30 min depending on LLM throughput).
   - Post-run, the coverage query returns: `entities_total > 0`, `relations_total >= 0`, `observations_with_graph_done >= 200`. **Paste post-run numbers.**
6. **Spot-check 3 observations:**
   - Pick 3 obs at random; query their evidence + the entities now linked to them; verify the entities are plausible (e.g. an observation mentioning "Houston ISD" has a `Houston ISD` entity).
   - **Paste the 3 spot-checks.**
7. `git diff --stat` + `git log --oneline -1` on `worker/memory-graph-extractor-audit`. **Paste.**

---

## Hard constraints

- **Audit before fix.** Don't blindly add a wiring call if the audit reveals the issue was an empty SDK key. Investigation determines the fix.
- **No new dependencies.** Per CLAUDE.md, no dep added that's <7 days old. Backfill uses existing async patterns + existing Anthropic SDK.
- **Backfill is idempotent.** Re-running after success processes only newly-NULL rows. Re-running after partial failure picks up where it left off via `graph_status` filtering.
- **No schema changes.** `graph_status` column already exists.
- **LLM cost ceiling.** If backfill estimate exceeds $5 in token spend at current rates, pause and escalate to Jon before running. Estimate by sampling: process 10 obs in dry-run mode, project cost-per-obs * 238.
- **Lossless.** No DELETE on existing entities; if a re-extraction produces different entities, upsert (existing `upsert_entity` semantics handle this).
- **Local-only git.** Worker commits on `worker/memory-graph-extractor-audit`; terminal-Lead merges after Lead approves AND the live backfill completes successfully.

---

## After this brief lands

Phase 5 (People & Things tab) is greenlit. The original Phase 5 plan from `briefs/memory-ui-redesign.md` becomes implementable:
- Reuses the dormant entity-drawer code in `memory-shell.js` (lines 1941–2079 at time of audit — note these get deleted by Phase 6 cleanup if it lands first, so Phase 5 may need to re-port from git history).
- No new backend needed (entity/relation APIs exist already).
- ~200 LOC frontend, low risk.

Write a follow-up brief `briefs/memory-phase-5-people-and-things.md` once this prerequisite is satisfied.
