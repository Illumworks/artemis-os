# M3a — State Machine Route Sweep + Enum Completion + CHECK Tightening

**Owner:** Sonnet Worker (NOT Codex — semantic enum decisions inside) via terminal-Lead
**Branch:** `worker/m3a-state-sweep-and-enum-completion`
**LOC budget:** ~450 (full-diff insertions; cap at ~550 with headroom)
**STOP CONDITION:** if you reach 450 insertions, STOP and ping Lead. Do not exceed without explicit Lead approval. (M1 and M3 both blew their caps; this brief makes the stop rule the first instruction.)
**Brief author:** Lead (Opus 4.7)
**Depends on:** M3 merged (state machine + soft CHECK + transition() service in place).
**Grounded in:** M3 brief at `briefs/m3-campaign-state-machine.md`, M3 Worker's report of 6 legacy-value callsites, mapping survey on `lead/j6a-granola-integration`.

## Why this brief exists

M3 shipped the state machine but did NOT perform the route sweep (its brief item 6). The Worker's correct rationale: existing code writes legacy status values that aren't in M3's StrEnums, so converting them isn't mechanical — it's a semantic mapping decision. The survey reveals M3's enums are also missing **three real-world Workspace states and two Brief states** that production code actively writes today. M3a closes both gaps: extend the enums to cover real usage, then perform the route sweep, then tighten the CHECK constraint that M3 had to soften.

After M3a: every status mutation in `artemis/marketing/` flows through `transition()`. The DB CHECK enforces enum-values-only. The 77 pre-existing tests use the new enum names.

## Scope

### In scope

1. **Extend M3 enums** to cover real-world states:

   **WorkspaceState** — add three states observed in `writing_studio/adapter.py`:
   - `content_in_review` — drafts submitted to Gate 2, awaiting human review
   - `all_content_approved` — every deliverable approved at Gate 2; campaign ready to ship
   - `revision_needed` — at least one deliverable rejected/sent back at Gate 2

   Updated legal transitions:
   - `sent_to_writing_studio → content_in_review` (was terminal in M3; no longer terminal)
   - `content_in_review → {all_content_approved, revision_needed}`
   - `revision_needed → in_content_preparation` (re-prep)
   - `all_content_approved` is the new terminal.

   **BriefState** — add two:
   - `monitoring` — user said "watch this signal area but don't act now"
   - `changes_requested` — user requested edits to the brief before approval

   Updated transitions:
   - `in_inbox → monitoring`
   - `monitoring → in_inbox` (when an updated brief surfaces in the area)
   - `in_inbox → changes_requested`
   - `changes_requested → in_inbox` (after the originating agent reworks)

2. **Mapping table** — single canonical source for legacy-value → enum conversion, declared once in code as a module-level constant so the sweep can `grep` for the table and verify completeness:

   ```python
   LEGACY_STATUS_MAP: dict[tuple[str, str], Enum] = {
       # (entity_type, legacy_value) → new_enum_member
       ("deliverable", "ready_for_review"): DeliverableState.DRAFT_READY,
       ("deliverable", "rejected_at_gate_2"): DeliverableState.REJECTED,
       ("deliverable", "review_pending"): DeliverableState.DRAFT_READY,
       ("workspace", "content_in_progress"): WorkspaceState.IN_CONTENT_PREPARATION,
       ("workspace", "content_in_review"): WorkspaceState.CONTENT_IN_REVIEW,  # new state, see #1
       ("workspace", "all_content_approved"): WorkspaceState.ALL_CONTENT_APPROVED,  # new
       ("workspace", "revision_needed"): WorkspaceState.REVISION_NEEDED,  # new
       ("workspace", "created"): WorkspaceState.PENDING_CONTENT,
       ("brief", "monitoring"): BriefState.MONITORING,  # new
       ("brief", "changes_requested"): BriefState.CHANGES_REQUESTED,  # new
       # signal_queue values that look misattributed — see #3
       ("signal", "approved"): SignalState.QUALIFIED,
       ("signal", "rejected"): SignalState.REJECTED_HARD_FILTER,
       ("signal", "snoozed"): SignalState.PENDING_QUALIFICATION,  # snooze re-queues
       ("signal", "archived"): SignalState.SUPPRESSED_STALE,
   }
   ```

   These mappings are Lead-reviewed BEFORE Worker codes — see the "Mappings to confirm with Lead" section below. Worker pings Lead with the final table before starting the sweep.

3. **Route sweep** — replace every direct status write with `transition()`. Specific callsites (from M3 Worker's report):
   - `artemis/marketing/writing_studio/external.py:115` — `_drafts[external_id].status = "ready_for_review"` → `transition(draft, DeliverableState.DRAFT_READY)`
   - `artemis/marketing/writing_studio/adapter.py:31,33,76-79,95-102,160-161` — six map lookups + conditional writes. Replace map values with enum members; replace conditional writes with `transition()` calls.
   - `artemis/marketing/writing_studio/invoke.py:315,330` — `deliverable.status = "ready_for_review"` → `transition()`.
   - `artemis/marketing/writing_studio/sync.py:355-359` — map of WS status → internal status; rewrite map to use enum members.
   - `artemis/marketing/routes/campaign_ops.py:51-52` — `"monitor": "monitoring"`, `"request_changes": "changes_requested"` → enum members (now that they exist).
   - `artemis/marketing/routes/campaign_deliverables.py:116` — `deliverable.status = "review_pending"` → `transition(deliverable, DeliverableState.DRAFT_READY)`.
   - `artemis/marketing/routes/signal_queue.py` — any `signal_status=` parameter writes → `transition()` using SignalState mappings.
   - `artemis/marketing/repository.py:179-180` — `workspace_state="created"` → `WorkspaceState.PENDING_CONTENT`.

4. **Test migration** — update the 77 pre-existing tests M3 Worker found, that create rows with legacy values. Either:
   - Replace the literal string with the enum's `.value` (cheaper diff)
   - OR replace with the enum member where the test reads back the value
   Use whichever yields the smaller diff per test file. Tests that round-trip through `transition()` should use the enum member; tests that just check a column value can use `.value`.

5. **Tighten CHECK constraints** — new Alembic migration that drops M3's permissive CHECK and replaces with enum-values-only on all 5 columns. This migration runs AFTER the test migration so the test suite passes against the tight constraint. Migration up should fail with a clear error if any row in the table has a value outside the enum (alert the operator that production data needs a one-off cleanup).

6. **Grep proof** — final test asserts `grep -rn "\.status\s*=" artemis/marketing/` returns ZERO lines outside `state_machine.py` internals and test fixtures. Add this as a CI test (`tests/test_no_direct_status_writes.py`).

### Out of scope

- Production data backfill (existing rows with legacy values). The CHECK tightening migration will fail loudly if such rows exist; that becomes its own one-off operator script. Document the procedure in the migration's docstring.
- Refactoring `transition()` itself.
- New state machine features. Just sweep + enum completion + CHECK tighten.
- UI work to expose the new states (e.g., "monitoring" badge). UI follows after.

## Mappings to confirm with Lead BEFORE coding

Worker MUST ping Lead with these three before starting:

1. **`review_pending` on `campaign_deliverables` → `DeliverableState.DRAFT_READY`?**
   - Read: a Codex/Node holdover state meaning "submitted, awaiting review." Equivalent to DRAFT_READY in our DeliverableState. **Lead's call: yes, map to DRAFT_READY.** Confirm in chat before coding.

2. **`signal_queue` legacy values — are these actually Signal states or Brief states?**
   - Code uses `"approved"`, `"rejected"`, `"snoozed"`, `"archived"` on `signal_queue.status` writes. These read like Brief states. Two possibilities:
     - (a) The legacy code conflated signal lifecycle and brief lifecycle in one column.
     - (b) These writes are actually updating a brief-shaped record stored in the signal queue table.
   - Worker investigates by reading the callsite, then asks Lead. **Lead expects: (a) — they were brief-state writes against a poorly-named column. Map per the table above, but verify the callsite intent first.**

3. **`SignalState.SNOOZED` doesn't exist in M3.**
   - The legacy `"snoozed"` value has no clean target in SignalState. Options: add it (new state), map to `PENDING_QUALIFICATION` (re-queue), or treat as a brief state (snooze = on the brief side, not the signal side).
   - **Lead's call: snooze belongs to BriefState (already there). Legacy `signal.status = "snoozed"` writes are mis-attributed; they should be writes against the brief. The mapping in the table re-routes these via `PENDING_QUALIFICATION` as a fallback, but the right long-term fix is moving the write to the brief column. Worker flags this for a follow-up brief; does NOT fix the misattribution in M3a.**

## Invariants (structural)

1. **Zero direct `.status = ` writes** in `artemis/marketing/` after this brief. Enforced by the new CI test.
2. **CHECK constraint allows enum values only.** New migration enforces this; old soft CHECK is dropped.
3. **`LEGACY_STATUS_MAP` is the single source of truth** for legacy-to-enum conversion. If a future sweep finds another legacy value, it gets added to the map; the conversion happens via the map.
4. **`transition()` is called everywhere status changes happen** — including from inside `writing_studio/adapter.py`'s state computation. The adapter computes the new state, then calls `transition()` with that target.
5. **Tests use enum members (not string literals).** The new CI grep can be extended to catch string-literal status values in tests too, but that's optional in M3a — focus on production code first.

## Files expected

- `artemis/marketing/state_machine.py` — extend three enums + transitions + `LEGACY_STATUS_MAP`. ~60 LOC delta.
- `artemis/marketing/writing_studio/external.py` — sweep. ~5 LOC delta.
- `artemis/marketing/writing_studio/adapter.py` — sweep. ~30 LOC delta.
- `artemis/marketing/writing_studio/invoke.py` — sweep. ~10 LOC delta.
- `artemis/marketing/writing_studio/sync.py` — sweep. ~15 LOC delta.
- `artemis/marketing/routes/campaign_ops.py` — sweep. ~10 LOC delta.
- `artemis/marketing/routes/campaign_deliverables.py` — sweep. ~5 LOC delta.
- `artemis/marketing/routes/signal_queue.py` — sweep. ~15 LOC delta.
- `artemis/marketing/repository.py` — sweep. ~5 LOC delta.
- 77 test files — migration. ~100 LOC delta total (most tests change 1-2 lines).
- `alembic/versions/<rev>_m3a_tighten_state_check.py` — drop soft CHECK, add strict CHECK. ~60 LOC.
- `tests/test_no_direct_status_writes.py` — CI grep test. ~20 LOC.

Total: ~335 LOC. Should fit comfortably under the 450 cap. **If your tally exceeds 450, STOP and ping Lead. Do not exceed.**

## Test plan

1. **All 77 pre-existing tests pass** with new enum names.
2. **New CI test** (`test_no_direct_status_writes.py`) passes — grep returns zero matches in `artemis/marketing/` outside `state_machine.py`.
3. **Tightened CHECK migration up/down clean** three times.
4. **Tightened CHECK rejects a legacy value insert.** Test: try to insert a row with `status = "ready_for_review"` directly; assert CHECK violation.
5. **Three new WorkspaceState transitions work end-to-end.** Test: `sent_to_writing_studio → content_in_review → all_content_approved` round-trip via `transition()`, audit rows written.
6. **Two new BriefState transitions work.** Test: `in_inbox → monitoring → in_inbox` and `in_inbox → changes_requested → in_inbox`.
7. **LEGACY_STATUS_MAP completeness.** Test: for every key in the map, `transition(target_entity, value)` succeeds against a fixture in that entity's starting state.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (`f083ab4`).
- dotenv `override=False` (`7ad1598`).
- No `git push`.
- `pwd && git branch --show-current` before every state-changing Bash call.
- `git diff --stat` for LOC self-reporting.
- **STOP AT 450 INSERTIONS. Do not exceed without Lead approval.** Re-read this if you hit 400.

## What "done" looks like

1. Three new WorkspaceState members + two new BriefState members.
2. All 6 documented callsite sweeps complete.
3. `LEGACY_STATUS_MAP` declared and referenced from sweep sites.
4. 77 pre-existing tests migrated; suite passes.
5. CHECK constraint tightened in a new Alembic migration.
6. CI grep test passes; zero direct `.status =` writes remain.
7. New state transitions tested end-to-end with audit rows.
8. `./scripts/check.sh` does not regress.
9. Full-diff insertions ≤ 450.

## Report Worker submits

1. `git diff --stat` output.
2. `LEGACY_STATUS_MAP` final form (paste — Lead double-checks).
3. The three new WorkspaceState members + two new BriefState members + their transitions (paste).
4. CI grep test output (paste — should be zero matches).
5. Test pass count.
6. Any callsite where the sweep was ambiguous and Lead's call differed from the brief's mapping table — flag.
7. Any test file that needed more than 5 line changes — flag (might indicate the test was actually exercising the state machine, not just using a legacy value).
8. Branch + worktree path.

---

**Lead notes (not for Worker):**
- The three flags from M3 (LOC overrun, deferred sweep, soft CHECK) are all addressed here.
- The LOC stop-rule is reiterated in **bold**, at the top, and again in invariants. Third strike on this means a process-level intervention.
- After M3a: production code uses enums everywhere, CHECK enforces structurally, audit log is complete. The state machine is fully wired in. M4 can rely on it.
- The "snoozed signal" misattribution flagged in mapping #3 becomes a tiny follow-up brief — out of scope here to keep M3a focused.
