# Pipeline Authoring Principles + Durability

**Audience: the AI Pipeline Builder (its grounding/system prompt) + any human building a pipeline on Artemis.**
**Status:** Living. Distilled from Phase BH (2026-05-26/27), where building one solid marketing pipeline surfaced the principles every pipeline needs.

The point of this doc: Artemis pipelines are meant to be built by *conversation* with the Builder — by operators, not engineers. For that to produce **solid** pipelines without per-pipeline hand-fixing, the lessons below must be baked into how the Builder designs pipelines and how the platform executes them.

---

## Part 1 — What the platform guarantees (durability)

A pipeline author does NOT configure these; the execution engine provides them:

- **Durable dispatch.** Runs are kicked off so they can't be silently dropped (the executor task is retained, not fire-and-forget — fixed in CC7). A run that's accepted *will* start.
- **Orphan self-heal.** A run that's `queued` without starting (e.g. server bounced at the wrong moment) is re-dispatched by the scheduler sweep before being failed.
- **Per-node failure isolation.** One node failing (an agent erroring, a tool timing out) marks that node failed and lets the rest of the run proceed; it does not crash the whole pipeline.
- **Wall-clock bound.** Each agent/subprocess has a timeout (env-configurable) so a stuck node can't hang a run forever.

### What the platform does NOT yet guarantee (know the boundaries)
- **Crash-resume of an in-flight run.** If the server dies *mid-run*, that run is not resumed from its last completed node. (Future hardening.)
- **Automatic per-node retries/backoff.** A failed node stays failed for that run; no auto-retry. (Future hardening.)
- **Strong idempotency.** Re-running a pipeline may re-emit; dedup is the author's responsibility via node logic (see Principle 7).

When these matter (heavy production load), they're a dedicated "durability hardening" effort — not a per-pipeline concern.

---

## Part 2 — Authoring principles (what makes a pipeline solid)

These are the lessons that, if missed, produce a pipeline that "runs successfully" while doing nothing. Each was a real failure mode in Phase BH.

### P1 — Give every agent node an imperative *task*, not just an identity
An agent's system prompt says *who it is*; it needs a user-message that says *do this now*. Without it, the agent reads its prompt as a spec and replies "what's your ask?" instead of acting.
→ The Builder must attach an imperative instruction to each agent node ("Run your scan now. Use your tools. Emit results. Act autonomously; don't ask for clarification."). The platform synthesizes a role-default if the node doesn't specify one.

### P2 — Agents do work through TOOLS; their text output is a summary, not the work-product
A tool-using agent's real output is the *side effects of its tool calls* (rows written, status changed), committed to the DB. The agent's returned text is a human summary. Don't design a node to "parse the agent's text" for structured data.

### P3 — Downstream/gate nodes READ from the DB, not from upstream node text
Because of P2, a downstream node (qualifier, gate, content) must read the prior stage's *committed effects from the DB* (e.g. signals with `status='qualified'`), NOT structured keys from the upstream node's output text — those won't exist for tool-using agents.
→ This is the single most common silent-failure: the data is in the DB, but the consumer reads node_states and finds nothing.

### P4 — Tools must be implemented AND scoped per agent
An agent's declared `tools` list is both its capability set and its security boundary. A declared-but-unimplemented tool is silently dropped — the agent then can't do its job. The Builder must only give an agent tools that exist, and the platform scopes each run to exactly that agent's tools (it cannot call another agent's tools or platform built-ins).

### P5 — Tool-using agents need a tool-capable provider
Not every provider can call custom tools. On the Claude Code subscription, tool-use runs through an MCP server (the engine handles this); the API path (with a key) runs the in-process tool loop. The Builder picks a provider; the platform routes tool-using agents to a tool-capable path. A text-only provider + a tool-using agent = an agent that can't act.

### P6 — Load the data the agent needs INTO its prompt
An agent only knows what its composed prompt contains: its persona/voice, its goal, its allowed reason codes / domain rules, relevant context. If the blueprint has rich fields but the runtime doesn't inject them, the agent runs blind. The platform composes the full prompt from the agent row + shared config; authors should put operational knowledge where it gets injected, not in fields nothing reads.

### P7 — Design explicit empty/skip + dedup semantics
A run with no qualifying items should halt cleanly (downstream skipped, no phantom output) — and the author must decide dedup across runs (e.g. suppress-recently-seen) so repeat runs don't re-emit the same thing OR suppress everything. Test the *second* run, not just the first.

### P8 — Single-source operational config, read at runtime
Rules/criteria that operators change (reason codes, territory, thresholds) live in ONE place (a config table / spec), read at runtime — not copied into each agent. One edit reflows everywhere. (See the Signal Playbook / Josh-spec pattern.)

---

## Part 3 — "Is my pipeline solid?" checklist

Before trusting a pipeline, verify by *running it*, not by inspecting that it's wired:
1. Does a real run produce the intended **side effects in the DB** (not just "nodes succeeded")?
2. Does the **second** run behave correctly (dedup doesn't death-spiral; new items still flow)?
3. Does each tool-using agent have **implemented, scoped** tools and a **tool-capable provider**?
4. Does every agent node have an **imperative task**?
5. Do downstream/gate nodes **read effects from the DB**?
6. Does a **node failure** degrade gracefully (run continues, failure visible)?
7. Does the **empty-input** path halt cleanly?

If you can't answer yes with evidence from an actual run, it's not solid yet — it just looks wired. (Phase BH's entire lesson: "looks done" ≠ "is done"; only running it reveals the truth.)

---

## Part 4 — How the Builder uses this

The AI Pipeline Builder should treat Part 2 as design constraints it enforces when composing a pipeline: every agent node gets an imperative task (P1), only existing+scoped tools (P4), a tool-capable provider for tool-users (P5), downstream nodes wired to read DB effects (P3), and explicit empty/dedup handling (P7). The Builder should also run/validate against Part 3's checklist before presenting a pipeline as ready — surfacing "I verified a real run produced N signals" rather than "I wired the nodes."

This is how the platform delivers solid pipelines by conversation, without engineers fixing each one.
