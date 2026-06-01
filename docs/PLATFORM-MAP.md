# Artemis OS — Platform Map

**Living doc. Last updated:** 2026-05-30 LATE
**Companion docs:** `SITE-MAP.md` (UI nav specifics), `ROADMAP-2026-05-30.md` (forward plan), `LEAD-SESSION-LOG.md` (decision trail), `ARTEMIS-OS-MASTER-PLAN.md` (philosophy).

---

# 🎯 If you are an LLM picking up this codebase cold — read this section first

Artemis OS is **a general-purpose agent operations platform** built inside Amira Learning. Marketing intelligence is its first proving ground — the first vertical demonstrating that specialized AI agents can do real work, learn from their own runs, and propose their own improvements to a human reviewer. **The marketing agents are seed data, not features.** The platform is general.

**The product is the Agent-Builder pattern:** users describe what they want via conversation; a Senior-Engineer-class builder agent designs the definition with them; the user commits it; the system runs it and watches itself work. Over time, the system proposes improvements to its own definitions based on what it observed.

**Three load-bearing invariants** (never violate):

1. **Subscription-only by default.** Claude Code subscription path for autonomous runtime. No per-token Anthropic API cost in pipeline agents. Interactive Builder may use API for tool-use semantics; everything else is subscription.
2. **Lossless memory by structure.** Drawers + observations are never deleted. Supersession via `superseded_by` only. The full evidence chain back from any decision to its sources is preserved.
3. **No hallucinations.** Every JSON-emitting LLM surface has Pydantic validation. Every tool input is schema-validated with self-teaching errors. Every Builder proposal is grounded against actual schemas/enums before submission.

**The self-improvement loop is structurally complete (verified in production 2026-05-30):**

```
Agent runs (under definition)
  → trajectory_summarizer captures what happened (M1 → memory)
  → operator opens Builder for that agent (CC18 wires target_id)
  → Builder reads recent runs (CC19 MCP tools) + grounds against schema (CC20) + searches memory (M2)
  → Builder proposes definition update (H1-H5 anti-hallucination guarantees)
  → operator approves OR rejects (MC1 / CC29 carryover writes observation back to memory)
  → engine.commit() applies the change durably (proven twice in production)
  → next run uses the updated definition (Run #340 empirically showed improvement)
```

The platform learns from its own runs. Operator job is reviewing proposals, not doing the work.

---

# Health legend (used throughout this doc)

- ✅ **Healthy** — substrate present, runtime integrated, UI visible, real data, tests exercise the integration
- 🟡 **Shallow** — wired but sparse usage or thin in one dimension
- 🟠 **Dormant** — substrate built but no production runtime callers
- 🔴 **Hollow** — substrate present + declared complete, but the loop demonstrably doesn't fire (the bug pattern we spent the session closing)
- 🚫 **Deprecated** — being sunset; do not extend; prefer the replacement

---

# Per-surface map (UI tabs)

Left rail navigation. Each entry: what it does · current status · backend routes · DB tables · key dependencies.

## Personal Workspace tier

| Surface | Status | What it does | Backend | Tables | Dependencies |
|---|---|---|---|---|---|
| **Focus** | 🟡 | Daily brief landing — what to focus on | `/api/brief/*` | `brief_snapshots` | Jira, GCal, Slack, OKR, memory |
| **Calendar** | 🟡 | GCal-synced day/week/month view | `/api/calendar/*` | `gcal_events_cache` (0 rows — sync not actively flowing) | GCal OAuth |
| **Meetings** | 🟡 | Granola-summarized meetings | `/api/meetings/*` | `meeting_summaries` (1 row), `raw_inputs` (writes from `meetings/summarizer.py:481`) | Granola OAuth, meeting_summarizer (H4 Pydantic) |
| **Jira Board** | ✅ | Read-only Jira board | `/api/jira/*` | None (proxy) | Jira REST API |
| **OKR Studio** | ✅ | Personal OKR composition + tracking | `/api/okr/*` | `okr_objectives` (4), `okr_key_results` (20), `okr_activity` (30) | None |

## Operations tier (where agents/skills/pipelines/memory are BUILT)

| Surface | Status | What it does | Backend | Tables | Dependencies |
|---|---|---|---|---|---|
| **Automations** | 🚫 Deprecated (PIPE6) | Legacy scheduled triggers | `/api/automations/*` | `automations`, `automation_runs` (both 0 rows) | **PIPE6 sunsets this tab** — D6 lock: migrate to Pipelines with trigger nodes |
| **Skills** | 🟡 | Curated capabilities for agents | `/api/builder/skills/*` | `skills` (1 row), `agent_skills` (0 rows) | Builder Engine; MC3 carryover on approve |
| **Pipelines** | ✅ | DAG-based pipeline runs (Marketing pipeline lives here) | `/api/pipelines/*` | `pipelines` (1), `pipeline_runs` (37+) | CC7 dispatch, CC8 run-lock, MCP tools, scout/qualifier/content agents |
| **Agents** | ✅ | Agent roster + Builder (Agent-Builder is the front door for editing) | `/api/agents/*`, `/api/builder/*` | `agents` (18), `agent_runs` (290+), `agent_run_trajectory_summaries` (35+) | CC10-CC18 (loop), CC19 (MCP), CC20 (grounding), M2 (memory) |
| **Workflows** | 🚫 Deprecated (PIPE6) | Legacy sequential recipes | `/api/workflows/*` | `workflows` (1), `workflow_runs` (3) | **PIPE6 sunsets this tab** — D6 lock: migrate to Pipelines with sequential edges |
| **Memory** | ✅ | Memory shell — observations + drawers + evidence chains + scopes | `/api/memory/*` | 11 memory tables (see Memory section below) | M1-M6, MC1-MC5, MW1, CC27, CC28 |

## Marketing tier

| Surface | Status | What it does | Backend | Tables | Dependencies |
|---|---|---|---|---|---|
| **Dashboard** | 🟡 | Marketing KPIs (audit pending) | `/api/marketing/*` | various | Pipeline + signals |
| **Writing Studio** | 🟡 | Brand voice + outbound drafting (handoff broken — CC12 pending) | `/api/marketing/writing-studio/*`, `/api/marketing/writing-rules/*` | `writing_profiles` (1), `writing_rules` (2), `writing_examples` (7), `writing_sources` (9) | Content composer agent; CC12 closes pipeline→Studio handoff |
| **Campaigns** | 🟡 | Campaign definitions (sparse) | `/api/marketing/campaigns/*` | `campaigns` (3?), `campaign_briefs` (0), `campaign_deliverables` (1) | Pipeline output destination |
| **Signals Inbox** | ✅ | Gate 0 — pre-qualified signals from scouts | `/api/marketing/signal_queue/*` | `signal_queue` (200+), `signal_reason_codes` | Scouts emit; qualifier reads |
| **Approval Queue** | ✅ | Gate 1 — composed briefs awaiting review | `/api/marketing/approvals/*`, `/api/marketing/signal_queue/{id}/approve` | `signal_briefs`, `approvals` (12) | Brief composer agent; MC2 carryover on approve |

## Dev Projects tier

| Surface | Status | What it does |
|---|---|---|
| **Dev Projects** | 🟠 Dormant | REPL-style code workspace (1 project, 0 messages) |

---

# Background piping (the wiring that's NOT in the UI)

These are the async / scheduled / cross-surface pipes that operate behind the rendered tabs.

## The trajectory chain (CC10-CC18 + M1)

```
agent_run completes (via pipeline executor OR Builder.handle_turn)
  ↓
trajectory_summarizer.create_trajectory_summary (async task, CC10 retains ref to prevent GC)
  ↓ LLM call (H3 Pydantic-validated output)
  ↓ writes to agent_run_trajectory_summaries (CC11 brace fix, CC13 pass-data, CC14 fire-after-commit)
  ↓
M1: also writes to memory_observations scoped agent:<id> with evidence→agent_run
  ↓
M2: when operator opens Builder for that agent, search_memory grounds Builder reasoning
```

**Where it fires from:** every agent_run terminal status (succeeded / failed). Fire-and-forget via `summarize_async`, retained in `_TASKS` set to prevent GC orphan.

**Where it can fail:** the H3 Pydantic gate. If LLM output is malformed, retry-with-error once; if persistent, empty observation lands with NULL fields preserving audit row.

## The Memory Carryover chain (MC1-MC5 + CC29)

```
Operator approves OR rejects via UI/API on any of these surfaces:
  - Definition proposal approve   → MC1 fires (write_proposal_approval_observation)
  - Definition proposal reject    → CC29 fires (write_proposal_rejection_observation)
  - Gate 1 signal/brief approve   → MC2 fires (write_signal_gate1_approval_observation)
  - Skill promotion approve       → MC3 fires (write_skill_promotion_observation)
  - Pipeline human-gate decision  → MC4 fires (write_pipeline_gate_decision_observation)
  - FA tool-driven approval       → MC5 fires (write_fa_marketing_approval_observation)
  ↓
Each helper writes ONE observation with multi-scope (primary + workspace:platform audit)
  via _multi_scope_observation_write shared helper in memory_carryover.py
  ↓
Evidence links: source row (definition_proposal | signal_queue | skill | pipeline_run | floating_artemis_messages)
                + cited agent_run sources
```

**Failure isolation:** every carryover wraps in try/except. Memory write failure CANNOT break the approval endpoint. The DB status flip + engine.commit() are durable; memory is additive.

## The MCP tool execution chain (CC1, CC2, CC19)

```
Pipeline agent OR Builder needs to call a tool
  ↓
ClaudeCodeAdapter.complete() (CC19: routes through MCP when tools present)
  OR ClaudeCodeAdapter.run_with_tools() (CC2: pipeline agents)
  ↓
spawns claude -p --mcp-config <tmp.mcp.json> --strict-mcp-config
  ↓
claude-code subprocess invokes Artemis MCP server (artemis/tools/mcp_server.py)
  - --agent-id + --run-id  → agent-run-scoped tools (CC1)
  - --builder-session-id   → Builder-scoped tools (CC19: builder_propose, builder_read_recent_runs,
                              builder_search_memory, builder_read_tool_signatures,
                              builder_read_db_schema, builder_read_skill_catalog)
  ↓
Tool implementations execute in subprocess; results stream back via stdio
  ↓
CC17: every successful invocation logged to tool_invocations
  (CC21: now supports builder_session_id scope via XOR CHECK constraint)
  ↓
H1: validation errors are self-teaching (enumerate valid enum values)
```

## The anti-hallucination chain (H1-H5)

```
Every JSON-emitting LLM surface validates output before persistence:
  - H1: tool input_schema enforcement at ToolRegistry layer (self-teaching errors)
  - H2: scout intake (scout_runner) → ScoutEmittedSignal Pydantic + reason_code allowlist
  - H3: trajectory_summarizer → TrajectorySummary Pydantic + retry + provenance framing
  - H4: meeting_summarizer → MeetingSummary Pydantic + retry + FA provenance framing
  - H5: brief_generator → DailyBrief Pydantic + retry (IN FLIGHT)
  - H5: pipelines/assistant → PipelineProposal Pydantic (IN FLIGHT)

Per-LLM-output discipline: max_length on all strings, extra="forbid" on all models,
                          retry-on-failure with error in next prompt (autonomous surfaces),
                          provenance framing when output is consumed by another LLM
```

## The agent self-improvement loop (the closed cycle)

```
Pipeline triggers (manual or cron) → Pipeline executor walks DAG
  ↓
Each agent node: agent_run created, agent_executor invokes the agent
  ↓ (via MCP path)
Agent calls tools (signal_queue.write, signal_queue.update_status, signal_briefs.write, etc.)
  ↓ (H1 self-teaching errors when invalid input; agent self-corrects next turn)
Agent run completes → trajectory_summarizer captures patterns (M1 writes to memory)
  ↓
At Gate 1: brief_composer writes signal_briefs; operator reviews in Approval Queue
  ↓ Operator approves → MC2 carryover writes observation
  ↓
[Later, optionally] Operator opens Builder for agent (CC18 target_id)
  ↓ Builder reads recent runs via read_recent_runs (CC19 MCP)
  ↓ Builder grounds against schemas via read_tool_signatures/read_db_schema/read_skill_catalog (CC20)
  ↓ Builder searches memory via search_memory (M2) — sees full agent history not just last 10 runs
  ↓ Builder proposes definition update (H1-H5 validation guarantees no hallucinated facts)
  ↓
Operator reviews proposal in Proposals Inbox UI
  ↓ Approves → MC1 carryover writes observation; engine.commit() applies durably
  ↓ Rejects → CC29 carryover writes observation with reason captured
  ↓
Next pipeline run uses updated definition (verified empirically: brief_composer #340 vs #329/318/275)
```

---

# Memory architecture (the keystone)

Memory is the substrate that lets the platform reason across its own history. As of 2026-05-30 LATE, the keystone is structurally complete.

## Memory state (live)

```
31 observations · 10 drawers · 49 evidence links · 17 scopes · 32 obs_scopes (MW1 join)
```

## Memory tables (11 of them)

| Table | What | Status |
|---|---|---|
| `memory_drawers` | Verbatim, immutable evidence floor | ✅ M5 writes signal genealogy |
| `memory_observations` | Curated summaries (the read layer) | ✅ M1/M5/M3/MC1-MC5/CC29 all write |
| `memory_evidence` | Many-to-many: observation ↔ source. `source_id TEXT` (CC28) | ✅ |
| `memory_scopes` | (scope_kind, scope_id) — the universal handle. 12 scope kinds (CC27) | ✅ |
| `memory_observation_scopes` (MW1) | NEW many-to-many join: obs ↔ multiple scopes | ✅ MW1 |
| `memory_embeddings` | Vector embeddings for semantic search. pgvector. | ✅ CC26 fixed serialization |
| `memory_entities` + 3 sister tables | Entity graph (P3 substrate) | 🟠 Dormant — entities=0; extraction wired but not run on real data |
| `memory_relations` + `memory_relation_rejections` | Entity relations (P3 substrate) | 🟠 Dormant |
| `memory_conflicts` | Detected contradictions between observations | 🟠 Dormant — substrate exists, not surfaced in UI yet (MW3 task) |

## Scope kinds (12 values after CC27)

`project | workspace | brand | agent | skill | global | pipeline | district | account | person | meeting | personal`

Last 6 were added by CC27 to prepare for Salesforce/ChurnZero/Gong integration + personal-instance distribution.

## Evidence source kinds (9 values after CC23)

`drawer | observation | agent_run | signal_queue | definition_proposal | pipeline_run | skill | floating_artemis_messages | meeting`

## Memory write sources (active)

| Source | What it writes | Scope | Confidence origin |
|---|---|---|---|
| M1 trajectory | post-run summary observation | agent:<dotted_id> | `m1_trajectory` |
| M3 FA conversation | verbatim turn drawer | agent:floating-artemis | (drawer, no obs) |
| M5 signal qualification | drawer + observation | workspace:marketing | `m5_signal_qualification` |
| MC1 proposal approve | observation (multi-scope) | agent:<id> or skill:<slug> + workspace:platform | `mc_definition_proposal` |
| MC2 Gate 1 approve | observation (multi-scope) | workspace:marketing + workspace:platform | `mc_signal_gate1` |
| MC3 skill promote | observation (multi-scope) | skill:<slug> + workspace:platform | `mc_skill_promotion` |
| MC4 pipeline gate decide | observation (multi-scope) | pipeline:<id> + workspace:platform | `mc_pipeline_gate` |
| MC5 FA marketing approve | observation (3-scope) | agent:floating-artemis + workspace:marketing + workspace:platform | `mc_fa_marketing` |
| CC29 proposal reject | observation (multi-scope) | agent:<id> or skill:<slug> + workspace:platform | `mc_definition_rejection` |
| FA `_write_memory` tool | observation (user-directed) | agent:floating-artemis (default) | `fa_write_memory` |
| Meetings summarizer (H4) | raw_inputs entry | (raw_inputs table) | n/a |

## Memory read sources (active)

| Reader | What it reads | When |
|---|---|---|
| M2 Builder grounding | `search_observations(scope=agent:<id>)` | Every Builder edit session, before propose |
| M4 FA prompt build | `search_observations(scope=agent:floating-artemis, query=user_msg)` | Every chat turn, 5s cache |
| brief/sources.py | `search_observations(scope=global, query="work priorities")` | Daily brief generation |
| MCP memory server | `memory_search`, `memory_get_observation`, etc. | External Claude Code clients |
| HTTP routes `/api/memory/*` | Memory shell UI queries | Operator browse |
| floating_artemis tools/core.py | `_query_memory` tool (user-callable) | FA chat when user asks |

---

# DB tables inventory (active surfaces)

Quick scan of which tables are alive vs dormant. Counts approximate from session-end.

| Active (✅) | Light (🟡) | Dormant (🟠) |
|---|---|---|
| agents (18) | writing_profiles (1) | automations (0) |
| agent_runs (290+) | writing_rules (2) | automation_runs (0) |
| agent_run_trajectory_summaries (35+) | writing_examples (7) | personal_todos (0) |
| signal_queue (200+) | writing_sources (9) | gcal_events_cache (0) |
| tool_invocations (358+) | meeting_summaries (1) | dev_messages (0) |
| pipeline_runs (37+) | skills (1) | floating_artemis_voice_corpus (0) |
| pipelines (1) | agent_skills (0) | campaign_briefs (0) |
| floating_artemis_sessions (102+) | campaigns | memory_entities (0) |
| floating_artemis_messages (82+) | brief_snapshots (3) | memory_relations (0) |
| okr_objectives (4) | workflows (1) | raw_inputs (0) — meetings DOES call insert_raw_input |
| okr_key_results (20) | workflow_runs (3) | dev_projects (1) |
| okr_activity (30) | slack_users (1), channels (1) | connectors (0) |
| definition_proposals (6) | slack_inbound_messages (38) | |
| memory_observations (31) | | |
| memory_drawers (10) | | |
| memory_evidence (49) | | |
| memory_scopes (17) | | |
| memory_observation_scopes (32, MW1) | | |
| integrations (4 active: jira/slack/gcal/granola) | | |
| builder_sessions (15) | | |

---

# Module health (by `artemis/<module>/`)

| Module | LOC | Status | Notes |
|---|---|---|---|
| `artemis/marketing/` | 8149 | ✅ Healthy | Pipeline core, 11 scouts, qualifier, content composer, Josh spec parser (F1) |
| `artemis/routes/` | 6635 | ✅ Healthy | FastAPI route layer |
| `artemis/scouts/` | 5972 | ✅ Healthy | Per-scout client implementations |
| `artemis/pipelines/` | 4776 | ✅ Healthy | DAG executor, run-lock, dispatch durability |
| `artemis/memory/` | 4459 + 4279 tests | ✅ Healthy | Keystone — every layer wired this session |
| `artemis/integrations/` | 4408 | 🟡 Light | Granola, GCal, Slack, Jira active; ingestion paths sparse |
| `artemis/floating_artemis/` | 4172 | ✅ Healthy | M3+M4 memory awareness wired |
| `artemis/tools/` | 3076 | ✅ Healthy | MCP server, tool registry, signal_queue ops |
| `artemis/builders/` | 2748 | ✅ Healthy | Builder Engine, definition_proposals, MC1+CC29 hooks |
| `artemis/builder/` | 2443 | ✅ Healthy | Agent-Builder + memory_carryover module |
| `artemis/providers/` | 2144 | ✅ Healthy | claude-code/anthropic/codex/gemini/openai/openrouter/lm-studio adapters |
| `artemis/automations/` | 1056 | 🚫 Deprecated (PIPE6) | 0 production rows; D6 lock — sunset + auto-migrate to Pipelines (PIPE6 stream) |
| `artemis/connectors/` | 902 | 🟠 Dormant | Substrate present, 0 configured connectors |
| `artemis/dev_projects/` | 825 | 🟠 Dormant | 1 project, 0 messages |
| `artemis/writing_rules/` | 800 | 🟡 Light | Studio surface alive, handoff broken (CC12) |
| `artemis/agent/` | 681 | ✅ Healthy | Shared agent types/tools/loop |
| `artemis/meetings/` | 669 | 🟡 Light | H4 Pydantic gate wired; granola flowing sparsely |
| `artemis/okr/` | 644 | ✅ Healthy | Personal scope working; team/org expansion banked |
| `artemis/brief/` | 544 | 🟡 Light | Daily brief firing; H5 Pydantic in flight |
| `artemis/ws/` | 425 | 🟡 Light | Writing Studio routes |
| `artemis/mcp/` | 425 | ✅ Healthy | External Claude Code MCP server (read-only memory exposure) |

---

# Active streams (forward work)

Per `docs/ROADMAP-2026-05-30.md`:

| Stream | LOC | When |
|---|---|---|
| ~~**H5 — Daily Brief + Pipeline AI Panel Pydantic**~~ | ~~~180~~ | ✅ Merged at 67719fd (anti-hallucination stream now structurally complete across all 7 LLM-emit sites) |
| ~~**SP1 + SP2** combined into single SP brief~~ | ~~~500~~ | ✅ Merged 2026-05-30 (commit 69876bc via Codex). Migration 0052. Signal Playbook editor live. |
| ~~**PIPE6 — Workflows + Automations sunset + auto-migrate to Pipelines**~~ | ~~~400 + migration 0053~~ | ✅ Merged 2026-05-30 EOD (commit c25eb4e via Codex). D6 lock executed. |
| **CC12 — Writing Studio content-agent handoff** | ~200 | After PIPE6 OR parallel (different surface) |
| **Marketing flow audit** (Dashboard / Campaigns / Approval Queue) | TBD | After CC12 |
| **MW2-MW4 — Memory Wings UI** | ~750 | After ~4 weeks of memory data |
| **SH1-SH5 — Platform Stewardship** | ~1100 | After MW + 4 weeks data — design locked in `docs/platform-stewardship-design-2026-05-30.md` |
| **Responsiveness Phase 1 (B+A)** | ~130 | When daily-use latency is a friction |
| **Personal-instance distribution** | TBD | Quarter+ |
| **Salesforce / ChurnZero / Gong integration** | TBD | Quarter+ (substrate ready: CC27 scopes, CC28 source_ids) |

---

# Banked findings (not blockers, fix when convenient)

- **CC34** — scout_federal_funding emitted 0 rows in CC14 smoke; investigate
- **`int(ev.source_id)` cast audit** — repository.py memory preview lookups (analyzed safely scoped, banked for re-read)
- **Test DB rebuild discipline** — after parallel-Worker rounds, drop+recreate `artemis_test` to clear contamination
- **Migration number coordination** — parallel briefs both declaring `down_revision="0048"` (happened twice this session) — assign numbers upfront in future parallel migration briefs
- **Sidebar Memory badge** doesn't auto-refresh on memory changes (cosmetic, banked)
- **`_LEGACY_HASHED_OBSERVATION_IDS`** — obs #29/30/31 from pre-CC28 smokes have SHA-256 hashed source_ids (lossless, documented, no fix needed)
- **`_link_evidence_raw` helper** in memory_carryover.py can be removed now that CC23 extended Literal (tiny cleanup)
- **Inbox UI placement fix** (`f6ab956` in worktree) — deferred to UI pass
- **`tool_invocations` for agent_run-scoped tools may have schema mismatch with `agent_run_id` text vs bigint** — verify when next touching

---

# Cross-cutting flows worth knowing about

## The marketing pipeline (the most-exercised flow)

```
trigger_scheduled (cron OR manual) → pipeline_run created
  ↓
scout_state_doe, scout_legislative, scout_procurement, scout_board_minutes,
scout_regional_news, scout_federal_funding, scout_linkedin_observer,
scout_leadership_transition, scout_starbridge_researcher (parallel)
  ↓ each emits signals to signal_queue (signal_status=pending_qualification)
  ↓ trajectory_summarizer captures each scout's run → M1 writes memory observation
  ↓
qualifier_cross_reference: applies Josh's spec rules (F1 parser) to each signal
  ↓ updates signal_status to qualified / rejected_hard_filter / suppressed_stale
  ↓ M5 fires on each qualified transition: writes drawer + observation + 2 evidence links
  ↓
qualifier_brief_composer: composes Gate 1 briefs for qualified signals (post-CC19 grounded)
  ↓ writes signal_briefs rows
  ↓
gate_1_signals_inbox: pipeline suspends (status=awaiting_approval) — operator review
  ↓ Operator approves brief in Approval Queue UI
  ↓ MC2 fires: writes observation to workspace:marketing + workspace:platform
  ↓
[Future: content composer + Writing Studio handoff via CC12]
```

## The self-improvement loop (the closure cycle)

See top section. Verified end-to-end in production: Run #340 successfully processed 7 signals using brief_composer's post-Proposal-#4 prompt (md5 `2cfeaa06...`), then Builder used M2 + CC20 to propose #5 + #6, operator approved #5 (md5 changed to `39dfcc3b...`), CC29 ready for any future rejections.

## The Builder grounding stack (CC18-CC20 + H1-H5)

When operator opens Builder for an agent:
1. CC18 wires `target_id` → Builder knows which agent
2. CC19's MCP tools become callable (read_recent_runs, propose, etc.)
3. CC20's grounding tools surface real schemas: `read_tool_signatures`, `read_db_schema`, `read_skill_catalog`
4. M2's `builder_search_memory` returns curated observations from `agent:<id>` scope (full history, not just last 10 runs)
5. Builder's system prompt mandates grounding before propose() call
6. Propose tool: H1-H5 Pydantic validates every input + every emitted JSON
7. Citation validation: only `run_ids` returned by `read_recent_runs` in this session can be cited
8. Proposal lands in `definition_proposals` with `kind` + `target_id` + `proposed_definition` + `citations`
9. Operator reviews in Inbox; approves → MC1 carryover + engine.commit; rejects → CC29 carryover

---

# Locked Decisions Ledger

**Purpose:** every architectural decision ever made in this platform, with current execution status. **Audit this section BEFORE drafting a new plan** to avoid dropping locked work onto the floor (the failure mode Jon flagged: "we draft a new plan and forget what was still outstanding from the old one").

## Master-plan D-decisions

| Decision | Locked | What | Execution status |
|---|---|---|---|
| **D1** Builder-first creation, forms-supplemental | original master plan | Every meta-object gets a Builder; forms remain for power users / quick edits. Builder is the front door. | ✅ **Done** — Agent Builder + CC18-CC20 + MC1 + memory grounding all live. Empirical kill-criterion passed 2026-05-20. |
| **D2** Self-improvement integration mandatory | original master plan | Every shipped agent ships with trajectory summary generation + Builder-readable history. | ✅ **Done** — CC10-CC18 + M1 + M2 wired. Verified Run #340 used Proposal-#4-updated prompt successfully. |
| **D3** Ruleset versioning append-only from day 1 | original master plan | `campaign_ruleset_versions` ships as separate append-only table (port from Node slice 13). | 🔴 **OUTSTANDING** — table doesn't exist in current DB. `rulesets` table is single-version only. Invariant I-21 not structurally enforced. **Task #76 added.** |
| **D4** HMH partner flag is operator-mutable | original master plan | `district_marketing_flags` table where operators flag districts as HMH-partner / skip / etc. Salesforce-ready when SF integration ships. | 🔴 **OUTSTANDING** — table doesn't exist. Hardcoded district lists still fragile. **Task #77 added.** |
| **D5** Scout agent runtime is M5b, not M5 | original master plan | Definitions (M5) + runtime (M5b) separated. | ✅ **Done** — all 11 scouts shipping with runtime. |
| **D6** Pipeline is unified orchestration primitive | original master plan | Workflows, Chains, DAGs, and Automations all reduce to one `Pipeline` concept. Existing primitives auto-migrate (PIPE6) or sunset. | ✅ **Done 2026-05-30 EOD** — PIPE6 merged (c25eb4e via Codex). Migration 0053. 1 workflow auto-migrated. Routes return 410. Sidebar tabs removed. |
| **D6.1** Pipelines belong to Operations, not domain tabs | original master plan | Pipelines under Operations alongside Agents/Skills/Memory. Domain pages deep-link in; never own their own pipeline tabs. | ✅ **Done** — current left rail respects this. |
| **D7** Signal Playbook: Marketing UI over Josh's spec | 2026-05-26 | Marketing UI for Josh/Anne Marie to view + edit signal criteria without a deploy. | ✅ **Done 2026-05-30** — combined SP brief shipped via Codex (commit 69876bc). Migration 0052. UI live. |

## Memory design D-decisions (locked 2026-05-29)

13 decisions from `docs/memory-shell-vision-2026-05-29.md`. All locked.

| Decision | What | Execution status |
|---|---|---|
| **D1 (mem)** Default wing per source | M1/M5/MC=durable; FA conversation=working with auto-promote at hit_count≥3 | ✅ **Done** — MW1 schema + MC1-MC5 helpers + FA writes |
| **D2 (mem)** Attention bands KILLED | Use existing score/hit_count/confidence instead | ✅ **Done** — never added attention_band column |
| **D3 (mem)** Carryover writes multi-scope | Every approval writes to primary + `workspace:platform` audit | ✅ **Done** — MC1-MC5 via MW1 join table |
| **D4 (mem)** Sort options replace attention filter | Recent/Most Referenced/Highest Confidence/Score | 🟡 **Partial** — UI filter chips not yet implemented (MW2-MW4 territory) |
| **D5 (mem)** Entities in MW3 detail pane | Chips on each observation; defer browse modal | 🟡 **Deferred** — MW3 not yet shipped; `memory_entities` table still empty (0 rows) |
| **D6 (mem)** Observations multi-scope (many-to-many) | Join table `memory_observation_scopes` | ✅ **Done** — MW1 migration 0048 + 32 join rows present |
| **D7 (mem)** Builder reads increment hit_count | `search_observations` updates accessed_at | ⚠️ **Verify** — claim is `search_observations` already does this; not empirically confirmed |
| **D8 (mem)** Conflicts surface in MW3 detail pane | `memory_conflicts` substrate gets banner UI | 🟡 **Deferred** — MW3 not yet shipped; conflict_detector still unused in UI |
| **D9 (mem)** No automatic aging | Operator-driven `valid_until` field; lossless preserved | ✅ **Done by design** — `valid_until` column exists; UI uses it for default-hide |
| **D10 (mem)** Scope IS the privacy boundary | `personal:` scope kind for private memory | ✅ **Done** — CC27 added `personal` to ScopeKind Literal |
| **D11 (mem)** Superseded default-hide in UI | `WHERE superseded_by IS NULL` default | ⚠️ **Verify** — check M6 filter logic includes this |
| **D12 (mem)** Confidence per-source defaults | M1=1.0 if 3 fields, MC=1.0, FA write=1.0, FA chat=0.5 | ✅ **Done** — `confidence_origin` column tracks source; defaults per helper |
| **D13 (mem)** Backward-compat trivial | Existing rows backfilled cleanly to multi-scope | ✅ **Done** — MW1 backfill verified lossless |

## Discipline lessons codified

- ✅ "Substrate complete" ≠ "behavior complete" — verify runtime + DB rows + end-to-end loop
- ✅ Workers' self-reports are claims; spot-check critical ones
- ✅ Parallel briefs need migration-number coordination + file-overlap analysis
- ✅ Test DB contamination from parallel workers — rebuild after parallel rounds
- ✅ Provenance framing on LLM-content consumed by other LLMs
- ✅ Self-teaching error messages on tool validation (H1 pattern)
- 🆕 **Audit this Locked Decisions Ledger before drafting any new plan** (added 2026-05-30 LATE after Jon caught D6/PIPE6 drift)
- 🆕 **When a brief introduces Pydantic validation on LLM-emitted JSON, the brief MUST audit upstream prompt files** (added 2026-05-30 LATE after H5 missed `brief/prompt.py` — Worker correctly stayed in scope, but the brief's file ownership was incomplete; net result: LLM emitted old shape, Pydantic rejected, briefs would have gone empty in prod). Future H-style briefs include the prompt-emitting file in scope and verify the prompt template references the new field names + enums explicitly.

## Update protocol for this ledger

- When a stream lands → flip status from outstanding/queued to Done
- When a new decision is locked → append to relevant table with locked-date
- When a banked finding becomes a task → reference its task number
- Re-verify ⚠️ entries quarterly

---

# Operating discipline (Lead principles, proven this session)

1. **Never assume — verify directly.** DB query, runtime invocation, browser smoke. "Substrate complete" is never sufficient.
2. **Worker self-reports are claims, not evidence.** Re-verify critical claims. Branch existence is also a claim — wait for the relay before merging.
3. **When you find a gap, ask "what's the elegant solution," not "what's the smallest patch."**
4. **Bias toward "what's the right shape?" over "what's the quickest fix?"**
5. **Lossless invariant is load-bearing.** Don't violate it for convenience.
6. **Failure isolation on additive layers.** Memory writes, carryover writes, observability — never break the primary operation.
7. **Parallel briefs need migration-number coordination AND file-overlap analysis** before firing.
8. **Test DB contamination** is real with parallel workers — rebuild after parallel rounds.
9. **Provenance framing** on LLM-generated content consumed by other LLMs (H3/H4 pattern).
10. **Self-teaching error messages** in tool validation (H1 pattern) — runtime hallucinations become single-turn-recoverable.
11. **Brief = model tier.** Every Codex/Worker brief states a recommended model + reasoning effort (see `briefs/CONVENTIONS.md`). Match the tier to the *reasoning* the task needs, not its importance — fully-specified work runs on `gpt-5.4-mini`/low; ambiguous debugging on the flagship/high. The Lead front-loads the thinking into the brief so a cheaper model can execute it correctly.
12. **Scheduled agentic runs execute out-of-process.** The scout scheduler stays an in-process APScheduler timer, but every cycle runs as a `python -m artemis.marketing.scout_cli <agent_id>` subprocess (see `artemis/marketing/scout_scheduler.py`). The web process never spawns `claude` directly — when the child exits, the OS reaps its claude grandchild + every subscription-adapter semaphore. Established 2026-06-01 after #102: an orphaned `claude worker` from an in-loop `run_agent` call took down the FastAPI app.

---

# How this doc stays current

**Each section dated when meaningfully updated.** Sections older than 2 weeks should be re-verified before relied on (especially health classifications + table row counts).

**Update protocol:**
- Stream lands → update Active streams + Module health + Memory state if relevant
- Audit completes → update relevant Per-surface map row + relevant Module health
- Architectural decision → update Three invariants section if it changes load-bearing behavior
- New banked finding → append to Banked section

**Companion docs that go deeper:**
- `docs/ROADMAP-2026-05-30.md` — forward plan, brief sequences
- `docs/LEAD-SESSION-LOG.md` — decision trail, rollover continuity
- `docs/ARTEMIS-OS-MASTER-PLAN.md` — philosophical anchor
- `docs/memory-shell-vision-2026-05-30.md` — locked memory design decisions
- `docs/platform-stewardship-design-2026-05-30.md` — Stewardship stream design
- `docs/builder-responsiveness-design.md` — Responsiveness Phase 1+2 design
- `docs/hallucination-audit-2026-05-29.md` — anti-hallucination architectural layer
- `docs/pipeline-authoring-principles.md` — durability + boundary principles
- `docs/SITE-MAP.md` — UI navigation specifics

---

# 🎯 For the LLM picking up cold — your first-30-minutes checklist

1. **Read this doc** (you just did)
2. **Read `docs/LEAD-SESSION-LOG.md` top section** — current state + recent decisions
3. **Run `git log --oneline -20 lead/j6a-granola-integration`** — see the recent merge train
4. **Run `uv run alembic current`** — should match migration head listed in session log
5. **Run quick DB query: `psql -d artemis_os -c "SELECT 'observations', COUNT(*) FROM memory_observations;"`** — sanity-check memory state matches doc
6. **Read `docs/ROADMAP-2026-05-30.md`** — see forward plan + what's queued
7. **If a stream is in flight, read its brief** in `briefs/`
8. **If unsure about a surface's status, check this doc's Per-surface map** before assuming substrate completeness

**The pattern you want to avoid:** picking up a stream, building on what you assume works, discovering 6 layers of hollowness because nothing told you what was actually wired. That's the pattern this doc exists to prevent.

When in doubt: query the DB. Trace from claim to evidence.
