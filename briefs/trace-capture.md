# Brief: Execution-Trace Capture (P6 / self-evolution foundation)

## Why
P6 self-evolution (Artemis learns from accumulated execution history → proposes prompt/skill/routing changes →
human approves → applied) is blocked on having traces to learn from. Today there is NO per-run trace store
(no `agent_traces` table, no capture hook). Build that foundation now so data STARTS accumulating — P6 also
needs weeks of real data, so every day without capture is lost learning. This also fills the proactivity
"trace capture" gap noted in the roadmap realignment.

## FIRST: reconcile with what already exists (do NOT duplicate)
Read before designing:
- `docs/p2-proactivity-build-plan.md` (the `P2-foundation` / trace-capture note)
- `docs/self-improvement-consumer-side.md`
- The existing **trajectory-summary** + **agent_runs** models/code (grep `trajectory_summary`, `agent_runs`,
  the executor/run path + `artemis/floating_artemis/chat.py` `handle_turn`).
Determine what trajectory summaries ALREADY capture and what is MISSING for P6 (finer-grained, structured
per-run/per-turn trace). Build the minimal NEW layer that complements — not duplicates — trajectory summaries.

## Build
1. **`agent_traces` table** + alembic migration (**revision 0096, down_revision 0095**). Suggested columns
   (refine against existing models): `id`, `agent_id`, `session_id`/`run_id`, `feature_tag`/surface,
   `provider`, `model`, `input_summary` (or context summary / prompt hash + truncated), `tools_used` (JSON),
   `output_summary`, `outcome` (success/error/partial), `error` (nullable), `latency_ms`, token/cost if
   readily available, `created_at`. Index `(agent_id, created_at)`. Owner/agent-scoped (not cross-team).
2. **Capture hook (NON-BLOCKING).** Record a trace row at the end of the agent execution path — the
   floating-agent turn (`chat.py` `handle_turn`) and/or the agent_run/executor path, wherever runs actually
   flow. MUST be fire-and-forget: wrap in try/except, never raise into or slow the user-facing turn.
3. **Read/query helper** (e.g., recent traces per agent) for the future P6 consumer.

## Constraints
- Non-blocking; never breaks/slows a turn. No new dependencies. Match surrounding style.
- Migration revision **0096** (down_revision 0095); commit the migration.
- The worktree has NO `.env` → UNIT tests only. Lead runs `alembic upgrade head` + a live smoke after merge.

## Tests (unit)
Trace row built correctly from a run; a capture failure is swallowed (does NOT raise); query helper returns
recent traces. Flag any env-coupled checks for the Lead to run live.

## Deliverable
Branch `worker/trace-capture`; commit; report files changed + decisions + what trajectory summaries already
covered vs what you added + test results. Do NOT merge.
