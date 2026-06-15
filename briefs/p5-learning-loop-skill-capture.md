# Worker Brief — P5: the learning loop (skill capture + reuse)

**Owner:** Sonnet worker (backend). **Lead:** Opus — verifies the loop closes end-to-end + merges.
**Isolation:** own worktree (`worker/p5-learning-loop`), own test DB (name contains `artemis_test`); commit on
the branch before reporting; do-NOT-merge. Adds a migration → Lead runs `alembic upgrade head` post-merge.
**Status:** READY. Design + decisions below come from a Lead-reviewed grounding pass.

## The loop (what P5 closes)
`agent run` → `trajectory summary` (EXISTS) → **distill a repeated success into a skill proposal (NEW)** →
human approves (EXISTS) → `Skill` row (EXISTS) → **inject the skill into that agent's future runs (NEW)** →
**track usage (NEW)**. The agent gets better over time, gated by human approval.

## REUSE — do NOT rebuild (verified live)
- Trajectory capture: `summarize_async()` fires after every `run_agent()` (`artemis/builders/executor.py:559`);
  `AgentRunTrajectorySummary` rows (`what_worked`/`what_stalled`/`what_was_missing`);
  `get_trajectory_summaries_for_agent()` (`artemis/builder/repository.py:286`).
- Proposal→approval: `create_definition_proposal(... kind="skill", proposed_by="self-improvement", citations={run_ids, rationale})`
  — `"self-improvement"` is ALREADY in the `ck_definition_proposals_proposed_by` CHECK constraint. Approve flow
  `_commit_skill()` (`artemis/builder/engine.py`) writes the `Skill` row. Proposals Inbox already lists
  `skills_with_pending_proposals` (`artemis/builder/routes.py:637`) — distiller proposals appear there for free.
- LLM: use `resolve_adapter_async(provider="claude-code", feature_tag="skill_distiller")` EXACTLY like
  `trajectory_summarizer.py:416-429`. There is NO `ANTHROPIC_API_KEY` here — never use a direct AnthropicAdapter.
- `Skill` model `artemis/builders/models.py:186`; `agent_skills` join + `list_skills_for_agent()`
  (`artemis/builders/repository.py:530`).

## Lead decisions (DECIDED — implement exactly)
1. **Trigger = ON-DEMAND** (not cron/threshold). New `POST /api/builder/agents/{agent_id}/distill-skills`.
   Cheapest/safest/predictable; no surprise LLM spend. (Cron can come later.)
2. **"Repeated procedure" = a multi-step procedure that appears in `what_worked` in ≥2 of the last 10
   trajectory summaries for that agent.** Bake this threshold into the distiller prompt. Conservative on
   purpose (precision over recall — a noisy skill proposal wastes the human's review).
3. **Skill injection scope (Component C):** inject ONLY skills that (a) are `status="approved"`, (b) have
   non-empty `instructions`, AND (c) whose `tools[]` overlap the agent's own tool list. HARD CAP: max 3 skills,
   ~200 tokens of instructions each. Empty/missing `agent_skills` → inject nothing (no error).
4. **Surface scope (v1):** the loop operates on BUILDER/automation agents (`run_agent`), where the trajectory
   data comes from. Inject skills into the agent executor's system prompt (Component C). Do NOT inject into the
   Floating Artemis (Artemis/Callie) prompt in v1 — FA keeps on-demand `list_skills`. (FA proactive injection =
   future.)

## Build tasks
- **A — Skill distiller** (new `artemis/builder/skill_distiller.py`): `distill_skill_candidates(session, agent_id)`
  reads last 10 trajectory summaries (+ optionally `search_observations` agent-scope trajectory obs), passes
  them AND the existing skill catalog (`engine.read_existing("skill")` — for dedup) to the LLM via the resolver,
  prompt per decision #2, parse a JSON array of skill defs (slug/name/description/instructions/tools[]), and for
  each NON-duplicate create a `definition_proposal` (`kind="skill"`, `proposed_by="self-improvement"`,
  citations={run_ids, rationale}). Returns a summary (n_proposed). **Never auto-approves.**
- **A2 — route**: `POST /api/builder/agents/{agent_id}/distill-skills` → runs the distiller. Owner-gated like the
  rest of the builder routes (`require_token` + `require_owner` — the builder is owner-only per M3). Run the
  distiller as fire-and-forget OR await (it's user-initiated, latency acceptable) — your call; if backgrounded,
  use the `summarize_async` `_BACKGROUND_TASKS` pattern, don't block.
- **feature_tag**: add `"skill_distiller"` to `artemis/providers/feature_catalog.py` mirroring
  `"trajectory_summary"`.
- **C — skill injection** in `artemis/builders/executor.py:_build_system_prompt()` (line ~53): load
  `list_skills_for_agent(session, agent.id)`, filter per decision #3, append a bounded "Learned skills" block.
  Must be a no-op when there are no qualifying skills.
- **D — usage tracking**: migration adds `usage_count INTEGER NOT NULL DEFAULT 0` + `last_used_at TIMESTAMPTZ
  NULL` to `Skill`. Increment when a skill's instructions are injected into a run (Component C path).

## Cardinal constraints
- **Human-gated:** the loop ONLY ever creates PROPOSALS. It never writes/approves a `Skill` itself. Approval
  stays the existing human endpoint. (Precision/safety — the agent must not teach itself unreviewed behavior.)
- **Fail-safe:** distiller LLM/JSON error or no provider → create ZERO proposals, log, never crash the route or
  any run. Injection must never break execution (missing skills/empty join/oversized instructions → skip).
- **Dedup:** the distiller MUST receive the current skill catalog and must not propose a skill that duplicates
  an existing slug/intent. Re-running the distiller must not pile up duplicate proposals (skip if an equivalent
  pending proposal already exists).
- **Cost:** one LLM call per distill invocation; token cap on injection (decision #3). Report added cost.
- **Lossless:** no deletes; superseded/rejected proposals follow the existing state machine.

## Verify (REQUIRED — assert the loop CLOSES, not "tests pass")
Build a smoke (LLM mocked for determinism, plus ONE real-adapter call to prove the distiller's LLM path returns
live) that exercises the REAL path:
1. Seed ≥2 trajectory summaries for an agent whose `what_worked` describes the same procedure → run the
   distiller → assert a `definition_proposal` (kind=skill, proposed_by="self-improvement", with citation
   run_ids) is created, and that a duplicate run does NOT create a second identical proposal.
2. Approve it via the existing endpoint → assert a `Skill` row exists (status approved).
3. Assign it to the agent (or have approval assign) → run that agent → assert the skill's instructions appear
   in the built system prompt (within the cap, only on tool-overlap) AND `usage_count` incremented +
   `last_used_at` set.
4. Negative: an agent with no qualifying skills → system prompt unchanged, no error; distiller with a
   no-provider/error → zero proposals, no crash.
Report: branch+commit (not merged); each loop step's observed EFFECT; the real-adapter distiller proof; added
token cost; confirmation it's human-gated + fail-safe; full test result (verify any pre-existing failures vs main).
