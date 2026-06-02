# OP1 — Automations Registry Port from Node

**Owner:** Sonnet Worker (NOT Codex — semantic decisions on approval-resume + scheduler integration)
**Branch:** `worker/op1-automations-port`
**LOC budget:** ~550 (full-diff insertions; cap at ~650 with headroom)
**STOP CONDITION:** if you reach 550 insertions, STOP and ping Lead. Do not exceed.
**Brief author:** Lead (Opus 4.7)
**Depends on:** existing Agents + Workflows + Approvals slices (all merged). APScheduler pattern (meeting scheduler + token refresh scheduler already in `artemis/main.py` lifespan).
**Grounded in:** `audits/operations-gap-report-v2.md` §1 (Automations), Node reference at `/Users/artemis/Desktop/Artemis/claudeck-artemis/server/routes/automations.js`, frontend caller in `public/js/features/operations-shell.js`.

## Why this brief exists

Automations is the biggest remaining Operations gap. The frontend page exists, the temporary Workflows bridge in `home.js` exists, but **the `/api/automations*` route family is entirely unmounted on Python**. Calls fail; the operations shell renders "Could not load automations." The Workflows bridge is a UX stopgap, not a port.

OP1 ports Node's automations registry to Python: tables, models, schemas, CRUD + run + run-history routes, approval-resume side effects, archive-not-delete semantics, latest-run embedding on list. After OP1: the Operations Automations page loads, lists, creates, runs, archives. Same shape as the Workflows surface already has.

## Scope

### In scope

1. **Tables** (Alembic migration):
   - `automations` — `id` (UUID), `name`, `description`, `status` (`active`/`paused`/`archived`), `trigger_type` (`manual`/`scheduled`/`webhook`), `schedule_config` (JSONB — cron string + timezone), `target_type` (`agent`/`workflow`/`chain`/`dag`), `target_id` (UUID — FK by application, not DB, since target_type varies), `model`, `provider`, `fallback_provider`, `fallback_model`, `approval_policy` (JSONB — `{"required": bool, "approver_role": "any"|"owner"}`), `output_config` (JSONB), `metadata` (JSONB), `owner_user_id`, `created_at`, `updated_at`, `archived_at` (nullable).
   - `automation_runs` — `id`, `automation_id` FK, `status` (`queued`/`running`/`awaiting_approval`/`succeeded`/`failed`/`cancelled`), `trigger` (`manual`/`scheduled`/`webhook`), `triggered_by` (user email or `scheduler`), `started_at`, `completed_at`, `target_run_id` (UUID — references the underlying agent_run / workflow_run / etc. that this automation kicked off), `error_message`, `metadata` (JSONB), `created_at`.
   - Indexes: `automations(status, owner_user_id)`, `automation_runs(automation_id, created_at DESC)`.

2. **SQLAlchemy models** in `artemis/automations/models.py`.

3. **Pydantic schemas** in `artemis/automations/schemas.py`:
   - `AutomationCreate`, `AutomationUpdate`, `AutomationRead` (with `latest_run` embedded — single LEFT JOIN at read time, not a separate fetch)
   - `AutomationRunRead`
   - `RunRequest` (for manual trigger)

4. **Repository** in `artemis/automations/repository.py` — async functions for CRUD, list, archive (sets `archived_at` + status `archived`, never deletes), latest-run lookup, run history list.

5. **Routes** in `artemis/automations/routes.py`, mounted in `main.py`:
   - `GET /api/automations` and `GET /api/automations/` (no-slash compat per J10 invariant) — list with latest_run embedded; filter by status, owner
   - `POST /api/automations/` — create
   - `GET /api/automations/{id}` — detail (with latest run)
   - `PATCH /api/automations/{id}` — update fields (status, schedule, target, policy)
   - `DELETE /api/automations/{id}` — soft delete (sets archived status; behavior matches Node)
   - `POST /api/automations/{id}/run` — manual trigger; honors approval_policy (returns `awaiting_approval` run if policy requires it)
   - `GET /api/automations/{id}/runs` — run history (cursor-paginated by created_at DESC, page size 30)
   - `POST /api/automation-runs/{run_id}/cancel` — cancel an in-flight run
   - `POST /api/automation-runs/{run_id}/resume` — resume an `awaiting_approval` run (called by the approval system on approve)

6. **Approval-resume side effect.** When `approval_policy.required = true` and the approver approves, the existing approvals service must call back into automations to resume the held run. Wire this in `artemis/marketing/routes/approvals.py` (or wherever the approval-decision handler lives) — on approve, find the linked automation_run, call the same internal "dispatch to target" function the manual run uses. **Do NOT** add a new HTTP self-call; this is in-process.

7. **Headless dispatch.** When an automation fires, it instantiates an agent_run / workflow_run / chain_run / dag_run on the target. Reuse the existing execution functions in `artemis/routes/builders/execution.py` — don't reimplement. The automation_run's `target_run_id` is the resulting underlying run's id; clients can drill from automation_run → target_run for full lineage.

8. **APScheduler integration** — `artemis/automations/scheduler.py`:
   - `start_automation_scheduler()` / `stop_automation_scheduler()`
   - On start: read all `status = active AND trigger_type = scheduled` automations, register a cron job per row using `schedule_config.cron` + `schedule_config.timezone`.
   - On automation update (status flip, schedule change): re-register the job (deregister old, add new). Use the existing patch hook — don't add a separate webhook.
   - On status flip to `paused` or `archived`: deregister the job.
   - Wired into `main.py` lifespan after token_refresh and meeting schedulers.

9. **Tests** in `artemis/automations/tests/`:
   - CRUD round-trip.
   - List embeds latest_run correctly (no run → null; latest run by created_at DESC).
   - Archive is soft delete; archived automations excluded from default list, included with `?status=archived`.
   - Manual run with no approval policy → dispatches immediately, target_run_id populated.
   - Manual run with approval policy → status = `awaiting_approval`, no target run created yet.
   - Resume from approval → dispatches, target_run_id populated.
   - Cancel an in-flight run → status = `cancelled`, target run cancellation propagates if supported.
   - Scheduler registers/deregisters jobs on status changes (use APScheduler test mode).

### Out of scope

- UI work. The Operations Automations page already exists and calls `listAutomationsApi()` etc.; once routes mount, the page loads. Visual polish is a later brief.
- The home.js Workflows bridge can stay as a fallback until OP1 lands; remove it in a follow-up after Jon confirms Automations renders.
- Webhook trigger type. v1 ships `manual` + `scheduled`. Webhook stub exists in the enum but no handler.
- Cross-workspace / multi-tenant. Single-operator.
- Migration of any Node-era automation rows to Python. There are none in production yet.

## Invariants (structural)

1. **Soft delete only.** `DELETE` route flips `status = archived` + sets `archived_at`. Row never leaves the table. Mirrors Node behavior.
2. **Approval-required run never dispatches without approval.** Test enforces: a `RunRequest` against an automation with `approval_policy.required = true` creates an `awaiting_approval` run, asserts `target_run_id IS NULL`.
3. **Scheduler is idempotent on start.** Calling `start_automation_scheduler()` twice does not duplicate jobs. Mirror meeting scheduler's pattern.
4. **Target lookup is FK-by-application.** No DB-level FK on `target_id` because target_type varies. Application validates at create + update time: target must exist in the appropriate table (`agents` / `workflows` / `agent_chains` / `agent_dags`).
5. **Latest-run embedding uses a LEFT JOIN, not N+1.** List endpoint must be a single query with LATERAL JOIN or equivalent. Test for query count.

## Files expected

- `alembic/versions/<rev>_op1_automations.py` — tables. ~80 LOC.
- `artemis/automations/__init__.py` — empty. ~1 LOC.
- `artemis/automations/models.py` — SQLAlchemy. ~80 LOC.
- `artemis/automations/schemas.py` — Pydantic. ~80 LOC.
- `artemis/automations/repository.py` — async CRUD + list with embedded latest_run. ~110 LOC.
- `artemis/automations/routes.py` — 9 endpoints. ~120 LOC.
- `artemis/automations/scheduler.py` — APScheduler wrapping. ~50 LOC.
- `artemis/automations/tests/test_automations_crud.py` — ~50 LOC.
- `artemis/automations/tests/test_automations_run.py` — manual + approval-resume + cancel. ~50 LOC.
- `artemis/automations/tests/test_automations_scheduler.py` — registration. ~30 LOC.
- `artemis/main.py` — lifespan hook. ~5 LOC delta.
- `artemis/marketing/routes/approvals.py` (or appropriate) — approval-resume callback. ~15 LOC delta.

Total: ~670 LOC. Slightly over the 550 target; if you're under 650 ship it. If approaching 700, stop and ping Lead.

## Test plan

Eight scenarios above. Plus:
- `./scripts/check.sh` does not regress (note pre-existing failures).
- Existing approval tests must still pass — the approval-resume callback adds behavior, must not change existing semantics for non-automation approvals.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (`f083ab4`).
- dotenv `override=False` (`7ad1598`).
- No `git push`.
- `pwd && git branch --show-current` before every state-changing Bash call.
- `git diff --stat` for LOC self-reporting.
- **STOP at 550 LOC.** Three consecutive overruns is a process problem.

## What "done" looks like

1. All 9 routes mounted + responding 200/201/204 on happy paths.
2. List embeds latest_run in a single query (assert via query count test).
3. Manual run dispatches headlessly to the target's existing execution function.
4. Approval-resume callback fires when an approval flips to approved + the approved approval has an automation_run association.
5. Scheduler registers active scheduled automations on app start; deregisters on archive.
6. Soft delete preserves the row.
7. All tests pass.
8. Operations Automations page (`public/js/features/operations-shell.js`) loads + lists + creates + runs an automation end-to-end against a stub target (test agent or test workflow).

## Report Worker submits

1. `git diff --stat` output.
2. 9 route paths + HTTP methods (paste).
3. Test pass count.
4. Any divergence from Node behavior — flag for Lead before merge.
5. Branch + worktree path.

---

**Lead notes (not for Worker):**
- This is the biggest pending Operations gap. After OP1: Operations is mostly whole except Memory HTTP routes (separate brief) and Skills lifecycle polish (smaller brief).
- The approval-resume side effect is the only non-trivial design call. If the existing approvals service doesn't have a clean callback hook, flag — we might need OP1a to refactor approvals first.
- APScheduler is now hosting 4 schedulers in lifespan (meeting, token_refresh, scout when M5b lands, automation here). All follow the same start/stop pattern. Keep it consistent.
