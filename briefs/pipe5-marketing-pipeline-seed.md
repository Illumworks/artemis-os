# PIPE5 — Marketing Pipeline Seed JSON

**Owner:** Codex (paste-ready, mechanical translation)
**Branch:** `codex/pipe5-marketing-pipeline-seed`
**LOC budget:** ~250 (full-diff insertions; cap at 300)
**STOP CONDITION:** if you reach 250 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE1 merged (pipelines table + Pydantic schema for PipelineNode/PipelineEdge available).
**Grounded in:** `docs/marketing-ops-v1/PIPELINE.md` (the conceptual flow), the 16 seeded marketing agents on lead, M4 qualifier rule layer, M3 state machine.

## Why this brief exists

PIPE1 ships the pipeline data model and JSON storage. PIPE5 translates the marketing flow from `marketing-ops-v1/PIPELINE.md` into a concrete `Pipeline` row — the first real-world Pipeline, validating that the architecture handles a multi-node multi-trigger orchestration. After PIPE5: the marketing pipeline is queryable via `GET /api/pipelines`, viewable in the Pipelines list, and ready to be wired to execution (PIPE4) and visualized (PIPE2).

This is **pure data translation** — no logic. Copy the flow into Pipeline JSON shape, register as a seed loader matching the M5 pattern, expose a one-shot CLI.

## Scope

### In scope

1. **`artemis/pipelines/seeds/marketing_pipeline.py`** — idempotent seed loader. Pattern mirrors `artemis/marketing/seeds/marketing_agents.py` exactly (look at it for shape).
   - Pipeline slug: `marketing.main` (system-owned, `owner_user_id = NULL`)
   - Re-runnable via `INSERT ... ON CONFLICT (id) DO UPDATE` — preserves owner_user_id (none in this case), created_at; updates nodes, edges, trigger_config, description.

2. **`scripts/seed_marketing_pipeline.py`** — CLI wrapper, ~20 LOC, mirrors `scripts/seed_marketing_agents.py`. Operator runs explicitly after PIPE1 deploy.

3. **The actual pipeline JSON** — nodes + edges representing the marketing flow:

   ### Nodes (in this order, with positions for future PIPE2 canvas):

   ```
   trigger_scheduled  ←  fires every 4 hours
       ↓
   parallel scout fan-out (9 agent_invocation nodes):
   - marketing.scout.starbridge_researcher
   - marketing.scout.regional_news
   - marketing.scout.linkedin_observer
   - marketing.scout.legislative
   - marketing.scout.federal_funding
   - marketing.scout.state_doe
   - marketing.scout.procurement
   - marketing.scout.board_minutes
   - marketing.scout.leadership_transition
       ↓ (signals land in signal_queue per agent — fan-in is implicit at the queue)
   agent_invocation: marketing.qualifier.cross_reference
       ↓
   agent_invocation: marketing.qualifier.brief_composer
       ↓
   human_gate: gate_1_signals_inbox  (Josh/Angela approval)
       ↓
   agent_invocation: marketing.content.brief_assembler
       ↓
   agent_invocation: marketing.content.asset_selector
       ↓
   agent_invocation: marketing.content.writing_studio_adapter
       ↓ (drafts submitted to Writing Studio; Gate 2 lives in approval queue)
   ```

   Total: 1 trigger + 9 scouts + 2 qualifier + 1 human gate + 3 content = **16 nodes**.

   The two qualifier agents NOT in the linear flow (`marketing.qualifier.ruleset_manager` and `marketing.qualifier.ruleset_compiler`) are NOT in this pipeline — they're operator-invoked utilities, not on the signal flow. Leave them out.

4. **Edges** — for now, simple sequential edges from trigger → 9 scouts (one edge each, fan-out from trigger), then a fan-in junction where the qualifier picks up signals from the queue rather than from explicit edges. Use this convention: every node has at least one incoming edge except the trigger node; scouts have edges from the trigger; qualifier has edges from each scout (9 incoming edges, representing the fan-in even though the runtime fan-in is via signal_queue table).

   Conservative edge count: **~25 edges** (9 trigger→scouts + 9 scouts→qualifier + 1 qualifier→brief_composer + 1 brief_composer→gate_1 + 1 gate_1→brief_assembler + 1 brief_assembler→asset_selector + 1 asset_selector→ws_adapter + 1 ws_adapter→terminal).

5. **Trigger config** at the top-level `trigger_config` JSONB:
   ```json
   {
     "type": "scheduled",
     "cron": "0 */4 * * *",
     "timezone": "America/Chicago"
   }
   ```

6. **Node config shapes** — minimal per type:
   - **`trigger_scheduled`:** mirrors `trigger_config` top-level (redundant by design — the trigger node is the canonical entry point; the top-level field is the index Pipelines list uses for badge rendering).
   - **`agent_invocation`:** `{"agent_id": "marketing.scout.starbridge_researcher", "mode": "scheduled"}` — references the seeded agent row by `agent_id` slug.
   - **`human_gate`:** `{"approval_kind": "signal_brief", "approvers": ["josh@amiralearning.com", "angela@amiralearning.com"], "timeout_hours": 72}`. The execution engine (PIPE4) will create approvals rows when this node fires; for now the config just sits.
   - **`position` field** on every node: hand-place positions so PIPE2 canvas renders sensibly. Trigger top-center; scouts in a horizontal row below; qualifier centered below scouts; gate centered below qualifier; content team in a vertical or horizontal column below gate. Don't agonize — close-enough is fine; PIPE2 users can drag to reorganize.

7. **Tests** in `artemis/pipelines/tests/test_marketing_pipeline_seed.py`:
   - Seed loads idempotently; row count after two runs is 1.
   - Node count = 16. Slug list matches expected agent_ids exactly.
   - Edge count > 0. No edge references a missing node_id.
   - Trigger config has cron string + timezone.
   - The pipeline row's `name` is `"Marketing Pipeline"` and `description` is a short one-liner (write a sensible one).

### Out of scope

- Any execution behavior. PIPE5 just writes the row. PIPE4 wires execution.
- Visual layout polish. Hand-place positions roughly; PIPE2 users tune via canvas.
- Removing the orchestrator agents not in the flow (ruleset_manager, ruleset_compiler) — they stay in the agents table as operator utilities.
- Multiple pipeline variants (e.g., one per state). One canonical marketing pipeline for v1.

## Invariants

1. **All referenced agent_ids exist in the agents table.** Pre-flight check in the seed loader: query agents table for each agent_id; if any missing, raise with a clear error pointing at M5 seed.
2. **Idempotent.** Re-running seeds is safe; doesn't duplicate. Pattern matches M5.
3. **JSON validates against PIPE1's Pydantic schema.** If PIPE1's `PipelineNode` requires fields you didn't populate, fix the seed (or flag the schema gap).
4. **No `agent_id` outside `marketing.*` namespace.** This is the marketing pipeline; mixing in other domains is a future Pipeline.

## Files expected

| File | LOC |
|---|---|
| `artemis/pipelines/seeds/__init__.py` (may not exist; create if missing) | ~1 |
| `artemis/pipelines/seeds/marketing_pipeline.py` (loader + JSON) | ~180 |
| `scripts/seed_marketing_pipeline.py` (CLI wrapper) | ~20 |
| `artemis/pipelines/tests/test_marketing_pipeline_seed.py` | ~50 |

**Total: ~250 LOC.** Mostly data (the JSON for 16 nodes + ~25 edges). Cap at 300 if you find you need a touch more validation.

## Test plan

1. Loader runs, creates 1 pipeline row.
2. Re-running loader is idempotent — still 1 row, updated_at advances, created_at unchanged.
3. Node count = 16. Iterate, assert types and agent_ids match expected.
4. Edge count = 21–25 (some flexibility on fan-in modeling).
5. `validate_marketing_pipeline()` helper passes — every edge source/target points to a real node ID.
6. `agent_invocation` nodes reference agent_ids that exist in the agents table (query, don't hard-code).
7. The pipeline appears in `GET /api/pipelines` after seeding, with `latest_run = null` and `status = "active"`.

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` must pass within exempt set before declaring done
- `git switch lead/j6a-granola-integration` after commit

## What "done" looks like

1. One pipeline row in the DB with `name = "Marketing Pipeline"`, 16 nodes, valid edges, scheduled trigger.
2. `GET /api/pipelines` shows it in the list.
3. Seed is idempotent.
4. All tests pass.
5. `./scripts/check.sh` passes within exempt set.
6. Full-diff insertions ≤ 300.

## Report Codex submits

1. `git diff --stat` output.
2. The 16 node IDs + types (paste).
3. Edge count + a sample of 3 edges (paste).
4. Trigger config (paste).
5. Test pass count.
6. Confirmation `./scripts/check.sh` passes within exempt set.
7. Confirmation main worktree is back on lead.
8. Branch.

---

**Lead notes (not for Codex):**
- This brief is unusual: it's almost entirely data with very little logic. The 16 nodes are predetermined (M5 already seeded the agent rows). The edges are predetermined by the marketing flow. Codex's job is faithful translation.
- The fan-in modeling (9 scouts → 1 qualifier) is conceptually a fan-in but runtime-wise the qualifier polls signal_queue. We model it as 9 explicit edges in the JSON because PIPE2's canvas needs to render the connections; the executor (PIPE4) will read those edges as "wait for ALL upstream nodes to complete OR poll signal_queue, whichever the qualifier prefers." That choice is PIPE4's, not PIPE5's.
- After PIPE5 lands, the marketing pipeline is visible in the Pipelines list (Operations → Pipelines). User can click it, see the JSON, run it (records intent only until PIPE4). Real visualization in PIPE2; real execution in PIPE4.
