# Brief — Wire real agent run-health metrics (stubbed endpoint)

**Type:** dormant/stubbed feature (built shell, never implemented). **Model:** Codex or terminal Sonnet.
**Own worktree**, branch `worker/agent-run-metrics`, cwd INSIDE the worktree. Branch off `main`.

## Problem (verified live 2026-06-05)

Every agent profile shows RUNS 0 / SUCCESS 0% / AVG DURATION — / AVG COST — and "No live run metrics
were found, falling back to a stable design-time profile" — even though `agent_runs` has real data
(e.g. marketing.scout.board_minutes = 74 runs, starbridge_researcher = 108, legislative = 85).

Root cause: `GET /api/stats/agent-metrics` (`artemis/routes/stats.py:153`) is a STUB:
```python
async def stats_agent_metrics() -> AgentMetricsOut:
    """Return empty-state agent metrics stub.
    # TODO(J11-followup): wire real metrics from agent_runs table."""
    return AgentMetricsOut(overview=AgentMetricsOverview())
```
It always returns empty `agents:[]` / `recent:[]`, so the frontend (`fetchAgentMetrics` →
operations-shell `getAgentMetricRow` / recent-runs) has nothing to match. (Note: the per-agent
"Recent runs" list elsewhere works because it comes from the agent DETAIL endpoint's `recentRuns`,
a different query — don't confuse the two.)

## Fix

Implement `stats_agent_metrics` to aggregate from `agent_runs`. Match the existing `AgentMetricsOut`
Pydantic shape EXACTLY (overview, agents[], byType, daily, recent[]) — the frontend already consumes
these wire names; do not rename. Key columns on `agent_runs`: run_id, agent_id (text), status
(success = 'completed'/'succeeded' — confirm the exact terminal-success value used in this codebase),
started_at, completed_at, cost_input_tokens, cost_output_tokens, error, is_ephemeral.

Per-agent aggregate row (what `getAgentMetricRow` reads): keys it expects — `agent_id`, `agent_title`,
`runs`, `successes`, `avg_duration`, `avg_cost`, `total_cost`, `total_input_tokens`,
`total_output_tokens` (verify exact names in operations-shell.js `buildAgentProfile` metrics block).
`recent[]` rows expect `agent_id`, `agent_title`, `status`, `started_at`. Compute avg_duration from
completed_at - started_at; avg_cost from token columns × pricing (reuse any existing cost helper).
Consider excluding `is_ephemeral` runs from aggregates (confirm desired behavior — log the choice).

## Verify LIVE (assert the EFFECT)

- `GET /api/stats/agent-metrics` returns non-empty agents[] with board_minutes runs≈74, and recent[]
  populated; numbers reconcile with `SELECT agent_id, count(*) FROM agent_runs GROUP BY agent_id`.
- In the running app, an agent profile's PERFORMANCE / RUN HEALTH card shows real RUNS / SUCCESS% /
  AVG DURATION / AVG COST (not 0 / fallback). Use the preview/browser to confirm render.

## Constraints
- Read-only aggregation — no writes, no schema change, no migration. Lossless.
- Org dep rule: nothing < 7 days old; commit uv.lock if regenerated. Tests + ruff + mypy clean.
- Replace the `test_stats_agent_metrics_stub` expectation (`artemis/routes/tests/test_j3c_stubs.py:45`)
  with a real assertion (seeded agent_runs → non-empty aggregates). Do NOT merge — report branch + how
  to verify each effect. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
