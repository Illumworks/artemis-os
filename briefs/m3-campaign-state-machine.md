# M3 — Campaign State Machine

**Owner:** Sonnet Worker (isolated worktree)
**Branch:** `worker/m3-campaign-state-machine`
**LOC budget:** ~400 (full-diff insertions)
**Brief author:** Lead (Opus 4.7)
**Grounded in:** `docs/marketing-ops-v1/PIPELINE.md`, `docs/marketing-ops-v1/schemas/{signal,signal-brief,campaign-workspace,writing-studio-draft}.md`, `docs/marketing-ops-v1/gates/{gate-1,gate-2}.md`

## Why this brief exists

The marketing pipeline is a multi-stage state machine that spans 5 tables (`signal_queue`, `signal_briefs`, `campaign_workspaces`, `campaign_deliverables`, `writing_studio_drafts`). Each table has its own lifecycle column today, but transitions are **enforced ad-hoc in routes** — there is no central definition of legal transitions, no guard against illegal jumps, and no auditable transition log. The team has already lost the Node-era 15-state machine once in migration; we are not losing it again.

M3 ships a single source of truth for state and transitions, with structural enforcement (not "be careful") and a transition audit log. Subsequent briefs (M4 qualifier rules, M5 agent seed) depend on this; they will call `transition()` and not write status columns directly.

## Scope

### In scope

1. **State enum module** — one Python module declaring every state across all 5 lifecycles as `enum.StrEnum` subclasses. One enum per lifecycle. No magic strings elsewhere in the codebase after this brief.
2. **Transition map** — a `LEGAL_TRANSITIONS: dict[Enum, set[Enum]]` per lifecycle. Single declarative source. Importable by routes, services, and tests.
3. **`transition()` service** — `artemis/marketing/state_machine.py::transition(session, entity_type, entity_id, to_state, reason=None, actor=None) -> Entity`. Validates the transition is legal, updates the row, writes one `campaign_state_transitions` audit row, commits. Raises `IllegalTransition` on violation. Single function. Other modules call this; they do NOT mutate `.status` directly.
4. **Audit table** — `campaign_state_transitions`: `id`, `entity_type` (signal / brief / workspace / deliverable / draft), `entity_id`, `from_state`, `to_state`, `actor` (email or agent name), `reason` (nullable), `transitioned_at`. Append-only. Indexed on `(entity_type, entity_id, transitioned_at)`.
5. **Alembic migration** — creates `campaign_state_transitions` table. Adds CHECK constraints on the 5 status columns enforcing the enum values. Backfills any existing rows whose status is now invalid into a `legacy_` prefix value rather than failing.
6. **Repository hook** — every existing route that writes to a status column today gets re-routed through `transition()`. This is a mechanical sweep. Failures here mean a route is jumping states illegally — flag in the report; do NOT silently allow.
7. **Tests** — for each lifecycle: every legal transition succeeds; every illegal transition raises `IllegalTransition`; audit row written on success; audit row NOT written on failure; concurrent transition attempts serialize correctly (FOR UPDATE).

### Out of scope

- The qualifier rule layer that decides *when* to transition (that's M4).
- Agent prompts that drive transitions (M5).
- UI surfaces for the audit log (later).
- Backfilling historical transitions that happened before this brief (we have no transition history to backfill — start fresh).

## The five lifecycles

Verbatim from team docs. Each is its own enum.

### `SignalState`
```
pending_qualification   ← scout writes
qualified               ← passed Phase 1+2+3
rejected_hard_filter    ← failed Phase 1 (terminal)
suppressed_stale        ← dedupe collapsed it (terminal)
```
Legal: `pending_qualification → {qualified, rejected_hard_filter, suppressed_stale}`. Terminal states have no outgoing edges.

### `BriefState`
```
created                 ← composer wrote it
in_inbox                ← surfaced to Gate 1
approved                ← Josh/Angela said yes (terminal — workspace created)
rejected                ← Josh/Angela said no (terminal — feedback training signal)
snoozed                 ← re-surface in N days
asked                   ← chat thread open, waiting on response
```
Legal: `created → in_inbox`; `in_inbox → {approved, rejected, snoozed, asked}`; `snoozed → in_inbox`; `asked → in_inbox`.

### `WorkspaceState`
```
pending_content              ← created on Gate 1 approval
in_content_preparation       ← 5.1 / 5.2 / 5.3 running
sent_to_writing_studio       ← 5.3 POSTed all drafts (terminal for our scope)
content_preparation_failed   ← error during 5.x (terminal — needs manual recovery)
```
Legal: `pending_content → in_content_preparation`; `in_content_preparation → {sent_to_writing_studio, content_preparation_failed}`.

### `DeliverableState` (one per email/social/long_form/landing_page)
```
queued
generating              ← Writing Studio is drafting
draft_ready             ← draft submitted to approval drawer
approved                ← Gate 2 approved (terminal)
revised                 ← Gate 2 sent back; re-enters generating
rejected                ← Gate 2 killed it (terminal)
```
Legal: `queued → generating`; `generating → {draft_ready, content_preparation_failed_local}` (use a generic-error state name — Worker decides); `draft_ready → {approved, revised, rejected}`; `revised → generating`.

### `DraftState` (Writing Studio internal — already exists in current schema)
Keep current values. Out of scope for change in M3 unless inconsistent with the above. If they overlap, deduplicate against `DeliverableState`.

## Invariants (structural — not "be careful")

1. **No direct status writes.** After M3, grep for `.status =` in `artemis/marketing/**/*.py` must return only `transition()` callsites + test fixtures. Anything else is a bug.
2. **Audit row is atomic with state change.** Same transaction. If audit insert fails, state change rolls back.
3. **`from_state` in audit row matches current DB value at time of transition** (read inside the same transaction with `FOR UPDATE`). Prevents the "two writers race, second wins, audit lies" failure.
4. **Terminal states have empty outgoing set.** Enforced in the `LEGAL_TRANSITIONS` declaration, not at runtime — a single source of truth.
5. **Unknown `to_state` raises immediately**, before any DB call. Cheap fail.

## Files expected (rough — Worker adjusts)

- `artemis/marketing/state_machine.py` — enums, transition map, `transition()`, `IllegalTransition`, helpers. ~180 LOC.
- `artemis/marketing/models/state_transition.py` — SQLAlchemy model for `campaign_state_transitions`. ~30 LOC.
- `alembic/versions/<rev>_campaign_state_machine.py` — table + CHECK constraints. ~60 LOC.
- `artemis/marketing/routes/{campaign_ops,signal_queue,approvals,campaign_deliverables,writing_studio}.py` — replace direct `.status =` writes with `transition()` calls. **Surgical edits only.** ~40 LOC delta total.
- `artemis/marketing/tests/test_state_machine.py` — exhaustive tests. ~100 LOC.

## Test plan

1. **Legal transitions per lifecycle.** Parametrize across every edge in `LEGAL_TRANSITIONS`. Assert: state changed, audit row inserted, `from_state` correct.
2. **Illegal transition raises.** For each lifecycle, pick two non-adjacent states; assert `IllegalTransition`. Assert audit row NOT inserted. Assert state column unchanged.
3. **Terminal state is sticky.** Try to transition out of a terminal state; assert `IllegalTransition`.
4. **Unknown state raises before DB call.** Pass a string; assert immediate `IllegalTransition` with no DB hit (mock the session, assert no `flush`).
5. **Concurrent transition test.** Two sessions try to transition the same entity simultaneously; assert one wins, one raises `IllegalTransition` because `from_state` no longer matches. Requires `FOR UPDATE` in the read.
6. **Idempotent migration.** Run alembic up, down, up; assert no errors and audit table is empty after final up.

## Invariants Worker must NOT regress

- **conftest hard-fail on non-test DB.** Commit `f083ab4`. Do not weaken.
- **dotenv `override=False`.** Commit `7ad1598`. Do not flip.
- **No `git push`.** Local-only repo. Workers commit on their branch and stop.
- **`pwd && git branch --show-current`** before every state-changing Bash call. CWD trap is real.
- **`git diff --stat` for LOC self-reporting.** No estimating. No excluding "boilerplate."

## What "done" looks like

1. All 5 lifecycle enums declared in one module.
2. `LEGAL_TRANSITIONS` declarative map covers every legal edge.
3. `transition()` is the only path to mutate state; grep proves it.
4. Audit table exists, populated on every transition, never on failure.
5. CHECK constraints on the 5 status columns prevent garbage values at DB layer.
6. Tests cover the 6 scenarios above and pass.
7. `./scripts/check.sh` does not regress (pre-existing unrelated failures are fine — note them).
8. Full-diff insertions ≤ 450 (10% headroom over 400 budget). Over budget → stop and ask Lead.

## Report Worker submits

1. `git diff --stat` output.
2. The 5 enums and their values (paste, don't summarize).
3. The `LEGAL_TRANSITIONS` map (paste).
4. Grep result for `.status =` in `artemis/marketing/` (should be only `transition()` internals + fixtures).
5. Test pass count.
6. Any route that was jumping states illegally (i.e. a `.status =` write that doesn't map cleanly to a legal edge) — flag for Lead, do not silently fix.
7. Branch + worktree path.

## Open questions for Lead (NOT for Worker)

None blocking. State definitions are pulled verbatim from team docs. The only design choice — whether `DeliverableState` and `DraftState` are the same enum — is left to the Worker with a fallback rule: if the values overlap entirely, deduplicate; if not, keep both.

---

**Lead notes (not for Worker):**
- This is the substrate. M4 qualifier rules call `transition(signal, qualified)` or `transition(signal, rejected_hard_filter)`. M5 agent seed prompts reference state names by enum, not magic string.
- The 15-state Node-era machine is rolled into the 4 main lifecycles above (5 if you count DraftState separately). The "15" was a count of distinct named states across all entities, not a single machine — confirmed by walking `marketing-ops-v1/` schemas.
- Audit log doubles as the spine for any future analytics ("how long does the average signal sit in `in_inbox` before approval?"). Don't over-design for analytics now; the table shape is right.
