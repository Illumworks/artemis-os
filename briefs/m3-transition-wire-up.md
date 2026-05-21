# M3 transition() Wire-up — Follow-up after M4/M5b

**Owner:** Sonnet Worker (small mechanical fix)
**Branch:** `worker/m3-transition-wire-up`
**LOC budget:** ~150 (full-diff insertions; cap at 180)
**STOP CONDITION:** if you reach 150 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** M3a + M4 + M5b merged (current lead).
**Grounded in:** M4 and M5b worker reports both flagged M3 `transition()` integration as TODO; need to land it.

## Why this brief exists

M4 and M5b shipped with M3 `transition()` integration deferred:
- **M4** (`artemis/marketing/cross_reference.py`) writes `signal_status` directly in 3 places and added `cross_reference.py` to `tests/test_no_direct_status_writes.py` exclusion list with a `TODO(M3)` comment.
- **M5b** (`artemis/marketing/scout_runner.py`) has a docstring TODO referencing `transition()` not being wired for `SignalState.qualified`.

The substrate is ready (M3 state machine merged, audit table exists, `transition()` service is stable). This brief wires both call sites through `transition()` and removes the exclusion. Mechanical work, low risk.

## Scope

### In scope

1. **`artemis/marketing/cross_reference.py`** — replace the 3 direct `signal_status =` writes with `transition()` calls:
   - Each call uses the appropriate `SignalState` enum member (`qualified`, `rejected_hard_filter`, or `suppressed_stale`).
   - Each call passes a meaningful `reason` (e.g., `"phase1_passed"`, `"phase2_low_fit"`, `"phase1_failed_<filter_id>"`).
   - The `actor` field is `"cross_reference_agent"` (string identifier for system actors; matches the convention M5b uses for `"scout_runner"`).

2. **`artemis/marketing/scout_runner.py`** — resolve the TODO in the runner:
   - After `signal_queue.write()` succeeds, call `transition(session, "signal", signal.id, SignalState.qualified, actor="scout_runner", reason=f"emitted_by_{agent_id}")`.
   - The brief originally said signals start at `pending_qualification` then move to `qualified` after the qualifier runs — verify this is the right semantic. If signals from the scout runner should land as `pending_qualification` (because the qualifier hasn't seen them yet), then DON'T wire the transition here — just delete the TODO. **Worker decides based on the actual signal lifecycle:** if scout_runner writes signals destined for the qualifier, leave them at `pending_qualification` and remove the TODO with a comment explaining. If scout_runner writes signals that bypass qualifier (unlikely but possible), transition to `qualified`.

3. **`tests/test_no_direct_status_writes.py`** — remove `cross_reference.py` from the exclusion list. The grep test should now pass against the swept code.

4. **Tests:**
   - `artemis/marketing/tests/test_cross_reference_transitions.py` (new) — for each of the 3 transition call sites: signal lands in the expected state, audit row written, transition reason populated correctly.
   - Existing M4 + M5b tests must still pass.
   - The `test_no_direct_status_writes` grep test must pass (it's the verification gate).

### Out of scope

- Any other `transition()` integration. M3a already swept the rest of the codebase. M4 + M5b are the only two TODOs.
- Adding new SignalState enum members. The existing 8 (per M3a) cover everything M4 and M5b need.
- Changing `transition()` itself.
- Performance optimization (transition() calls are atomic with the signal write; tracing through performance is later).

## Invariants

1. **`signal_status` is mutated ONLY via `transition()`.** After this brief, the grep test enforces it.
2. **Audit row atomic with state change.** Same transaction. If audit write fails, state change rolls back. (`transition()` already does this; just confirm M4's call sites use the existing transactional pattern, don't bypass it.)
3. **From-state check.** `transition()` validates current state matches expected source; if a signal somehow has an unexpected `signal_status` value, the transition raises `IllegalTransition` rather than silently overwriting. M4's tests should cover the happy path; if a transition raises in production, that's a real signal worth investigating, not a bug to silence.

## Files expected

| File | LOC |
|---|---|
| `artemis/marketing/cross_reference.py` | ~40 delta (3 writes → transition() calls + import) |
| `artemis/marketing/scout_runner.py` | ~10 delta (TODO resolution; possibly just a comment removal) |
| `tests/test_no_direct_status_writes.py` | ~5 delta (remove exclusion) |
| `artemis/marketing/tests/test_cross_reference_transitions.py` (new) | ~80 |

**Total: ~135 LOC.** Mechanical sweep + targeted tests. Cap at 180 if test fixtures need more setup than expected.

## Test plan

1. Each of the 3 transition sites: construct a signal in the expected source state, invoke the path, assert target state + audit row.
2. The grep CI test (`test_no_direct_status_writes`) passes — zero direct `.signal_status =` writes in `artemis/marketing/` outside `state_machine.py` and fixtures.
3. M4's existing 32 qualifier tests still pass (regression check).
4. M5b's existing 6 scout runner tests still pass (regression check).

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit

## What "done" looks like

1. Zero direct `.signal_status =` writes in `artemis/marketing/` outside `state_machine.py` + fixtures.
2. `test_no_direct_status_writes` exclusion list no longer mentions `cross_reference.py`.
3. M4 transition-site tests all pass with audit rows verified.
4. Existing M4 + M5b tests still pass.
5. `check.sh` passes within exempt set.

## Report Worker submits

1. `git diff --stat` output.
2. The 3 transition() call sites in cross_reference.py (paste — Lead spot-checks `to_state` + `reason` semantics).
3. Confirmation that scout_runner's TODO was resolved correctly (paste the comment/code change + brief note on why qualified-or-not).
4. Test pass count.
5. Confirmation grep CI test passes.
6. Branch.

---

**Lead notes (not for Worker):**
- This is post-wave cleanup. The hesitation in M4/M5b was reasonable — Workers were cautious about a substrate they didn't fully test. M3a's CI grep test caught it; this brief closes the loop.
- If during the sweep, Worker notices any OTHER `.signal_status =` write that the exclusion list doesn't cover (i.e., a write that M3a missed), flag it — that's a real M3a gap, not part of this brief, but worth knowing.
