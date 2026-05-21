# PIPE1 — Pipeline Data Model + CRUD + JSON Editor

**Owner:** Sonnet Worker (NOT Codex — schema design + JSONB shape decisions inside)
**Branch:** `worker/pipe1-pipeline-data-model`
**LOC budget:** ~1300 (full-diff insertions; cap at ~1500 with headroom)
**STOP CONDITION:** if you reach 1300 insertions, STOP and ping Lead via SendMessage. Do not exceed without explicit approval.
**Brief author:** Lead (Opus 4.7)
**Depends on:** all current merges on lead — OP1, M4, M5b, M3a, mem-m2, M7, M5, M3, M1.
**Grounded in:** D6 + D6.1 in `docs/ARTEMIS-OS-MASTER-PLAN.md`, existing automations (OP1) + workflows + chains + dags tables for shape reference.

## Why this brief exists

D6 locks Pipeline as the unified orchestration primitive replacing Workflows, Chains, DAGs, and Automations. PIPE1 ships the foundation: a `pipelines` table with `nodes` and `edges` JSONB columns, full CRUD routes, and a minimal list + JSON-edit UI so the marketing pipeline (PIPE5) can be seeded as JSON immediately. Visual canvas comes in PIPE2; execution engine in PIPE4. PIPE1 makes pipelines storable, readable, editable, and run-triggerable — the substrate everything else builds on.

## Scope

### In scope

1. **New `pipelines` table** (Alembic migration):
   - `id` (UUID PK)
   - `name` (TEXT NOT NULL)
   - `description` (TEXT)
   - `nodes` (JSONB NOT NULL DEFAULT '[]') — list of node objects (shape below)
   - `edges` (JSONB NOT NULL DEFAULT '[]') — list of edge objects (shape below)
   - `trigger_config` (JSONB) — optional trigger spec (manual / scheduled / webhook / event)
   - `status` (TEXT NOT NULL DEFAULT 'active') — `active` / `paused` / `archived`. CHECK constraint enforces enum.
   - `owner_user_id` (BIGINT, nullable for system pipelines like marketing)
   - `metadata` (JSONB) — free-form for UI hints, tags, etc.
   - `created_at`, `updated_at` (TIMESTAMPTZ)
   - Indexes: `(status, owner_user_id)`, GIN on `nodes`, GIN on `edges` (for future query-by-node-type).

2. **New `pipeline_runs` table**:
   - `id` (UUID PK)
   - `pipeline_id` FK → pipelines
   - `status` (TEXT NOT NULL) — `queued` / `running` / `awaiting_approval` / `succeeded` / `failed` / `cancelled`
   - `trigger` (TEXT) — `manual` / `scheduled` / `webhook` / `event`
   - `triggered_by` (TEXT) — user email or `scheduler` / `system`
   - `node_states` (JSONB DEFAULT '{}') — per-node execution state map for resumability (node_id → `{status, started_at, ended_at, output_summary, error}`)
   - `started_at`, `completed_at` (TIMESTAMPTZ)
   - `error_message` (TEXT)
   - `metadata` (JSONB)
   - Indexes: `(pipeline_id, started_at DESC)`.

3. **Node JSONB shape** (declare as a TypedDict in `schemas.py` for clarity; runtime is JSONB):
   ```python
   class PipelineNode(TypedDict):
       id: str                          # uuid or human slug, must be unique within the pipeline
       type: Literal[
           "agent_invocation",
           "skill_call",
           "trigger_manual",
           "trigger_scheduled",
           "trigger_webhook",
           "trigger_event",
           "human_gate",
           "conditional",
           "sub_pipeline",
       ]
       label: str                       # display name
       config: dict[str, Any]           # type-specific config (which agent, what cron, etc.)
       position: dict[str, float]       # {x, y} for canvas placement (used by PIPE2; ignored by PIPE1)
   ```

4. **Edge JSONB shape**:
   ```python
   class PipelineEdge(TypedDict):
       id: str
       source_node_id: str
       target_node_id: str
       condition: dict[str, Any] | None  # optional gate condition for conditional branches
       data_shape: dict[str, Any] | None # optional output→input mapping hint (for PIPE2 inspection)
   ```

5. **Pydantic schemas** in `artemis/pipelines/schemas.py`:
   - `PipelineCreate`, `PipelineUpdate`, `PipelineRead` (with `latest_run` embedded via LATERAL JOIN like OP1)
   - `PipelineRunRead`
   - `PipelineRunRequest` — for manual trigger
   - Validation: every edge's `source_node_id` + `target_node_id` MUST exist in `nodes` (Pydantic validator).

6. **Repository** `artemis/pipelines/repository.py` — async CRUD + list-with-latest-run + run-history.

7. **Routes** `artemis/pipelines/routes.py`, mounted in `main.py`:
   - `GET /api/pipelines` and `GET /api/pipelines/` (no-slash compat) — list with `latest_run` embedded; filter by status/owner/has_trigger
   - `POST /api/pipelines/` — create
   - `GET /api/pipelines/{id}` — detail with latest run
   - `PATCH /api/pipelines/{id}` — update (full nodes/edges replace OR partial via JSON merge — Worker picks; default to full replace for simplicity)
   - `DELETE /api/pipelines/{id}` — soft delete (status → archived)
   - `POST /api/pipelines/{id}/enable` — flip status to active
   - `POST /api/pipelines/{id}/disable` — flip status to paused
   - `POST /api/pipelines/{id}/run` — manual trigger; creates pipeline_runs row with status=`queued`. **Does NOT execute** in PIPE1 (execution engine is PIPE4). Just records the intent.
   - `GET /api/pipelines/{id}/runs` — cursor-paginated history
   - `POST /api/pipeline-runs/{run_id}/cancel` — mark `cancelled`
   - **No scheduler integration in PIPE1.** Trigger nodes are stored but no APScheduler wiring. PIPE4 wires execution.

8. **Frontend list view** `public/js/features/pipelines.js` + `public/css/features/pipelines.css`:
   - Mount under Operations → Pipelines
   - List cards: name, description (truncated), node count, trigger summary (e.g., "Manual" / "Every 1h" / "Webhook"), latest_run status badge, enable/disable toggle inline, Edit + Run buttons
   - **Compact card for single-node pipelines** (so an "email agent every hour" automation renders as a small row, not a giant card) — detect node count and switch layout.
   - Click Edit → opens detail panel with **raw JSON editor** (textarea + JSON parse on save). This is PIPE1's placeholder; PIPE2 replaces with the visual canvas.
   - Run button → POSTs `/run`; toast "Run queued — execution engine arrives in PIPE4" until PIPE4 ships.
   - Empty state copy: "No pipelines yet. Create one or seed the marketing pipeline via `scripts/seed_marketing_pipeline.py`."
   - Search box (filter by name).
   - Sort: by name / by latest_run timestamp.

9. **Navigation update** — add Pipelines link under OPERATIONS in the left rail (after Skills, before Agents — Worker decides best ordering for the rail). The Automations tab + Workflows tab stay during transition (PIPE6 removes them).

10. **Tests** in `artemis/pipelines/tests/`:
    - `test_pipeline_crud.py` — create, read, update, delete, soft-archive semantics, latest_run embedding, enable/disable toggles.
    - `test_pipeline_validation.py` — invalid edges (source/target not in nodes), invalid node types, status enum CHECK.
    - `test_pipeline_runs.py` — manual /run creates a pipeline_runs row in `queued` status (no execution); cancel transitions to `cancelled`.
    - `test_pipeline_no_slash_compat.py` — both `/api/pipelines` and `/api/pipelines/` return 200.

### Out of scope (deferred to later PIPE briefs)

- Visual canvas (PIPE2).
- Node type config UIs / detail panels (PIPE3).
- Actual execution engine — running a pipeline end-to-end (PIPE4).
- Marketing pipeline seed JSON (PIPE5 — uses PIPE1's schema).
- Legacy migration of Workflows/Chains/DAGs/Automations rows (PIPE6).
- APScheduler integration for scheduled trigger nodes (PIPE4).
- Sub-pipeline call resolution (PIPE4).
- Human Gate UI surface (PIPE4 — node creates an approval; existing Gate 2 panel handles it).
- Pipeline cloning / templating (later).

## Invariants (structural)

1. **Status enum enforced by CHECK constraint** at DB layer (`active`/`paused`/`archived`). No magic strings elsewhere.
2. **Soft delete only.** DELETE flips status to `archived`. Rows never removed.
3. **Latest-run embedding via LATERAL JOIN** (single query, no N+1). Mirror OP1's pattern.
4. **Node ID uniqueness within a pipeline** validated by Pydantic before write.
5. **Edge source/target reference existing node IDs** validated by Pydantic before write.
6. **No execution side effects.** `/run` creates a `pipeline_runs` row but does NOT invoke any agent or scheduler. PIPE4 wires execution.
7. **Compatible with future PIPE2 canvas.** Node `position` field is stored but unused by PIPE1; PIPE2 adds the visual layer without schema changes.

## Files expected (honest estimate post-calibration)

| File | LOC |
|---|---|
| `alembic/versions/<rev>_pipelines.py` | ~80 |
| `artemis/pipelines/__init__.py` | ~5 |
| `artemis/pipelines/models.py` | ~120 |
| `artemis/pipelines/schemas.py` | ~150 |
| `artemis/pipelines/repository.py` | ~180 |
| `artemis/pipelines/routes.py` | ~180 |
| `artemis/main.py` | ~5 delta |
| `public/js/features/pipelines.js` (new) | ~350 |
| `public/css/features/pipelines.css` (new) | ~150 |
| Left-rail HTML wiring (operations-shell.js or wherever rail items live) | ~15 delta |
| `artemis/pipelines/tests/test_pipeline_crud.py` | ~100 |
| `artemis/pipelines/tests/test_pipeline_validation.py` | ~60 |
| `artemis/pipelines/tests/test_pipeline_runs.py` | ~50 |
| `artemis/pipelines/tests/test_pipeline_no_slash_compat.py` | ~30 |

**Total honest estimate: ~1300.** Per the calibrated methodology — schema migrations + parametric tests + Pydantic validators land bigger than naive code-count suggests. If you go over 1300, stop at the first cap-relevant boundary (e.g., before adding more tests) and ping Lead.

## Test plan

1. **CRUD round-trip.** Create with nodes+edges, read back, update name + add a node, delete (soft).
2. **Latest-run embedding.** Create pipeline + manually run once; list endpoint shows latest_run populated. List with no run shows null.
3. **Validation rejects.** Create with edge whose source_node_id doesn't exist → 422. Create with duplicate node IDs → 422. Update status to an unknown value → 422.
4. **Enable/disable toggle.** Sequence active → paused → active works; archived can't go back to active without explicit POST (out-of-scope for PIPE1 to validate; just confirm the route exists).
5. **Run records intent only.** POST /run creates pipeline_runs row, status=`queued`, no agent invocation occurred (mock the executor or assert no exec calls).
6. **No-slash compat.** Both `/api/pipelines` and `/api/pipelines/` return 200 (J10 invariant).
7. **Migration up/down idempotent.** Up, down, up — no errors.
8. **Frontend smoke.** node --check on the new JS file. Open Pipelines page in browser, list empty, click "New" or whatever the empty-state CTA is, JSON editor opens.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (`f083ab4`)
- dotenv `override=False` (`7ad1598`)
- No `git push`
- `pwd && git branch --show-current` before every state-changing Bash call
- `git diff --stat` for LOC self-reporting — full diff, no estimating, no excluding "boilerplate"
- `./scripts/check.sh` must pass within the documented exempt set BEFORE declaring done
- After commit, `git switch lead/j6a-granola-integration` so the main worktree doesn't sit on your branch
- If your work touches public/js/ or public/css/, smoke-test browser bootstrap before reporting — no NEW console errors

## What "done" looks like

1. `pipelines` + `pipeline_runs` tables exist with CHECK constraints.
2. 10 routes mounted, all responding correctly per the table above.
3. List endpoint embeds latest_run via single LATERAL query.
4. Pipelines page renders in browser, empty state visible, JSON editor functions.
5. Compact card renders for one-node pipelines, full card for multi-node.
6. Search + sort work.
7. Tests pass.
8. `./scripts/check.sh` passes within exempt set.
9. Full-diff insertions ≤ 1500.

## Report Worker submits

1. `git diff --stat` output.
2. 10 route paths + HTTP methods (paste).
3. The PipelineNode + PipelineEdge TypedDicts (paste — Lead verifies shape).
4. Migration revision number (should be 0037).
5. Test pass count.
6. Screenshot or description of the empty-state Pipelines page + a hand-created pipeline rendered.
7. Confirmation `./scripts/check.sh` passes within exempt set.
8. Confirmation main worktree is back on lead/j6a-granola-integration.
9. Branch + worktree path.

---

**Lead notes (not for Worker):**
- This is the substrate everything else builds on. Get the JSONB shapes right; future PIPE briefs assume they're stable.
- The JSON editor is intentionally crude. Marketing pipeline (PIPE5) creators interact with it via the seed loader, not by hand-typing JSON. Visual canvas (PIPE2) is the real editing experience.
- After PIPE1 lands: PIPE5 (Codex paste — marketing seed JSON), PIPE2 (Sonnet — visual canvas), PIPE3 (Sonnet — node type configs), PIPE4 (Sonnet — execution engine). PIPE5 + agent-provider-UI + OPS-UI-1 can all run in parallel with PIPE2 since they don't depend on the canvas.
