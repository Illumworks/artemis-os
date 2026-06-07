# Brief: Phase 3 — close the self-training loops (+ ground the first draft)

**For:** the TERMINAL Opus Lead (2nd Claude Code Max) to orchestrate via parallel Sonnet workers.
**Back to:** the app Opus Lead (me) for live verification + merge to `main`. Local-only git.
Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**Context docs to read first:** `docs/writing-studio-and-self-training-audit-2026-06-03.md`
(the WRITE-ONLY rejection-loop findings + the "smallest set of files to close it"), and the
Phase 1/2 entries in `docs/LEAD-SESSION-LOG.md`. Frozen Node reference: `../claudeck-artemis/`
(`server/writing-studio-invoke.js` for proposed-learning extraction + the training-candidates
flow). There is a partially-scoped older brief `briefs/cc29-rejection-memory-carryover.md` — read
it and fold in anything useful.

**Current state (verified):** Phase 2 compose engine (`artemis/marketing/writing_studio/compose_engine.py`
+ `POST /api/writing-studio/drafts/{id}/compose`) already injects the ruleset and RETURNS proposed
learnings, but does NOT persist them. Reject reasons are captured at some surfaces
(`signal_queue.rejected_reason`, `Approval.decision_payload["reason"]`) but are dropped before the
memory observation and no runtime agent reads gate-decision observations. `write_pipeline_gate_decision_observation`
+ `write_signal_gate1_approval_observation` live in `artemis/builder/memory_carryover.py`.

This is the CORE self-training arc — build it carefully; verify the EFFECT live, not just unit-green
(live smokes have caught real bugs here repeatedly).

---

## Piece A — Ground the FIRST auto-draft in the ruleset (Phase-2 remainder)

Today only the interactive compose conversation is rule-grounded; the initial draft the
`marketing.content.writing_studio_adapter` agent produces at Gate-2 does not read writing_rules/
examples. Make initial draft generation pull the same ruleset the compose engine uses (reuse
`compose_engine`'s prompt-assembly / `buildWritingMemoryPrompt`-equivalent + the same
no-fabricated-claims guardrail). Decide the cleanest seam: either enrich the content agent's prompt/
context with the ruleset, or route initial generation through a shared prompt-builder. Keep the
anti-fabrication guardrail. VERIFY: a freshly generated draft visibly reflects the Amira voice/rules.

## Piece B — Writing learning loop (propose → approve/reject → ruleset)

1. Migrate the `writing_training_candidates` table (it was NOT ported to Python). Mirror the Node
   schema; chain off the current alembic head (`uv run alembic heads`; single linear head).
2. Persist the proposed learnings the compose engine already extracts (`proposedCandidates`) into
   `writing_training_candidates` with status "proposed", linked to the draft/profile.
3. Wire the review UI that currently says "not wired in this rebuild yet" (`public/js/features/
   writing-studio.js` ~1455 + the `+ Propose` dropdown ~1618): real endpoints to list proposed
   candidates, APPROVE one (promote into `writing_rules`/`writing_examples`) or REJECT/dismiss it.
   APPROVING a rule modifies the live ruleset — this is the human-in-the-loop gate; keep it explicit.
VERIFY: compose a turn that yields a proposed learning → it appears in the review UI → approving it
creates a real writing_rule that then shows up in subsequent prompts.

## Piece C — Rejection learning loop (signals + content)

The load-bearing one. Three sub-steps (audit §"Smallest set of files"):
1. **Capture the reason — OPTIONAL, NEVER REQUIRED.** Add an optional free-text reason on the in-app
   reject surfaces (signal reject in `signal_queue.py`; content/gate reject in `approvals.py` /
   `resume_run` — add `reason` to `ResumeRunRequest`). Frontend: an optional "why?" field on reject
   (do not block reject if empty).
2. **Write the reason into the memory observation, scoped to the AGENT.** Add a `rejection_reason`
   param to `write_signal_gate1_approval_observation` + `write_pipeline_gate_decision_observation`
   and include it in the observation content; pass the already-captured reason through (today it's
   orphaned). Add an agent-level scope (e.g. `agent:<qualifier_or_content_slug>`) so an agent can
   later retrieve its OWN history — derive the agent from the signal/draft provenance.
3. **Make the agents READ their own past rejections at runtime.** In
   `artemis/pipelines/node_executors/agent_executor.py` (where `shared_context` is built before
   `run_agent`), retrieve recent gate-decision/rejection observations scoped to this agent (via
   `artemis/memory/retrieval.py search_observations`) and inject them into the agent's context so the
   next run sees "here's what you got rejected for, and why." Apply to the qualifier agents (signals)
   AND the content/writing agents (drafts).
VERIFY (the whole point): reject a signal/draft WITH a reason → the reason lands in a memory
observation scoped to the agent → on the next run, that agent's prompt/context contains the prior
rejection + reason. Demonstrate the loop end-to-end with a live run.

---

## Orchestration guidance (for the terminal lead)

- A, B, C are largely independent and parallelizable; within C the 3 sub-steps are sequential
  (capture → write → read). Suggested: fan out A, B, and C-step-1+2 in parallel; do C-step-3 (the
  agent-context read) after C-step-2 lands. Watch shared files: `writing_studio.py`,
  `memory_carryover.py`, `agent_executor.py`, `models.py`/migrations — give each worker disjoint file
  ownership or sequence the overlaps; only ONE new alembic migration head at a time.
- Each worker: own git worktree, branch `worker/p3-<piece>`, unit tests run with
  `ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest ...`,
  ruff + ruff format + mypy clean. Workers must NOT run the live app (no `.env` in worktrees) and must
  NOT touch OKR rows or add/upgrade dependencies.
- **APPROVING writing rules + grounding drafts touch brand/voice + the ruleset — surface anything
  ambiguous rather than inventing brand content.**
- **Handoff back:** report each branch + its diff + test results to the app Opus Lead (me). I apply
  any migrations to `artemis_os`, verify each piece LIVE (real compose/reject/run against the running
  app on :8000), and merge to `main`. Log progress in `../claudeck-artemis/COORDINATION.md`.
