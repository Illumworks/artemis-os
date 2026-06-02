# PIPE4 — Execution Engine

**Owner:** Sonnet Worker (significant runtime architecture work)
**Branch:** `worker/pipe4-execution-engine`
**LOC budget:** ~1500 (honest overrun OK to ~1900 — execution engine for 5 node types + Slack delivery + resume semantics + audit log is genuinely big)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE1 + PIPE2 + PIPE3 + PIPE5 + Connectors + Reason codes injection + Agent Card blueprint + AI Panel — all merged. Pipeline data model is complete; this brief makes Run button do something real.

## Why this brief exists

After PIPE4, clicking Run on a Pipeline actually walks the graph, invokes nodes per their type, persists state for resumability, delivers approval asks via Slack DM, and reports back via the run history surface.

For the Marketing Pipeline specifically: scheduled trigger fires (or manual Run) → 9 scouts query their sources → signals land in `signal_queue` → Cross-Reference Agent qualifies + routes → Brief Composer produces brief → Gate 1 sends Slack DMs to Josh/Angela → on approval, content team fires → 4 deliverables draft → Gate 2 sends second Slack DM → on approval, content released. End-to-end flow operates.

## Scope

### Core executor module

**`artemis/pipelines/executor.py`** — the engine. ~500 LOC.

Class: `PipelineExecutor`
- Constructor takes a `pipeline_run_id` (created by /run endpoint)
- Async `run()` method:
  1. Loads pipeline definition + current node_states from `pipeline_runs.node_states` JSONB
  2. Topological sort over nodes (using edges to determine order)
  3. For each node in order: dispatch to appropriate node executor (see below)
  4. Update node_states after each node completes/fails/suspends
  5. Handle suspension semantics: if any node returns `suspend` (typically human_gate), persist state and exit; resume picks up where left off
  6. On full completion: mark pipeline_run as `succeeded`; on failure: `failed` with error_message

### Per-node executors

**`artemis/pipelines/node_executors/`** package — one module per type. ~600 LOC total.

#### `agent_executor.py` (~150 LOC)
- Handles `agent_invocation` nodes
- Loads target agent by `agent_id` from config
- Builds system message: agent's `system_prompt` + injected `reason_codes_emitted` list (per the injection scaffold from earlier brief)
- Resolves credentials via `artemis/connectors/resolver.py` for any tool namespaces the agent uses
- Invokes via existing `artemis/routes/builders/execution.py::run_agent()` (reuse don't reinvent)
- Captures result; writes to node_states
- Enforces cost cap from config — if cumulative cost exceeds cap, mark `partial_complete` and halt
- Cost accounting: every LLM call's cost adds to `pipeline_runs.cost_usd`

#### `human_gate_executor.py` (~180 LOC)
- Handles `human_gate` nodes
- Creates row in existing `approvals` table:
  - `kind`: from config.approval_kind
  - `subject_id`: this pipeline_run_id + node_id (composite key)
  - `approvers`: from config.approvers
  - `timeout_at`: now + config.timeout_hours hours
- **Slack DM delivery:** for each approver email, look up Slack user via existing J1/J8 integration (`artemis/integrations/slack/` already has user lookup by email). Send DM containing:
  - Pipeline name + node label
  - Brief summary (signal evidence or draft preview)
  - Two buttons: Approve / Reject (Slack interactive message format)
  - Or fallback: deep-link to in-app Approval Queue if Slack DM fails
- Returns `suspend` — executor stops processing further nodes; pipeline_run stays at `awaiting_approval` status
- Sets a timer (via APScheduler) for `timeout_at` to fire `on_timeout` behavior
- Logs Slack DM delivery status in `node_states[node_id].delivery_log`

#### `conditional_executor.py` (~80 LOC)
- Handles `conditional` nodes
- Evaluates `config.predicate` against context: signal data, prior node outputs, environment
- Supports operators: `equals`, `not_equals`, `greater_than`, `less_than`, `contains`, `in_list`
- Power-user fallback: `config.jsonlogic` field with full JSONLogic expression evaluator
- Returns `true_branch` or `false_branch` — executor uses this to pick which outgoing edge to follow

#### `sub_pipeline_executor.py` (~100 LOC)
- Handles `sub_pipeline` nodes
- Modes:
  - `inline`: instantiate a child `PipelineExecutor` with the sub-pipeline_run_id, await its completion before continuing
  - `async_fire_and_forget`: create the sub_pipeline_run, dispatch via APScheduler, mark this node complete immediately
- Detects cycles (pipeline referencing itself directly or transitively) — bail with clear error

#### `trigger_executor.py` (~40 LOC)
- Handles `trigger_scheduled`, `trigger_manual`, `trigger_webhook`, `trigger_event` nodes
- For all trigger types: just records "trigger fired at <timestamp> via <mode>" in node_states
- This is the entry point of the graph; no upstream nodes
- Manual mode: invoked by /run endpoint with `mode=manual`
- Scheduled mode: invoked by APScheduler when cron fires
- Webhook mode: invoked by `/api/pipelines/{id}/trigger/webhook` (route to be added)
- Event mode: future; v1 stubs

### Routes

Add to `artemis/pipelines/routes.py`:

- **`POST /api/pipeline-runs/{run_id}/resume`** — called when an approval flips. Body: `{node_id, decision: "approved"|"rejected", actor: email}`. Updates node_states for the gate node, then continues execution from the next node (or branches on rejection). ~80 LOC.

- **`POST /api/pipeline-runs/{run_id}/cancel`** — manual cancellation. Already exists from PIPE1; verify it sets status to `cancelled` and stops timers. Minor edit if needed. ~10 LOC.

- **Scheduled-trigger registration:** when a Pipeline is set to `active` and contains a `trigger_scheduled` node, APScheduler registers a cron job. When it fires, the job invokes the executor with `mode=scheduled`. New module: `artemis/pipelines/scheduler.py`. ~80 LOC.

### Audit log for timeout-based decisions

When `on_timeout: auto_approve` or `auto_reject` fires (per `human_gate` config), write an audit entry that clearly indicates the human did NOT explicitly act:

```python
# In artemis/pipelines/audit.py (or add to existing campaign_state_transitions audit pattern)
await audit_log({
    "kind": "gate_auto_decision",
    "pipeline_run_id": run_id,
    "node_id": gate_node_id,
    "decision": "auto_approved",  # or auto_rejected
    "reason": "timeout_after_72h",
    "configured_approvers": [...],
    "elapsed_seconds": ...,
})
```

Surface in run history: each timeout-based decision shows with a clear "Auto-approved (timeout)" label, not as if Josh manually approved.

### Escalation flow

When `on_timeout: escalate`:
1. Look up `config.escalation_to` (set per-gate at creation time per Jon's call)
2. Send Slack DM to escalation approvers with: "Original approver didn't respond in 72h. Please review: <brief preview> + Approve/Reject buttons"
3. Reset the timer for another timeout window (configurable; default same as original)
4. If escalation also times out: fall back to whatever the `on_timeout` of the ORIGINAL config would have been if NOT escalate (i.e., auto_approve or auto_reject as documented in the second-level config)

For v1 simplicity: if escalation also times out, just mark gate as `escalation_timeout` and surface to UI as needs-manual-resolution. Real second-level cascading is future.

### Resume semantics

When `/resume` is called for a suspended pipeline_run:
1. Validate the run is in `awaiting_approval` status and the node_id matches a current gate
2. Update node_states[gate_node_id] with: decision, actor, decided_at
3. Cancel the scheduled timeout job
4. Re-instantiate `PipelineExecutor` with this run_id
5. The executor sees that this gate is now resolved (not suspended), picks the appropriate outgoing edge:
   - `decision = approved` → follow approved-branch edge (typically the default outgoing edge)
   - `decision = rejected` → no further nodes; mark run as `failed` with reason="gate_rejected"
6. Execution continues from where it left off

### Fan-in semantics (for gate_2 with multiple upstream nodes)

When a gate has multiple incoming edges (like the marketing pipeline's gate_2 with 4 deliverables fanning in):
1. Gate doesn't fire until ALL upstream nodes have status=succeeded in node_states
2. The first 3 deliverables complete → executor checks gate_2's upstream count, sees not-all-done, parks gate_2 as `waiting_for_upstream`
3. When the 4th deliverable completes → executor re-checks gate_2's upstream, sees all-done, fires the gate (creates approval row, sends DM, suspends)

Config flag on the gate: `wait_for_all_upstream: true` (default) vs `wait_for_any_upstream: false` (gate fires as soon as any upstream completes). Use `true` for marketing.

### Slack DM message format

Reuse existing `artemis/integrations/slack/messages.py` (if it exists; create if not). Format:

```
Subject: Marketing Pipeline — Gate 1 Approval

You have a signal awaiting review:

District: Pinellas County Schools (FL)
Reason: POLICY_LIT_MANDATE
Evidence: "The District seeks a Reading Intervention solution that provides measurable student growth..."
Urgency: HOT

[Approve] [Reject] [View in Artemis →]
```

Slack interactive components (Approve/Reject buttons) fire webhooks to `/api/slack/approval-callback` which then calls `/api/pipeline-runs/{run_id}/resume`. New webhook route. ~50 LOC.

If Slack delivery fails (rate limit, user not found, etc.): fall back to in-app Approval Queue (existing M3/M7 surface). Log the failure in node_states.

### Cost cap enforcement

Per-node cost caps (from PIPE3's agent_invocation form):
- Before each LLM call: compute estimated cost
- If `accumulated_cost + estimated > config.cost_cap_usd`: STOP, mark this node as `partial_complete` with reason="cost_cap_exceeded"
- Propagate: pipeline_run status becomes `partial_complete` with error_message naming the offending node
- Surface in run history with clear "Stopped: $X cap reached" label

### State persistence

`pipeline_runs.node_states` JSONB shape per node:

```json
{
  "<node_id>": {
    "status": "pending" | "running" | "succeeded" | "failed" | "suspended" | "partial_complete" | "waiting_for_upstream",
    "started_at": "2026-05-22T...",
    "ended_at": "2026-05-22T..." (null while running),
    "output_summary": "Emitted 3 signals" (or whatever the node produced),
    "error": null,
    "cost_usd": 0.05,
    "delivery_log": [...] (for human_gate Slack DM status),
    "decision": "approved" | "rejected" | "auto_approved" | "auto_rejected" (for human_gate after resolution)
  }
}
```

### Tests

- Unit: each node executor in isolation with mock dependencies. ~80 LOC.
- Integration: walk a 3-node toy pipeline end-to-end (trigger → agent → trigger result). ~60 LOC.
- Integration: walk a 4-node pipeline with conditional branch (true and false paths). ~60 LOC.
- Integration: human gate suspend + resume. Mock approval table; mock Slack DM send. Resume executes downstream nodes. ~80 LOC.
- Integration: fan-in semantics (3 upstream nodes; gate waits for all). ~50 LOC.
- Integration: cost cap enforcement (mock cost accumulation; verify halt). ~50 LOC.
- Integration: timeout-based auto_approve fires audit entry + continues execution. ~50 LOC.
- Integration: escalation flow (timeout → escalate → second DM → resume). ~60 LOC.
- Smoke: marketing pipeline runs end-to-end with mocked agents/scouts/slack. Asserts all 21 nodes traverse correctly. ~100 LOC.

### Out of scope

- Real Slack DM sending (uses existing Slack client; just calls into it). Slack client wiring is OP1 / J8 territory.
- Parallel node execution (multiple nodes running concurrently). v1 is sequential per-branch; fan-out is logical (multiple branches exist) but executor processes them in topological order one at a time. v2 can parallelize.
- Retry semantics on transient failures. v1 fails on first error. Retries are a future polish.
- Webhook trigger handler routes. v1 stubs the executor path; the route itself can be a separate brief.
- Real-time run progress streaming to UI. v1 returns updates via polling pipeline_run status.
- Run cancellation mid-flight while a node is executing. v1 cancel only catches between-node boundaries.
- Run versioning / replay. Each /run creates a new pipeline_run row; history is just chronological.

## Invariants

1. **node_states persisted after EVERY node transition.** Crash recovery: on app restart, scan pipeline_runs in `running` or `suspended` status; resume execution where each left off.
2. **Cost cap stops execution immediately, no further LLM calls.** Verified by test.
3. **Auto-decision audit entries always include `reason: "timeout_after_<N>h"`.** Distinguishable from human decisions in run history.
4. **Slack delivery failure does NOT block pipeline.** Falls back to in-app Approval Queue with clear "Slack delivery failed" indicator.
5. **No new dependencies for cron parsing** — APScheduler is already loaded.
6. **Reuse existing execution.py for agent invocation** — don't reimplement agent runtime.
7. **Reuse existing connectors/resolver.py for credentials** — don't bypass.
8. **Reuse existing approvals table** — don't create a parallel approvals system.
9. **Connector resolution failures fail the pipeline_run with clear error** — "Starbridge connector required but not linked" instead of cryptic credential errors.

## Files expected

| File | LOC |
|---|---|
| `artemis/pipelines/executor.py` (new) | ~500 |
| `artemis/pipelines/node_executors/__init__.py` (new) | ~10 |
| `artemis/pipelines/node_executors/agent_executor.py` (new) | ~150 |
| `artemis/pipelines/node_executors/human_gate_executor.py` (new) | ~180 |
| `artemis/pipelines/node_executors/conditional_executor.py` (new) | ~80 |
| `artemis/pipelines/node_executors/sub_pipeline_executor.py` (new) | ~100 |
| `artemis/pipelines/node_executors/trigger_executor.py` (new) | ~40 |
| `artemis/pipelines/scheduler.py` (new) | ~80 |
| `artemis/pipelines/audit.py` (new or extend existing audit pattern) | ~60 |
| `artemis/pipelines/routes.py` (resume + cancel + webhook routes) | ~120 delta |
| `artemis/integrations/slack/messages.py` (pipeline approval DM format) | ~80 |
| `artemis/main.py` (lifespan hook for scheduler) | ~5 delta |
| Tests | ~600 |

**Total: ~2000 LOC.** Cap 2300 — biggest brief of the wave. Execution engine + Slack delivery + audit + tests is genuinely large; honest budget. If you're heading past 2300 with a structural reason (e.g., approval webhook signature verification needed more code), STOP and ping Lead.

## Test plan

Per the tests list above. Key scenarios:

1. **Trigger fires:** /run endpoint creates pipeline_run, executor walks graph, first agent invocation node executes (or mocks), success state persists.
2. **Human gate suspend:** gate creates approval row, sends mock DM, marks node as suspended, executor exits.
3. **Resume after approval:** /resume endpoint updates node_states, re-instantiates executor, downstream node executes.
4. **Resume after rejection:** /resume with decision=rejected → pipeline_run marked failed with reason="gate_rejected".
5. **Auto-approve on timeout:** timer fires, audit entry written with reason=timeout, executor resumes as if approved.
6. **Escalate on timeout:** timer fires, second DM sent to escalation_to, second timer started.
7. **Fan-in wait:** 4 nodes converge to gate_2; gate doesn't fire until all 4 succeeded.
8. **Cost cap:** mock LLM costs; verify halt + partial_complete status.
9. **Connector missing:** unlink Starbridge connector from scout; verify pipeline_run fails with clear "Starbridge connector required" error, not cryptic auth failure.
10. **End-to-end marketing pipeline:** mock all 9 scouts + qualifier + brief composer + content team agents; pipeline traverses all 21 nodes; gate_1 + gate_2 suspend + resume correctly.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- node --check on any modified JS (mostly backend brief; minimal JS — possibly none — but check what you touch)
- Run M5 + marketing pipeline seeders before integration tests; document if they need to be in conftest setup
- Browser smoke for the run history UI updates IF you touch UI; otherwise N/A
- LOC budget: stop at 2300 if you're heading materially over with a structural reason; ping Lead with the specific cause

## What "done" looks like

1. Run button on Marketing Pipeline → executor walks graph → mocked agents respond → mocked Slack DMs sent → pipeline_run progresses through all 21 nodes → completes (or suspends at gate)
2. Human gate suspend → mock Slack DM sent → in-app Approval Queue surfaces the approval → click approve → resume route fires → next node executes
3. Cost cap test: mock LLM cost exceeds cap → executor halts → pipeline_run marked partial_complete
4. Timeout auto-approve: mock timer fires → audit entry written → execution continues
5. Escalation: timeout → escalation_to receives second DM → resume on second approval
6. All tests pass
7. `./scripts/check.sh` passes within exempt set (allowing the known j5b Jira test exempt + any new test-isolation issues if surfaced — but no new regressions)

## Report Worker submits

1. `git diff --stat` output.
2. Architecture overview: executor's `run()` flow at high level (paste pseudocode if it helps).
3. Per-node-executor signatures (paste — Lead reviews the contracts).
4. Sample run history JSON for a completed marketing pipeline (paste — Lead verifies the shape).
5. Slack DM message format example (paste the actual rendered message).
6. Test pass count.
7. Browser smoke (if UI touched): screenshots of run history showing the marketing pipeline run.
8. Branch + worktree path.

---

**Lead notes (not for Worker):**
- This is the keystone landing. After PIPE4, the entire orchestration tier is functional end-to-end. The marketing pipeline actually runs.
- The Slack DM format above is a starting point. Worker has UX latitude on the exact Slack message structure — but it MUST include: pipeline name, node label, brief context, approve/reject affordances. Sub-points (urgency badge, evidence quote, etc.) can be Worker's call as long as approver can make the decision from the DM without opening Artemis.
- After PIPE4 lands + Jon walks a real run, expect 2-4 follow-up patches for things only visible at runtime (latency, error message clarity, run history UI gaps). Plan for it.
- PIPE6 (legacy Workflows/Automations sunset + auto-migrate) is next after PIPE4 stabilizes.
