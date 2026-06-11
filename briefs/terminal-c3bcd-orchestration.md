# Terminal Orchestration Brief — Build Callie C3b + C3c + C3d (Sonnet sub-agents)

**Owner:** terminal (orchestrator). **Executors:** 3 Sonnet sub-agents. **Lead:** Artemis (Opus) reviews +
merges + does the live/browser verify. **Goal:** close the Callie thread by building the three remaining C3
slices. Each has its own detailed brief — this brief is the orchestration plan + the rules that keep them
from colliding.

## The three slices (each = one sub-agent, one brief)
1. **C3b** — `briefs/callie-c3b-gate-card-via-callie.md` — marketing Gate cards post via Callie's token.
   Touches `artemis/pipelines/node_executors/human_gate_executor.py`.
2. **C3d** — `briefs/callie-c3d-deliverable-draft-body.md` — align the deliverable body with what the composer
   reads (`_latest_draft_content`). Touches `artemis/marketing/writing_studio/invoke.py` + the deliverables
   content node. NO external backend / Google Docs.
3. **C3c** — `briefs/callie-c3c-history-handoff.md` — retired Artemis-DM history → Callie's memory. Touches
   the memory modules. **Has an investigate-first step** (scope semantics) — if ambiguous, the sub-agent
   PAUSES and reports rather than guessing.

## Parallelism + isolation (non-negotiable)
- Spawn each sub-agent in its **own git worktree** (isolation). This is required — do NOT let three agents
  edit the one shared working tree (that caused branch-cross-contamination earlier this session).
- With worktree isolation, the three may run **in parallel** safely. Sequential is also fine if you prefer;
  C3c can lag (its investigate step may pause for Lead input).
- File-overlap note: C3c (memory) is independent. C3b (human_gate_executor) and C3d (writing_studio/invoke +
  deliverables node) are different files but adjacent in the pipeline/marketing area — worktree isolation
  removes the live-edit conflict; any residual overlap is resolved at MERGE time (see below), not during edit.

## Each sub-agent must
- Implement ONLY its brief's scope. Read the brief fully first.
- Write **DB-backed tests where natural** — the test DB is repaired and at head (`artemis_test` @ 0078), so
  real DB tests work now; don't default to mock-only.
- Run `uv run ruff check` + `uv run mypy` (strict) on touched files and the targeted test suite; report results.
- Commit to a worker branch (`worker/callie-c3b-…`, `-c3c-…`, `-c3d-…`). **Do NOT merge to main.**
- Report: the diff (files + key hunks), test/ruff/mypy results, and any judgment calls. C3c: report the
  scope-semantics finding before the write step if the memory model is ambiguous.

## Constraints (all sub-agents)
- Lossless (no deletes; supersession/copy only). No new dependencies (org policy: nothing <7 days old).
- ruff + mypy strict clean on touched files. The pre-existing repo-wide format debt in ~9 unrelated files
  (agent_builder.py, routes/stats.py, several existing tests) is a known baseline — do NOT fix those here.
- Do NOT touch: the P1/C2 Slack routing internals (events receiver auth/HMAC), the composer selection-toolbar
  code (hard-won), or Artemis's personal-DM scoping.

## Merge (Lead does this — sequential, NOT parallel)
Terminal collects the three verified branches and reports them to Lead (Opus). Lead merges **sequentially**
to main in order **C3b → C3d → C3c**, running the combined Slack/marketing/memory suite after each merge to
catch any cross-slice interaction, then does the live/browser verify:
- C3b: trigger a marketing gate → card posts as Callie (`U0B9S32PTAM`).
- C3d: open a campaign #18 draft in Writing Studio → body renders.
- C3c: ask Callie about a topic from the retired history → she recalls it.
A single launchd restart (`launchctl kickstart -k gui/$(id -u)/me.artemisos.app`) makes all three live.

## Done = Callie thread closed
Callie posts marketing comms as herself, #18 drafts render their content, and she carries the prior marketing
history in her memory.
