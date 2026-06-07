# Hollowness Audit — Full App Pass

**Date:** 2026-05-29
**Auditor:** Lead (Opus 4.7)
**Method:** Backend module inventory + cross-module import graph + DB row counts for 46 surface tables + spot-check of high-risk surfaces.
**Status:** Quick taxonomic pass complete. Five surfaces flagged for deeper audit. Three new pieces of hollowness discovered.

---

## Headline

We just spent CC10–CC18 fixing the **producer side** of self-improvement (trajectory summaries — now landing reliably, 35 in DB). We then audited **memory** (built but 1 row of data, audit just landed). This wider pass finds a third parallel hollowness on the **consumer side of self-improvement**, plus several surfaces that are scaffolded but dormant.

**The smoking gun this pass found:**

```
definition_proposals  |  0 rows
agent_run_trajectory_summaries  |  35 rows
builder_sessions  |  10 rows
```

After nine briefs shipped specifically to make the self-improvement loop produce proposals, after 35 trajectory summaries landed, after 10 Builder sessions were created — **zero proposals have ever been written to the DB.** The end-to-end loop (operator opens Builder → Builder reads summaries → proposes → row lands) has never fired. The first execution of `engine.commit()` has not happened. This is the same hollowness pattern in a third layer.

---

## UPDATE — manual smoke complete, Layer 4 root-cause identified

Lead ran the smoke (Builder session 11, target=brief_composer, 4 trajectory summaries). The Builder LLM did beautiful diagnostic work — read the summaries, identified 3 real failure patterns (ILLEGAL_TRANSITION hallucination, N+1 probe loop, silent no-op completions), drafted a complete revised definition with prompt + tool delta + rationale. **But it could not call `propose()` because the tool was never given to it.** Its own response: *"The `propose` tool isn't wired into this session's tool catalog — I can see it's referenced in the system prompt but isn't [a callable tool here]."*

### The structural bug

`artemis/providers/claude_code/adapter.py:100-162` (`complete()` method):

- Receives `request.tools` from caller
- **Ignores it.** Flattens conversation to a single prompt string via `_flatten_to_prompt`
- Runs `claude --print --output-format json`
- Returns text response with `stop_reason="end_turn"` — never `"tool_use"`

The Builder's loop at `agent_builder.py:464` calls `adapter.complete(request)` — with `tools=tool_specs` populated. The adapter drops the tools on the floor. The LLM literally has no `propose` tool callable from inside the claude-code subprocess via this path.

Meanwhile, `run_with_tools()` in the SAME adapter (line 164) — used by marketing pipeline agents — DOES support tools via MCP server (CC1/CC2). Pipeline can call tools; Builder cannot. Two adapter paths, only one wired.

### The "subscription-only" trade-off this exposes

The Builder cascade is `("claude-code", "codex", "lm-studio", "anthropic")` (`routes.py:48`). The anthropic adapter (`agent/client.py:117`) **does** support tools correctly — passes them to the API, parses `tool_use` blocks, returns them in the response. **And the `ANTHROPIC_API_KEY` is already set in `.env`** (verified empirically).

So the Builder could use tools today by inverting its cascade — but that means per-token API cost for every Builder interaction, which violates the stated "subscription-only" constraint.

### The three fix paths

| Option | LOC | Subscription-only? | Cost per Builder session | Notes |
|---|---|---|---|---|
| **A: Anthropic-first cascade for Builder** | ~10 | ❌ Broken | ~$0.05–0.30 per session (Sonnet 4.6 pricing × few KB context) | Smallest, cleanest semantics, breaks invariant |
| **B: Builder uses `run_with_tools` + MCP-exposed propose tool** | ~200-300 | ✅ Preserved | $0 marginal | Requires adding propose/read_existing/read_recent_runs/test_run to MCP server with `builder_session_id` scoping. Same pattern as marketing pipeline tools today. |
| **C: Hybrid — anthropic for Builder, claude-code for pipeline** | ~10 | Partial | Builder traffic only | Documented architectural split: Builder = human-facing low-frequency interactive (anthropic), pipeline = high-volume autonomous (claude-code). Costs bounded by Builder usage. |

**Lead's recommendation:** **Option C.** The Builder fires maybe 10–50 sessions per month at small token counts. Cost would be measured in cents per month. Option B is principled but requires recreating the MCP scoping architecture for a different context-shape (session_id vs run_id) — substantial work for an interactive surface where per-token cost is irrelevant at this volume. Marketing pipeline cost concern was about continuous high-volume token burn; Builder is not that.

**Jon-call required** before writing the brief. Three considerations:
1. Is the subscription-only invariant *strict* (no API token cost ever, anywhere) or *scoped* (no API token cost in autonomous runtime paths, OK for human-driven interactive surfaces)?
2. Once `propose` is callable, the consumer side of self-improvement closes. The next bottleneck is approval ergonomics + memory writes (M1).
3. If we go Option B (MCP), the marketing pipeline's MCP infrastructure becomes the universal tool-execution path for the whole platform — Builder, Floating Artemis, Pipeline AI Panel all inherit it. Bigger upfront, bigger downstream payoff.

---

## The classification framework

Each surface scored on 5 dimensions:

- **Substrate** — backend code exists
- **Integration** — real production callers wire it into runtime
- **UI** — frontend surface mounted and visible
- **Exercise** — real data from production use (DB row count)
- **Tests** — coverage includes integration paths, not just unit-level substrate

Verdict labels:
- 🟢 **HEALTHY** — all five dimensions pass
- 🟡 **SHALLOW** — wired but sparse exercise or missing one dimension
- 🟠 **DORMANT** — substrate + maybe UI, no integration or exercise
- 🔴 **HOLLOW** — substrate present, declared complete, but loop demonstrably doesn't fire

---

## Surface-by-surface verdict

### 🟢 HEALTHY — exercised end-to-end

| Surface | Substrate | Integration | UI | Exercise | Tests | Notes |
|---|---|---|---|---|---|---|
| **Marketing pipeline** | ✅ | ✅ | ✅ | ✅ 290 agent_runs / 195 signals / 358 tool_invocations / 36 pipeline_runs | ✅ | The substrate that's actually working. CC10–CC18 closed the producer side; signal_queue alive. |
| **Agents roster** | ✅ | ✅ | ✅ | ✅ 18 agents | ✅ | Roster page works; blueprint loads; CC18 anchors Builder. |
| **Tool invocations (MCP)** | ✅ | ✅ | ⚠️ partial | ✅ 358 logged | ✅ | CC17 working. No UI surface to browse invocations yet. |
| **Floating Artemis (substrate)** | ✅ | ✅ | ✅ | ✅ 102 sessions / 82 messages | ✅ | Sessions persist, messages thread. But memory hookup absent (see memory audit). |
| **OKR Studio (personal scope)** | ✅ | ✅ | ✅ | ✅ 4 objectives / 20 KRs / 30 activity rows / Jon owner / Q2 2026 cycle | ✅ | Working at personal scope. Roadmap is scope expansion (marketing-team → company), not hollowness. |
| **Builder sessions (CC18 path)** | ✅ | ✅ | ✅ | ✅ 10 sessions, target_id wired | ✅ | Sessions create with target_id; trajectory summaries flow in. |
| **Trajectory summarizer (post-CC18)** | ✅ | ✅ | ⚠️ partial | ✅ 35 summaries | ✅ | Producer side closed. Consumer side downstream (proposals) is the hollowness. |
| **Integrations (gcal/slack/jira/granola)** | ✅ | ✅ | ✅ | ✅ 4 active integrations, all verified recently | ⚠️ no tests dir | Connected; granola verified today. Whether each integration's data flows somewhere useful is a separate question (see SHALLOW below). |

### 🟡 SHALLOW — wired but sparse / one dimension thin

| Surface | Row count / state | Gap | Risk |
|---|---|---|---|
| **Pipelines** | 1 pipeline configured (`marketing.main`) | Only one pipeline exists. Platform supports many; one in use. | Low — by design today, but worth knowing. |
| **Writing Studio** | 1 profile (Amira Marketing Voice), 2 rules, 7 examples, 9 sources, 0 brand-voice memory drawers | Brand-voice corpus only 7 examples / 9 sources; memory keystone's brand-voice motivation isn't fed. CC12 (content→Studio handoff) is queued. | Medium — Studio is on the personal-distribution roadmap. |
| **Meetings / granola** | 1 meeting_summary, 0 raw_inputs, granola integration active + verified today | The meetings summarizer code imports `insert_raw_input` (line 481) but raw_inputs table is empty — that path may not actually fire in production despite being declared. | Medium — needs verification. |
| **Workflows** | 1 workflow, 3 runs | One workflow. Substrate broader; usage minimal. | Low |
| **Brief Builder / daily brief** | 3 brief_snapshots | Some firing. brief/sources.py wires memory retrieval (see memory audit). | Low |
| **Approvals** | 12 approvals | Substrate working; integration broader than current usage. | Low |
| **Slack ingestion** | 38 inbound messages, 1 user, 1 channel cached | Receiving but lean. No clear downstream consumer of slack messages identified. | Medium — what is this data used for? |

### 🟠 DORMANT — substrate exists, runtime usage zero

| Surface | Row count | Where the gap is |
|---|---|---|
| **Memory keystone** | 1 observation (audited separately) | P4 unstarted. See `docs/memory-audit-2026-05-29.md`. |
| **Skills system** | 1 skill, **0 agent_skills**, 0 skill proposals | No agent has any skill linked. Skill substrate exists; the "agents propose skills" path from the reference plan has never fired. |
| **Automations** | 0 automations, 0 automation_runs | 1,056 LOC of substrate, 539 LOC tests, zero production rows. |
| **Personal todos** | 0 rows | Table exists. Module unknown — possibly not wired in current UI. |
| **GCal events cache** | 0 rows | gcal integration active + verified; but no event ingestion has populated the cache. The calendar data isn't actually flowing into Artemis. |
| **Floating Artemis voice corpus** | 0 rows | Table exists for voice-input personalization; never written. |
| **Dev Projects** | 1 project, 1 session, **0 dev_messages** | 825 LOC of substrate. One session exists. Zero messages. Effectively unused. |
| **Connectors** | 0 connectors, 0 agent_connectors | Substrate exists (902 LOC) for connecting external systems; zero configured. |
| **Campaign briefs / deliverables / state transitions** | 0 briefs, 1 candidate, 1 deliverable | Campaign machinery substrate built, exercised barely. The "qualified signal → campaign brief" handoff hasn't matured. |
| **Raw inputs** | 0 rows | `meetings/summarizer.py:481` calls `insert_raw_input` — but the table is empty. Either the call site doesn't fire in prod, or it writes elsewhere. **Needs verification.** |

### 🔴 HOLLOW — declared shipped, loop demonstrably doesn't fire

| Surface | The hollow claim | The empirical refutation |
|---|---|---|
| **Self-improvement consumer side** | Producer fixed (CC10–CC18). Builder reads summaries (CC18 wires target_id). Inbox surfaces proposals (just landed). | `definition_proposals = 0`. **Engine.commit has never executed.** The first end-to-end test (operator chats with Builder → Builder calls propose) has not happened. |
| **Memory writes from agents** | Substrate complete, 11 tables, store/retrieval/consolidation all built | 0 drawers, 0 evidence, 1 observation (user-written manually). No agent code path writes to memory. See memory audit. |

---

## What's importing what — module health

Cross-module import graph for 22 backend modules (imports outside their own dir):

| Module | Imported by | Reading |
|---|---|---|
| `agent` | **88 files** | Core dataclasses. Universal. |
| `builder` | **57 files** | Heavily wired. |
| `marketing` | **51 files** | Pipeline core. |
| `builders` | **42 files** | Builder executor + models. Heavily wired. |
| `integrations` | **21 files** | Used. |
| `providers` | **19 files** | Used (claude-code adapter etc.) |
| `scouts` | 10 files | Used. |
| `ws` | 7 files | Writing Studio core. Used. |
| `tools` | 6 files | MCP tools. Used. |
| `memory` | **6 files** | Imported by only six call sites (per memory audit). Substrate is 5,800 LOC. |
| `floating_artemis` | 6 files | Used internally. |
| `pipelines` | 5 files | Used. |
| `meetings` | 3 files | Used. |
| `okr` | 3 files | Used. |
| `connectors` | 3 files | Substrate present, zero rows. |
| `writing_rules` | 3 files | Used. |
| `automations` | **2 files** | 1,056 LOC, two callers, zero rows. |
| `dev_projects` | **1 file** | 825 LOC, one caller. |
| `brief` | 1 file | Used. |
| `mcp` | 1 file | MCP server. Used by external Claude Code only. |
| `agents` | **0 files** | Module exists; nothing imports from it. **Audit follow-up.** |

The `agents` module (`artemis/agents/`) being imported by zero files is suspicious. Either it's the public package shim or it's dead code. Worth verifying.

---

## The five surfaces I want to audit deeply, in order

### 1. Self-improvement consumer end-to-end (HIGHEST PRIORITY — likely fix in <2hrs)

`definition_proposals = 0` means the loop has never closed. The fix may be as simple as me actually opening Builder, chatting "review this agent's recent runs and propose improvements," and seeing what happens. Either:
- It works and we just haven't exercised it → close immediately with a manual smoke
- It doesn't work and there's a 4th layer of hollowness → next surgical brief

This is the highest leverage spot-check.

### 2. Skills system (DORMANT)

Skills page exists. One skill in DB. Zero agent-skill links. The reference plan's "agents propose skills via Builder" path is the second half of the self-improvement loop. Need to verify:
- Does the Builder's `propose()` tool support `kind="skill"` (creating new Skills)?
- Does any agent's blueprint actually USE its linked skills at runtime?
- Is the Skills page UI just ornamental or is it backed by real DB queries?

### 3. Granola / Calendar / Slack inbound pipelines

Integrations are active but the data destinations are unclear:
- gcal: 0 events cached despite active integration verified today
- slack: 38 inbound messages, no clear consumer
- granola: 1 meeting summary, no raw_inputs

These integrations are connected but data isn't flowing into anything actionable. Need to verify whether they're intentionally one-way (Artemis reads on demand) or whether ingestion pipelines are broken.

### 4. Writing Studio + brand-voice handoff (CC12 territory)

Already partially audited (`docs/writing-studio-audit-2026-05-28.md`). Re-audit in light of memory audit findings — brand-voice memory was supposed to feed Studio. Today brand-voice drawers = 0.

### 5. The `agents` module (0 importers)

Quick verification — is `artemis/agents/` dead code or a package shim? If dead, prune.

---

## Recommended next-action sequence

**My recommendation (sequential, each one ~2hrs or less):**

1. **Self-improvement consumer manual smoke (NEXT, by Lead).** I open Builder on a real agent that has 3+ trajectory summaries (e.g. `marketing.qualifier.brief_composer` has 4), send a chat message asking for a review, and see whether `definition_proposals` gets a row. If yes → close this hollowness, mark task #21 (agent audit) completed. If no → next brief identifies the 4th-layer bug.

2. **Then fire M1 (memory: trajectory summary → memory observation).** ~80 LOC Worker brief. Once it lands, every future agent run produces real memory observations.

3. **Then Skills audit (deeper).** Same shape as memory audit. Probably reveals a SP2-shaped surface (Skills Playbook) that mirrors Josh's spec for skills.

4. **Then granola/slack/gcal data flow audit.** Quick taxonomic pass on whether these integrations have downstream consumers or are dormant connectors.

5. **Then writing studio re-audit + CC12.**

Steps 1 and 2 are critical-path. Steps 3-5 can stage behind them or be deprioritized in favor of forward-progress briefs (Signal Playbook, responsiveness) depending on Jon's call.

---

## Hard truths

Three patterns visible across all three hollowness layers (self-improvement, memory, now consumer-side proposals):

1. **The previous Opus session declared completion prematurely, repeatedly.** "Substrate complete" was said multiple times for things that were structurally complete but functionally inert. Every claim of "shipped" we inherit should be re-verified against DB row counts and runtime call paths.

2. **Tests passed without exercising integration.** Each hollow layer had test coverage in the 70%+ range. Tests covered units, not loops. The verification gap is "does the end-to-end loop fire when a real user interacts" — and that has never been part of CI.

3. **UI scaffolding outran integration.** Skills page, Memory shell, Inbox panel, Dev Projects, Automations all exist as frontend surfaces, all rendered something, all gave the impression of "we have this." The DB row counts show what's actually exercised.

**The discipline going forward:** before declaring any surface "shipped," verify (a) production runtime call path, (b) DB row count > 0 from a real user interaction, (c) at least one integration test that drives the full loop. The CC10–CC18 stream proved this discipline produces real progress quickly.

---

## Open question for Jon

Should I do the self-improvement consumer manual smoke (step 1) right now? It's me sending one chat message in the Builder for ~5 minutes. If proposals start landing, three hollownesses close in one afternoon (producer + consumer + the memory M1 brief becomes the next surgical move). If they don't land, we've found the 4th layer in another ~30 minutes of digging.
