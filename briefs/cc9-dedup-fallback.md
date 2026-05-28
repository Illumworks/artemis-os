# CC9 — Dedup Fallback for Null-District Signals (federal_funding 6× duplication)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc9-dedup-fallback`
**Browser smoke owner:** Lead, post-merge — run pipeline, confirm same grants.gov URL emits once not 6×.
**Report back to me by:** Jon pastes terminal-Lead's relay.
**LOC cap:** ~200 (includes investigation findings).
**Priority:** HIGH — solidity gate (alongside CC8) before SP1 + cron.

---

## Why this exists

CC6 smoke verified `federal_funding` emits real grants.gov data — but **78 signals / 13 distinct headlines = 6× duplication.** The same "Comprehensive Centers—Literacy for Students with Disabilities" grant gets re-emitted across runs (and possibly within a single run). Two likely causes (investigate first, then fix):

1. **`memory_layer.upsert_last_seen` is still stubbed** (per P3 design — Memory-M2 was TODO). So the suppress-stale qualifier rule has no record of "we've seen this signal" and can't suppress repeats. **Confirm in code.**
2. The dedup-signature key is `(district_id, reason_code)` — for federal grants `district_id` is `null`, so the dedup key collapses and matches nothing. **Confirm in qualifier rule + signal_intake.**

Either way, the result is Principle 7 (dedup) silently failing for keyless signals.

---

## Scope

### Part A — Investigate first (~20 LOC of notes in the report)

Before coding, answer these in the report:
- Where exactly is the suppress-stale check applied (`artemis/marketing/qualifier_rule_layer.py` or equivalent)?
- What does the dedup-signature look like today? Is it `(district_id, reason_code)`, `(district_id, reason_code, source_url)`, or something else?
- Is `memory_layer.upsert_last_seen` currently a stub (returns "ok-stub"), or does it actually write to a memory_layer table? Does that table exist?
- Are the 6× dupes happening at intake (multiple writes to `signal_queue` with the same content) or in qualification (each gets qualified separately)?

Paste the answers as Part A of the report.

### Part B — Source-URL-based dedup at intake (the core fix)

In `artemis/tools/signal_queue.py` (the `signal_queue.write` tool from P2), before inserting a new SignalQueue row:
- If `source_url` is non-empty AND a SignalQueue row already exists with the same `source_url` AND `signal_status` NOT IN (`archived`, `rejected_hard_filter`) AND `created_at >= now() - interval '30 days'`:
  - **Skip the write** and return `{"signal_id": <existing_id>, "status": "deduplicated", "duplicate_of": <existing_id>}` so the scout knows.
  - Log INFO with the duplicate detection.
- This is a cheap, deterministic intake-level dedup that catches the federal_funding case (same grants.gov URL across runs).
- This is FALLBACK behavior — district-based dedup (when district is non-null) takes precedence via the qualifier's suppress-stale; this catches the null-district case.

### Part C — Optional Part B alternative if simpler

If investigation shows the suppress-stale rule already keys on `source_url` and the problem is just that `memory_layer.upsert_last_seen` is a no-op stub: implementing the memory_layer write (one small table + the upsert) may be the cleaner single-point fix. Worker decides between (B) intake dedup or (C) memory_layer real implementation based on findings — explain the choice in the report. **One or the other, not both.**

### Part D — Tests
`artemis/tools/tests/test_signal_queue_dedup.py` (or extend existing):
1. Write a federal signal with `source_url='X'`, null district → row lands.
2. Write again with same `source_url='X'` within 30 days → returns `status="deduplicated"`, no new row, original count unchanged.
3. Write with same `source_url='X'` from a **different scout** (different agent_id) → still dedupes (cross-scout, same URL).
4. Write with same `source_url='X'` after the 30-day window → new row written (stale → re-emit allowed).
5. District-based dedup path (non-null district) still works unchanged.
6. Empty/null source_url falls through to the existing path (no false-positive dedup).

---

## Files owned
- EDIT: `artemis/tools/signal_queue.py` (Part B) **OR** memory_layer implementation (Part C — separate file if so).
- NEW or EXTEND: `artemis/tools/tests/test_signal_queue_dedup.py`.

**Do not touch:** the qualifier rule layer unless investigation says that's the right fix point — in which case flag it and explain. Don't change the existing district-based dedup path. Don't touch agents, blueprints, the seed.

---

## Acceptance criteria
1. **Part A investigation findings in the report** (4 questions answered).
2. `ARTEMIS_TEST_DB_URL=... uv run pytest <test file> -v` — all pass. **Paste.**
3. **DB proof:** run a manual smoke — call signal_queue.write twice with same source_url + null district → only 1 row in signal_queue, 2nd call returns `deduplicated`. **Paste the DB query + the 2nd return value.**
4. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cc9-dedup-fallback`. **Paste.**

---

## Hard constraints
- Investigate first, then choose Part B or Part C — explain the choice.
- Don't break district-based dedup for signals that have a district.
- Dedup window is 30 days (conservative — adjust only if investigation reveals a different intended cadence).
- Local-only git. Worker commits on `worker/cc9-dedup-fallback`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC9 — Dedup Fallback report
1. Commit / branch / worktree
2. LOC diff stats
3. Part A — investigation findings (4 answers)
4. Choice: Part B (intake source_url dedup) or Part C (memory_layer real) — and WHY
5. Test pass summary
6. DB proof of dedup working (acceptance #3)
7. check.sh summary
8. Anything surprising
```
