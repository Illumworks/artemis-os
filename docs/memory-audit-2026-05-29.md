# Memory Keystone Audit — Built But Dormant

**Date:** 2026-05-29
**Auditor:** Lead (Opus 4.7)
**Status:** Findings confirmed empirically. Production runtime writes ~zero memory. Eleven memory tables hold one row.

---

## Headline

`artemis/memory/` is ~5,800 LOC of working substrate covering store, retrieval (BM25 + semantic + recency hybrid), embeddings, consolidation, conflict detection, graph extraction, archive, backup, and hashchain. Test coverage is ~3,000 LOC. Architecturally complete against the reference plan's P0–P3 phases.

**And it is empty.** Live Postgres state right now:

| Table | Row count |
|---|---|
| `memory_drawers` | **0** |
| `memory_observations` | **1** |
| `memory_evidence` | **0** |
| `memory_scopes` | **1** (bootstrap `global` scope only) |
| `memory_entities` | **0** |
| `memory_relations` | **0** |
| `memory_embeddings` | **1** |
| `memory_conflicts` | **0** |

The single observation came from a user explicitly invoking the Floating Artemis `write_memory` tool. **No agent in the system writes to memory automatically. No agent reads from memory automatically.** This is the same kind of hollowness we just spent nine briefs (CC10–CC18) closing on the self-improvement side, in a different layer.

---

## What is actually wired (the four real consumers)

These are the only production code paths that touch memory APIs outside `artemis/memory/`:

### 1. Floating Artemis — `_write_memory` tool

`artemis/floating_artemis/tools/core.py:79`

```python
from artemis.memory.store import write_observation
```

Wired. Fires only on explicit user tool call. Default scope `agent:floating-artemis`.

**Gap:** every assistant turn isn't auto-captured as a drawer. The conversation evaporates after the turn.

### 2. Floating Artemis — `_query_memory` tool + chat retrieval

`artemis/floating_artemis/chat.py:212` (inspector event)
`artemis/floating_artemis/tools/core.py:48` (user-callable tool)

Reads `search_observations`. Results returned to user as tool output OR emitted as MemoryReadEvent for the UI inspector.

**Gap:** results are NOT injected into the system prompt. Floating Artemis is amnesiac unless the user explicitly asks for memory. The personal assistant cannot proactively use what it knows.

### 3. Daily Brief sources — `search_observations`

`artemis/brief/sources.py:62`

Calls `search_observations(query="work priorities focus today", scope_kind=global)`. Results flow into the daily brief template.

Wired. Soft — gracefully degrades when memory is empty.

### 4. Meetings Summarizer — `insert_raw_input`

`artemis/meetings/summarizer.py:33, 481`

When a meeting transcript is summarized, the raw input is written to memory as a raw_input record (verbatim source preservation).

Wired. Lone bright spot for write paths.

### 5. MCP memory server (external Claude Code clients)

`artemis/mcp/memory_server.py` exposes `memory_search`, `memory_get_observation`, `memory_list_scopes`, etc. for Claude Code / terminal-Lead to query Artemis memory.

Wired for external clients. **No internal agent uses these tools.**

---

## What is NOT wired (the hollow layer)

### A. Trajectory summaries do not become memory observations

`artemis/builder/trajectory_summarizer.py` — zero imports of `write_drawer` or `write_observation`. The summary lands in `agent_run_trajectory_summaries` only. The CC10–CC18 stream we just shipped produces 11+ summaries per pipeline run, and every one of them is invisible to memory.

The keystone plan promised: "memory becomes the self-improvement substrate." Right now self-improvement and memory are parallel, disconnected streams.

### B. Marketing pipeline writes nothing to memory

Scouts, qualifiers, content composers — none of them import any memory write function. Qualified signals land in `signal_queue` and `qualified_signals` only. The decision rationale ("this signal scored 78 because criteria X, Y, Z fired") never becomes a memory observation. Signal genealogy is lost.

### C. Builder doesn't read agent memory

When an operator opens the Builder for an agent, the system prompt is built from `read_recent_runs(target_id)` (trajectory summaries) but NOT from `search_observations(scope='agent:<id>')`. The Builder LLM has access to the agent's recent runs but not to any lessons-learned the agent has ever surfaced.

### D. Floating Artemis doesn't auto-write conversations

Every chat turn could become a drawer (verbatim user message + assistant response). It doesn't. The system has no episodic memory of conversations.

### E. Floating Artemis doesn't auto-read at prompt-build time

No retrieval pass before each turn. The assistant has access to memory only when the user explicitly asks via tool.

### F. Memory shell UI is a stub

`public/js/features/memory-shell.js` (lines 35-148) defines three rooms (review queue, project memory, working memory, agent memory). The shell mounts in Operations. **No backend endpoints are wired. The loading state is permanent.** No drawer/observation inspector exists.

### G. Writing Studio brand-voice memory does not exist

The reference plan's P0 motivation was Writing Studio brand-voice memory — verbatim corpus, scope separation per brand, semantic recall. The substrate supports it (the `brand` scope kind exists). The Writing Studio code does not call any memory function.

---

## Cross-reference to the reference plan

Per `../claudeck-artemis/docs/PLAN-memory-keystone.md`:

| Phase | Reference status | Python rebuild status |
|---|---|---|
| **P0 — Foundations** | Shipped (Node) | Substrate present; **empty in prod** |
| **P0c — Backup/restore** | Shipped (Node) | `backup.py` present, untested in prod |
| **P1 — Consolidation & evidence** | Shipped 2026-05-01 (Node) | `consolidator.py`, `conflict_detector.py` present; **0 evidence rows = unexercised** |
| **P2 — Score & temporal** | Shipped 2026-05-01 (Node) | Present in retrieval module; **unexercised** |
| **P3 — Graph & structural** | Slices 1-3 shipped (Node) | Graph backend present; **0 entities, 0 relations** |
| **P4 — Agent integration** | TBD in Node | **NOT STARTED in Python rebuild** |
| **P5 — Observability** | TBD in Node | **NOT STARTED in Python rebuild** |

The Python rebuild has substrate parity with Node through P3. **P4 (agent integration) is the gap.** This is the layer that makes the substrate earn its weight.

---

## Why this matters strategically

Three concrete impacts of the current state:

1. **Self-improvement loop is half-built.** CC10–CC18 made trajectory summaries reliable + discoverable through the Proposals Inbox. But the summaries are not memory observations — they cannot be retrieved across agents, cannot be linked as evidence to future proposals, cannot be queried by the Builder when reasoning about a different agent that may have hit the same pattern. Memory was supposed to be the substrate that lets agents learn from each others' runs. Right now each agent is a silo.

2. **Floating Artemis is amnesiac.** Jon's primary personal-assistant surface forgets everything between turns unless he manually invokes memory tools. The COO-doc framing of "agents that learn from their own work" is structurally true in the substrate but functionally false in the runtime.

3. **Writing Studio brand-voice memory has no source.** The keystone plan's motivating use case (semantic recall against a brand-voice corpus) is impossible today because no code path writes brand-voice drawers. Studio handoff (CC12) was already on the queue; without memory feeding it, the handoff is shallower than designed.

---

## Proposed brief sequence (M1–M6)

Same shape as the CC10–CC18 stream — small, surgical fixes, each one independently verifiable.

### M1 — Trajectory summary → memory observation (HIGHEST LEVERAGE)

After `trajectory_summarizer.create_trajectory_summary` succeeds, also write an observation scoped to `agent:<agent_id>` with the summary's `what_worked + what_stalled + what_was_missing` as content, and `link_evidence` back to the `agent_run` row.

Impact: every agent run now produces machine-readable memory the moment it lands. Reverses the parallel-stream problem.

### M2 — Builder reads agent memory

In `artemis/builders/executor.py::_build_system_prompt`, add a retrieval pass: `search_observations(scope=f"agent:{target_id}", limit=10)`. Inject results into the Builder's prompt under a "Prior observations" section.

Impact: Builder reasons across all of an agent's history, not just the latest N runs.

### M3 — Floating Artemis auto-write conversations

Every chat turn (user message + assistant response) writes a drawer scoped to `agent:floating-artemis`. Optional follow-on: consolidate every N turns into observation summaries.

Impact: episodic memory. The assistant remembers what was discussed.

### M4 — Floating Artemis auto-read at prompt build

Before each turn, run a retrieval pass on the user's message + recent conversation, inject top-K observations into the system prompt. Same pattern as M2.

Impact: proactive memory use, not just on-demand. Multi-turn coherence improves materially.

### M5 — Marketing signal → memory observation

When a signal transitions to "qualified" status, write a drawer (the raw signal content) + an observation (the qualifier's score + reason codes), scoped to `brand:<brand_id>` or `workspace:marketing`, evidence-linked back to the signal row.

Impact: signal genealogy becomes queryable. "Why did we qualify this kind of signal three months ago" gets a real answer.

### M6 — Memory shell UI wiring + drawer/observation inspector

Connect `public/js/features/memory-shell.js` to real backend routes. Add a drawer/observation inspector that shows the evidence chain for any selected observation. Implements Slice 4 (wings/rooms frontend) from the Node reference plan.

Impact: humans can see what memory holds and trust the layer.

---

## Sequencing recommendation

**Fire M1 first — single highest leverage.** Once it lands and we've seen one full pipeline run produce real observations linked back to runs, every other brief becomes easier to reason about because the data is real, not hypothetical.

M2 and M4 can fire in parallel — different code paths (Builder vs. Floating Artemis), no shared files.

M3 should wait until M4 — it's better to start reading memory before you start writing more of it, so the read path is exercised on real data from the start.

M5 (marketing → memory) can fire any time after M1 — independent of the Builder/Floating-Artemis work.

M6 (UI) should come after M1–M5 produce real data. UI on empty tables is what we have now.

---

## Hard constraints to honor

- **Lossless invariant.** Drawers and observations are never deleted. New writes never replace old ones — supersession only (`superseded_by`). Brief authors must NOT introduce any delete API.
- **Local embeddings only.** The substrate uses an in-process embedding model. No external embedding service. New write paths must accept best-effort embedding failures (the embeddings.py code handles this).
- **Idempotency on content hash.** `write_drawer` and `write_observation` are idempotent on content hash — re-emitting the same content does not create duplicate rows. Brief authors can rely on this; they should not add their own dedup logic.
- **Scope shape.** `(scope_kind, scope_id)` is the universal handle. The six kinds — `project`, `workspace`, `brand`, `agent`, `skill`, `global` — are fixed. New writes must use one of these.

---

## Open questions for Jon

1. **Auto-capture cadence for Floating Artemis (M3).** Every turn = noisy but complete. Every N turns with consolidation = quieter but lossy. Recommendation: every turn as drawer, every 10 turns as observation summary, supersedable.

2. **Marketing scope choice (M5).** `brand:<brand_id>` (Amira-only today) or `workspace:marketing` (team-level)? Recommendation: `workspace:marketing` since Amira is single-brand and the marketing-team motion is the workspace boundary.

3. **Builder memory injection volume (M2).** Top-10 observations per Builder prompt is the default. Could be too much for short reviews. Recommendation: top-5 with relevance threshold.

---

**The pattern from CC10–CC18 applies here. Memory's substrate is solid. The wire-up is missing. M1 alone (~80 LOC + a test + one migration touch) flips the entire self-improvement loop from "summaries exist" to "agents have machine-readable history."** That's the same leverage CC10 had. Recommend firing M1 first, immediately after the Inbox UI placement fix lands.
