# Marketing Pipeline Figma Reconciliation

**Owner:** Codex (paste-ready data update)
**Branch:** `codex/marketing-pipeline-figma-reconciliation`
**LOC budget:** ~200 (honest overrun OK to ~260)
**Depends on:** PIPE5 marketing pipeline seed merged. M5 agents seeded.

## Why

Jon's Figma boards show the canonical marketing workflow. Comparison to current PIPE5 seed (16 nodes) reveals three real divergences:

1. **Cross-Reference Agent shown as 3 phases in Figma** (Phase 1 hard filters / Phase 2 score / Phase 3 route). We have 1 node. Runtime is 1 agent with internal phases — keep 1 node, just clarify the label.

2. **After Writing Studio Adapter, Figma fans out to 4 deliverable types** (Email / Social / Long-Form / Landing-Page). We have linear flow ending at writing_studio_adapter.

3. **Gate 2 (Approval Drawer) is a pipeline node in Figma.** We have Gate 2 as a separate Approval Queue page; not modeled in the pipeline.

Per Jon's decisions:
- Keep ONE workflow (don't split)
- 4 separate deliverable nodes (not 1 with branching config)
- 1 Gate 2 after all deliverables (not 4 per-deliverable gates)

## Scope

### Marketing pipeline JSON update

Update `artemis/pipelines/seeds/marketing_pipeline.py` to:

1. **Rename Cross-Reference node** (cosmetic only):
   - `label`: "Cross-Reference (Phase 1→2→3)"
   - Optional: add `config.description` mentioning "Hard filters → Score against all rulesets → Route to top campaign type(s)"
   - No structural change; just clearer labeling

2. **Add 4 deliverable nodes** (agent_invocation kind):
   - `deliverable_email` — agent_id: `marketing.content.writing_studio_adapter` (same adapter, different deliverable_type config)
   - `deliverable_social` — same agent, deliverable_type="social"
   - `deliverable_long_form` — same agent, deliverable_type="long_form"
   - `deliverable_landing_page` — same agent, deliverable_type="landing_page"
   
   Each has `config = {agent_id: "marketing.content.writing_studio_adapter", mode: "scheduled", deliverable_type: "<type>", cost_cap_usd: 1.0}`.
   
   Position them in a row below writing_studio_adapter on the canvas (PIPE2 layout).

3. **Add 1 Gate 2 node** (human_gate kind):
   - `gate_2_approval_drawer`
   - `config = {approval_kind: "content_draft", approvers: ["josh@amiralearning.com", "angela@amiralearning.com"], timeout_hours: 72, on_timeout: "escalate", escalation_to: ["jon@amiralearning.com"]}`
   - Waits for all 4 deliverables to complete before firing the approval (the executor in PIPE4 will handle the fan-in wait semantics)

4. **Update edges:**
   - Remove the implicit "writing_studio_adapter is terminal" edge (none exists; it's just the leaf today)
   - Add edges from writing_studio_adapter → each of the 4 deliverables (fan-out)
   - Add edges from each of the 4 deliverables → gate_2 (fan-in)
   - gate_2 becomes the new terminal

### Node count and edge count

Before: 16 nodes, ~23 edges
After: 21 nodes, ~31 edges (16 + 5 new, 4 fan-out + 4 fan-in = 8 new edges minus 0 removed)

### Re-seed

`uv run python scripts/seed_marketing_pipeline.py` is idempotent and updates the pipeline row. Worker runs it after JSON change.

### Visual verification

Open Pipelines → Marketing Pipeline canvas. Confirm:
- writing_studio_adapter is no longer terminal — has 4 outgoing edges
- 4 deliverable nodes visible in a row below writing_studio_adapter
- 4 edges converge into gate_2_approval_drawer
- gate_2 is the terminal node
- Cross-Reference node label shows "Cross-Reference (Phase 1→2→3)"

### Tests

Update `artemis/pipelines/tests/test_marketing_pipeline_seed.py`:
- Node count = 21
- Specific node IDs present: deliverable_email, deliverable_social, deliverable_long_form, deliverable_landing_page, gate_2_approval_drawer
- Edge sanity: writing_studio_adapter has 4 outgoing edges; gate_2 has 4 incoming
- Idempotent re-run produces same final state

## Out of scope

- Splitting deliverable creation into actually 4 different agents (per Jon's call: one Writing Studio Adapter, 4 invocations with different deliverable_type config)
- Per-deliverable independent gates (per Jon's call: 1 gate after all 4)
- Content Registry node (the Figma's "Content Registry" is post-pipeline storage, not a node)
- Other Figma sections (Ruleset versioning, etc. — those are off-pipeline)

## Files

| File | LOC |
|---|---|
| `artemis/pipelines/seeds/marketing_pipeline.py` | ~80 delta (5 new nodes + 8 new edges + 1 label update) |
| `artemis/pipelines/tests/test_marketing_pipeline_seed.py` | ~30 delta |

**Total: ~110 LOC.** Cap 160. Mostly data; tests verify count + structure.

## Invariants

- Re-seed idempotent
- Existing agents referenced (marketing.content.writing_studio_adapter) must exist in DB pre-seed; abort with clear error if not
- conftest hard-fail on non-test DB
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, paste the 5 new node definitions, paste the new edge list, screenshot of updated canvas, test pass count, branch.

---

**Lead notes (not for Codex):**
- The fan-in semantics for gate_2 (waits for all 4 deliverables) are PIPE4's responsibility — PIPE5 just stores the edges. PIPE4 executor reads incoming edge count and waits for all upstream nodes to reach success before firing the gate.
- Cross-Reference's "three phases" label is pure UX clarity; runtime is unchanged (M4's qualifier_rule_layer + cross_reference logic stays).
