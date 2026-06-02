# Patch — Pipeline Empty-Signals Handling

**Owner:** Codex (paste-ready)
**Branch:** `codex/patch-pipeline-empty-signals-handling`
**LOC budget:** ~100 (cap 140)
**Depends on:** PIPE4 merged.

## Why

When scouts produce zero signals (current state: all scouts are stubs returning empty), the pipeline runs blindly through brief_composer and suspends at gate_1_signals_inbox creating an approval row with nothing to approve. Phantom approvals.

Per Jon's call: halt cleanly with "no signals this run" status. Don't create phantom approvals.

## Scope

Update `artemis/pipelines/executor.py`:

After the qualifier batch completes (or before brief_composer fires), check if any signals were qualified. If zero:

```python
if node.node_id == "qualifier_brief_composer":  # or whatever the brief-creator node is
    qualified_count = await conn.fetchval(
        "SELECT COUNT(*) FROM signal_queue WHERE pipeline_run_id = $1 AND status = 'qualified'",
        run.id
    )
    if qualified_count == 0:
        # No signals qualified — halt cleanly
        await mark_node_succeeded(node, output_summary="No signals qualified this run")
        # Mark all downstream nodes as 'skipped' (don't execute)
        for downstream in topological_descendants(node):
            await mark_node_skipped(downstream, reason="upstream produced no signals")
        # Mark pipeline_run as succeeded with note
        await mark_run_succeeded(run, summary="No signals this run; downstream skipped")
        return
```

Add `skipped` as a new node state value:
- `pending` → `running` → `succeeded` / `failed` / `suspended` / `skipped`
- `skipped` is a clean terminal state distinct from `succeeded` (operator can tell from history)
- Visualize on canvas as muted gray with "Skipped (no upstream data)" badge — fold into the live-view brief's CSS

## Out of scope

- Detecting partial-empty scenarios (some scouts produce, others don't). v1 just checks aggregate qualified count.
- Distinguishing "no signals because mock scouts" from "no signals because no real opportunity this cycle". Future briefs.
- Surfacing empty-runs differently in run history (just show "Skipped: no signals" reason).

## Tests

- Pipeline_run with 0 qualified signals → brief_composer marks succeeded with "No signals" summary
- Downstream nodes marked `skipped` not executed
- pipeline_run.status = `succeeded`; no approval row created
- Pipeline_run with ≥1 qualified signal → normal flow (gate fires, approval created)

## Files

| File | LOC |
|---|---|
| `artemis/pipelines/executor.py` | ~40 delta |
| `artemis/pipelines/node_executors/agent_executor.py` (brief_composer special-case check) | ~30 delta |
| `alembic/versions/<rev>_node_state_skipped.py` (no migration needed if status is just a string; otherwise enum addition) | ~20 if needed |
| Tests | ~50 |

**Total: ~120 LOC.** Cap 140.

## Invariants

- "skipped" state added cleanly; doesn't break existing run history queries
- node --check on JS (if any touched)
- ./scripts/check.sh passes within exempt set
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, sample run that hit empty-signals path (paste node_states), test pass count, branch.
