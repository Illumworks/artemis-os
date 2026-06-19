# Brief — Ares #3: Sub-agent cost fleet (Codex + local LLM + named delegation)

**Owner:** terminal opus (Lead). **Read first:** `docs/ares-architecture.md`
(Decision 2 — the cost/tool-less constraint), `docs/ares-plan.md` (§6 routing, P1/P2).
**Depends on:** Brief 2 (Ares drives the coding loop).

**Goal:** Ares saves cost by **planning on Claude/Opus** and **delegating
high-volume or cheap, TOOL-LESS sub-tasks to Codex and a local LLM (LM Studio)**,
plus a **named multi-step delegate primitive** (beyond one-shot spawn_subagent).

## The hard constraint (design around it, do not fight it)
`codex` and `lm-studio` are **text-only — they cannot run tools**. So delegate to
them ONLY tool-less sub-tasks: "produce this diff/code as text", "summarize these
files", "classify/triage", "draft this doc". Any sub-task that must *call tools*
(read files, run bash, git) stays on the tool-capable path (claude-code / Ares).
LM Studio runs locally via `ARTEMIS_LM_STUDIO_BASE_URL` (Jon hosts it for testing)
and must NEVER be a `complete_with_fallback` fallback target.

## Scope

1. **Per-task provider routing in the loop:** route Ares's sub-steps by kind via the
   existing cascade (`resolve_adapter_async` / `complete_with_fallback` with a
   `feature_tag`), e.g. `feature_tag="ares_codegen_bulk"` → Codex/local;
   `feature_tag="ares_plan"` → Claude/Opus. Use the
   `feature_routing_overrides` mechanism (`artemis/costs/routing_candidates.py`) so
   the routing is data-driven + tunable, not hardcoded. Bulk codegen returns TEXT
   (a diff/file body) that Ares then applies via his (tool-capable) edit path.

2. **Named multi-step delegate primitive** (beyond one-shot `spawn_subagent`):
   - A `delegate(task, provider_hint, result_schema, max_turns)` capability that
     dispatches a NAMED, trackable sub-run, collects a structured result, and
     reports back to the parent Ares session. Persist it (a `named_delegations` row:
     name, steps, provider, status, result) so a long sub-task survives + is
     resumable/recoverable (mirror the Argus persisted-dispatch + startup-recovery
     pattern — fire, persist, recover).
   - Provider selection respects the tool-less constraint: a delegation that needs
     tools cannot be sent to codex/local.
   - Result validation: Ares validates the sub-agent's output (compiles? tests pass?
     matches result_schema?) before trusting it — the "delegate → validate →
     report-up" loop. Don't trust un-validated sub-agent output.

3. **Cost visibility:** record which provider served each delegated step (the cascade
   already supports `serving_provider_out`) so cost attribution is accurate and Jon
   can see where the savings land.

## Constraints / gotchas
- Tool-less constraint above is the #1 correctness risk — gate it explicitly:
  refuse to route a tool-requiring sub-task to codex/lm-studio.
- Local LLM may be slow/offline during testing — degrade gracefully (fall back to a
  tool-capable provider, never to lm-studio as a fallback target; never hang).
- Circular imports (lazy provider imports). Migration numbering. Restart `-k` + verify pid.
- Observe the EFFECT: confirm a delegated step actually ran on the cheaper provider
  (check `serving_provider`/cost event), not just that Ares said it did.

## Verification (observe the EFFECT)
- A bulk-codegen sub-task is served by Codex (or local), returns text, and Ares
  applies it — confirmed via the cost/serving-provider record, end to end.
- A delegation requiring tools is correctly REFUSED for codex/local and runs on a
  tool-capable provider instead.
- A named delegation persists, survives an app restart (recovery), and reports a
  validated result back to the parent session.
- Local-LLM path works against a real `ARTEMIS_LM_STUDIO_BASE_URL` for a tool-less
  task; with LM Studio offline, it degrades gracefully (no hang, sensible fallback).
- Unit tests for the routing gate (tool-less enforcement) + the delegate
  persist/recover/validate loop.

**Deliverable:** committed; report the routing feature_tags + overrides added, the
delegate primitive's schema/table + recovery behavior, the tool-less enforcement
point, and a live proof that a sub-task ran on the cheaper provider (with the
serving-provider/cost evidence).
