# Hallucination Audit — Every LLM-Driven Surface in Artemis OS

**Date:** 2026-05-29
**Auditor:** Lead (Opus 4.7), with empirical spot-checks against codebase + DB
**Status:** Comprehensive enumeration of every LLM call site outside `artemis/providers/`. Risk classified on validation + pollution-chain dimensions.
**Trigger:** CC19's smoke landed proposals containing hallucinated state names. Jon: "hallucinations cannot happen on this app with any of the builders and agents." This audit maps the full risk surface before any anti-hallucination brief fires.

---

## Method

Each LLM-driven surface scored on six dimensions:

1. **What the LLM emits** — text only? structured JSON? tool calls? definitions?
2. **What facts it reasons about** — enums, IDs, schemas, recent runs, memory
3. **Grounding available** — does the LLM have tools/data to verify facts before emitting?
4. **Runtime validation** — what catches malformed/hallucinated output before persistence?
5. **Error message quality** — self-teaching (lists valid values) or opaque?
6. **Blast radius** — single retry vs. polluted DB vs. wrong definition vs. polluted memory feeding future LLMs

Risk verdicts:
- 🟢 **LOW** — output strongly validated; errors self-teaching; ephemeral or single-retry blast radius
- 🟡 **MEDIUM** — partial validation; some hallucination paths persist; user approval gate or limited downstream effect
- 🔴 **HIGH** — unvalidated JSON persisted; pollution chains downstream LLMs; durable contamination possible

---

## Surface-by-surface findings

### 🔴 1. Marketing scout runner — `artemis/marketing/scout_runner.py:165-205`

- **Emits:** JSON `{headline, sourceType, sourceUrl, campaignFamily, urgencyTier, reasonCodes, whyFlagged, evidence}`
- **Facts reasoned about:** valid `reasonCodes` for the scout's domain, valid `urgencyTier` values, valid `sourceType` enum, district names/IDs
- **Grounding:** ONLY the system prompt suffix `reason_code_system_suffix(agent.reason_codes_emitted)` lists allowed codes — text injection, not a tool the LLM can query
- **Runtime validation:** `json.loads` + `normalize_intake_payload(payload, scout_type=slug)` at line 203. **`normalize_intake_payload` checks structure (must be dict, must be list, confidence clamped to [0,1]) but DOES NOT validate `reason_code.code` against the agent's `reason_codes_emitted` allowlist.** Verified empirically at `scout_intake.py:193-204`.
- **Error messages:** opaque JSON parse errors only; no per-field validation messages
- **Blast radius:** **🔴 HIGH.** Hallucinated `reasonCodes` persist into `signal_reason_codes` table → qualifier reads them → tries to apply rules → silently no-op (no rule matches a hallucinated code) → signal misclassified. Empirically: `SELECT DISTINCT code FROM signal_reason_codes` could surface invented codes today.

**The bug class:** The Builder's hallucinated `disqualified` from CC19's smoke is the same shape — invented enum value. Scouts have been running for weeks and could have been seeding invented codes the entire time.

### 🔴 2. Trajectory summarizer — `artemis/builder/trajectory_summarizer.py:241-294`

- **Emits:** JSON `{what_worked, what_stalled, what_was_missing}` — three free-text fields
- **Facts reasoned about:** what tool calls happened, which signals were emitted, what failed, what succeeded — pure narrative
- **Grounding:** Receives the agent run snapshot as context (status, final_text, tool_calls, signals_emitted, duration_ms). Has truth in input.
- **Runtime validation:** `json.loads` → write three nullable strings to `agent_run_trajectory_summaries`. **NO Pydantic validation. NO length limit. NO enum check.** Verified at lines 267-294.
- **Error messages:** opaque (only catches JSON parse failure; logs warning + writes nulls)
- **Blast radius:** **🔴 HIGH (second-order pollution).** Summaries are read by the Builder (`agent_builder.py:371-384`) when an operator opens an edit session. The Builder reasons over them and proposes definition changes. **A hallucinated summary ("agent successfully called tool X" when it actually crashed) pollutes the Builder's reasoning the next time anyone touches that agent.** This is the bug behind CC19's smoke: the summarizer accurately captured the failure pattern, but the Builder still hallucinated state names because its reasoning is text-mediated, not schema-grounded.

**The bug class:** Free-text fields that other LLMs consume are an amplification vector. The summarizer itself may be accurate today, but it has no enforcement that prevents it from being inaccurate tomorrow.

### 🔴 3. Meeting summarizer — `artemis/meetings/summarizer.py:273-334`

- **Emits:** JSON `{bullets: [...], action_items: [{text, owner, due}, ...]}`
- **Facts reasoned about:** what was said in a Granola meeting transcript, who committed to what
- **Grounding:** Has the actual transcript (up to 6000 chars) — truth in input
- **Runtime validation:** `json.loads` only. `bullets_list` and `action_items` dict list written to `meeting_summaries` table with NO shape validation, NO owner format check, NO due-date format check. Verified at lines 320-334 + the persistence call.
- **Error messages:** opaque (JSON parse fail → placeholder; success → no validation)
- **Blast radius:** **🔴 HIGH (second-order pollution).** Meeting summaries are injected into Floating Artemis's system prompt via `get_recent_summaries()` (`chat.py:136-156`). A hallucinated action_item ("Jon committed to writing the grant proposal by Friday") becomes part of Floating Artemis's reasoning context, persisted durably. Floating Artemis may then remind Jon about a commitment he never made. Pollution affects every future Floating Artemis turn.

**The bug class:** Same second-order pattern as the trajectory summarizer. LLM-generated content that becomes another LLM's system prompt input is the most dangerous shape in the platform.

### 🟡 4. Agent Builder — `artemis/builder/agent_builder.py:403-659`

- **Emits:** conversational text + tool calls including `propose()` with full agent/skill definitions
- **Facts reasoned about:** valid tool names, valid state enums, valid model strings, agent slug uniqueness, schema constraints
- **Grounding:** `read_existing`, `read_capabilities`, `read_recent_runs` — partial. **No grounding for state enums, parameter constraints, or actual DB schema.** This is the gap CC19's smoke exposed.
- **Runtime validation:** `propose()` validates that cited `run_ids` were returned by `read_recent_runs` in the same session (lines 276-288) — prevents fabricated citations. But the `proposed_definition` JSONB itself is NOT schema-validated.
- **Error messages:** validation only on citations, so the only error message is about run_id provenance
- **Blast radius:** **🟡 MEDIUM** — proposals are staged for user approval before commit, so a Lead human is the safety gate. But (a) the user may approve a hallucinated proposal because it sounds plausible (we just rejected one for this exact reason), and (b) once approved, `engine.commit()` writes the hallucinated content to `agents.system_prompt` durably.

**The bug class:** CC19 smoke material. CC20 brief drafted to add `builder_read_tool_signatures`, `builder_read_db_schema`, `builder_read_skill_catalog`. Closes the grounding gap for Builder specifically; doesn't address other surfaces.

### 🟡 5. Floating Artemis chat — `artemis/floating_artemis/chat.py:504-720`

- **Emits:** conversational text + tool calls across 30+ tools (memory, builders, granola, jira, marketing, okr, system, writing_rules, gcal, slack)
- **Facts reasoned about:** page context, recent meeting summaries (🔴 HIGH-risk input from surface #3), memory observations, system status
- **Grounding:** `_query_memory` tool + page context injection. **No grounding for tool parameters, no validation that referenced IDs exist.**
- **Runtime validation:** Tool calls go through `AuthorizedToolRegistry` with layer gating (read-only / idempotent / side-effect / destructive). Layer 3/4 require user confirmation. Schema validation happens at tool execution time per the registry.
- **Error messages:** tool errors propagated to LLM (catches recovery)
- **Blast radius:** **🟡 MEDIUM.** Conversational hallucinations persist in `floating_artemis_messages` but don't feed other LLMs (single-turn pollution). Tool hallucinations are caught by schema + layer gating. The biggest risk: ingests hallucinated meeting summaries, so reasoning is corrupted upstream.

**The bug class:** Inherits pollution from surface #3 + has its own conversational unverified-fact assertions.

### 🟡 6. Pipeline AI Panel — `artemis/pipelines/assistant/turn_handler.py:290-330`

- **Emits:** conversational text + inline `PROPOSAL_BEGIN...PROPOSAL_END` JSON blocks for graph modifications
- **Facts reasoned about:** pipeline structure (nodes, edges, agent capabilities, trigger schedules)
- **Grounding:** receives current pipeline JSON + recent run summaries. **No grounding for valid agent capabilities, no schema introspection.**
- **Runtime validation:** proposals parsed via regex + JSON decode; full validation deferred to user-Accept route handler
- **Error messages:** opaque to the LLM
- **Blast radius:** **🟡 MEDIUM** — proposals user-approved before applying. Same shape as Builder (#4). Same fix shape (grounding tools).

### 🟡 7. Daily Brief generator — `artemis/brief/generator.py:55-86`

- **Emits:** JSON brief (highlights, priorities, okr_status, risks, next_actions — exact shape not enforced)
- **Facts reasoned about:** Jira issues, calendar events, Slack messages, OKR progress, memory, prior brief
- **Grounding:** has all real data as context — truth in input
- **Runtime validation:** regex extract JSON + `json.loads`. No Pydantic schema. Persisted to `brief_snapshots.brief_json` and `sources_json`.
- **Error messages:** opaque
- **Blast radius:** **🟡 MEDIUM** — brief is user-facing daily artifact. Read once by the human, not by other LLMs. Hallucinations are visible and correctable. Limited second-order risk.

### 🟡 8. Dev Projects loop runner — `artemis/dev_projects/loop_runner.py:96-191`

- **Emits:** conversational text + bash/listing command proposals
- **Facts reasoned about:** project file paths, code structure
- **Grounding:** project metadata only
- **Runtime validation:** local tool execution is permission-gated; user must approve. Conversational text not validated.
- **Blast radius:** **🟡 LOW-MEDIUM** — sandbox-scoped per project. Hallucinated file paths surface as errors when the user tries to execute. No DB pollution.

### 🟢 9. Agent run executor (main agent loop) — `artemis/builders/executor.py:300-372`

- **Emits:** text + tool calls (ToolUseBlocks) with structured inputs
- **Facts reasoned about:** task-specific content per agent definition
- **Grounding:** system prompt assembled from structured fields (lines 64-140); reason codes injected for scouts
- **Runtime validation:** **Tool calls validated against ToolRegistry input_schema at the registry layer. Unknown tools dropped (lines 309-315). Tool results type-checked before flowing back to LLM.** This is the only surface with strong runtime tool validation.
- **Error messages:** **STILL OPAQUE.** This is the gap Layer 1 of the platform-wide fix needs to address. Run #329's `ILLEGAL_TRANSITION` error came from this layer — the tool returned an error but didn't say WHICH states were valid.
- **Blast radius:** **🟢 LOW** for tool execution. Text hallucinations persist in `agent_context` but don't pollute other LLMs.

**The bug class:** Schema enforcement exists; error messages are the gap. Self-teaching errors would close the runtime hallucination loop (Run #329's recovery failed because the error message didn't tell the agent what to try instead).

### 🟢 10. Builder test_run — `artemis/builder/engine.py:278-385`

- **Emits:** test output text + tool call count
- **Grounding:** capped tools, write-tools filtered out, stubbed responses
- **Runtime validation:** tool calls capped at `_TEST_RUN_MAX_TOOL_CALLS`, stubs prevent side effects
- **Blast radius:** **🟢 LOW** — non-persistent, sandboxed. Output streamed to user via SSE, not DB.

---

## Summary risk matrix

| # | Surface | Risk | Pollutes other LLMs? | DB-persistent? | Fix shape |
|---|---|---|---|---|---|
| 1 | Marketing scout runner | 🔴 | No (text only) | Yes (`signal_reason_codes`, `signal_queue`) | Reason-code allowlist enforcement at intake |
| 2 | Trajectory summarizer | 🔴 | **YES → Builder** | Yes (`agent_run_trajectory_summaries`) | Pydantic schema + enum constraints |
| 3 | Meeting summarizer | 🔴 | **YES → Floating Artemis** | Yes (`meeting_summaries`) | Pydantic schema + action_item shape |
| 4 | Agent Builder | 🟡 | No (user gate) | Yes after approve | Grounding tools (CC20) |
| 5 | Floating Artemis | 🟡 | No | Yes (`floating_artemis_messages`) | Layer gating + tool schema (already partially OK) |
| 6 | Pipeline AI Panel | 🟡 | No (user gate) | Yes after approve | Grounding tools (Pipeline-equivalent of CC20) |
| 7 | Daily Brief | 🟡 | No | Yes (`brief_snapshots`) | Pydantic schema |
| 8 | Dev Projects | 🟡 | No | Yes (`dev_messages`) | Permission gating (already OK) |
| 9 | Agent run executor | 🟢 | No | Yes | **Self-teaching tool error messages** |
| 10 | Builder test_run | 🟢 | No | No | Already validated |

---

## The four architectural patterns the fix needs

### Pattern A — Self-teaching tool error messages (highest leverage)

Every tool's input_schema is already enforced at the registry layer (surface #9). The gap is the error MESSAGE. When `signal_queue.update_status(state="pending_human_review")` rejects, the response must be:

```
Invalid value for parameter 'state': 'pending_human_review'. 
Valid values are: pending_qualification, qualified, suppressed_stale, 
rejected_hard_filter, archived, held_pending_corroboration.
```

NOT just `ILLEGAL_TRANSITION`. The error teaches recovery in the next turn. Run #329 failed because the agent had no way to recover; with self-teaching errors, it would have retried with a valid state on call #2.

**Scope:** modify the ToolRegistry error path to enumerate enum values from each tool's input_schema when validation fails. Platform-wide — every agent benefits. ~80 LOC.

### Pattern B — Pydantic schemas on JSON-emitting LLM surfaces

Three surfaces (#1 scout, #2 trajectory, #3 meeting) do `json.loads` followed by direct DB insert with no shape validation. Each needs a Pydantic model that:

1. Validates field presence and types
2. Validates enum values (urgency_tier, source_type, reason_code.code)
3. Validates length constraints
4. If validation fails, returns a structured error to the LLM with the specific field that failed + valid values

The LLM gets a self-teaching error and retries. Same fail-loud-recover-fast shape as Pattern A but at JSON output time instead of tool call time.

**Scope:** one Pydantic model per surface, plus a retry wrapper that re-prompts the LLM on validation failure. ~150 LOC across the three surfaces.

### Pattern C — Grounding tools for definition-producing surfaces

Builder (CC20 already drafted) + Pipeline AI Panel + future definition-producing surfaces. These emit content (not just react to inputs) and need to KNOW facts before committing.

**Scope:** CC20 for Builder (already drafted, ~250 LOC). Pipeline AI Panel equivalent later (~200 LOC).

### Pattern D — Pollution-chain isolation

Surfaces #2 (trajectory → Builder) and #3 (meeting → Floating Artemis) feed other LLMs. These need extra hardening beyond their direct DB persistence:

1. Their consumed-by-LLM output must be REVALIDATED before being injected into a downstream LLM's system prompt
2. The consuming surface should know "this content is LLM-generated, not source-of-truth"
3. Ideally, both surfaces should also write to memory (M1 territory) with provenance markers so the source is traceable

**Scope:** wrap the read sites (`agent_builder.py:371-384` for trajectory; `chat.py:136-156` for meeting) with revalidation + provenance markers. ~60 LOC.

---

## Proposed brief sequence (the anti-hallucination stream)

**The principled order — by leverage, not LOC:**

| Brief | Pattern | LOC | Surfaces fixed | Notes |
|---|---|---|---|---|
| **H1 — Self-teaching tool errors** | A | ~80 | All agents using ToolRegistry (#9, #5, #4, #10) | Platform-wide unlock. Run #329-style runtime hallucinations stop recurring. |
| **H2 — Scout intake Pydantic + reason_code allowlist** | B | ~70 | #1 (scout runner) | Hallucinated reason_codes can't reach `signal_reason_codes` |
| **H3 — Trajectory summarizer Pydantic + revalidation** | B + D | ~80 | #2 (trajectory) + Builder read site | Closes the producer→Builder amplification |
| **H4 — Meeting summarizer Pydantic + revalidation** | B + D | ~70 | #3 (meeting) + Floating Artemis read site | Closes the producer→Floating Artemis amplification |
| **CC20 — Builder grounding tools** (already drafted) | C | ~250 | #4 (Builder) | Builder stops hallucinating definitions |
| **H5 — Daily Brief + Pipeline AI Panel Pydantic** | B + C | ~150 | #6, #7 | Lower priority — single-turn read |

**Total estimated:** ~700 LOC across 6 briefs. Same shape as the CC10-CC19 stream that closed self-improvement: many small, surgical, independently-verifiable fixes.

**Recommended sequence:** **H1 → H2 → H3 → H4 → CC20 → H5.**

- H1 first because it's platform-wide leverage at low cost.
- H2 because scouts produce the highest-volume LLM output and feed everything downstream.
- H3 + H4 next because they close the pollution amplification chains (worst-shape risk).
- CC20 after the foundation; it relies on tool input_schema being enforced (H1) to make its grounding outputs trustworthy.
- H5 last — lower-impact surfaces.

After this stream: re-run CC19's smoke. The Builder should propose with grounded state enums. Approve. `engine.commit()` exercises for the first time in production. Self-improvement loop closes for real.

---

## What this audit changes for the broader plan

- **CC20 alone was insufficient** as Jon correctly intuited. The Builder hallucination is one of three persistence-amplifying surfaces, not the only one.
- **M1 (memory: trajectory → observation) should fire AFTER H3.** Writing hallucination-prone trajectory summaries directly to memory observations would amplify the pollution into a third surface. H3 validates the summaries first; then M1 writes verified observations.
- **The "no hallucinations" invariant becomes enforceable.** With H1+H2+H3+H4+CC20 landed, every LLM-emitted fact is either (a) tool-call schema-validated, (b) JSON-shape Pydantic-validated, or (c) grounded against a read-truth tool. Hallucinations become detectable + recoverable rather than silent + persistent.

## Open question for Jon

Sequence the briefs as recommended (H1 → H2 → H3 → H4 → CC20 → H5), or rebalance? Some alternatives:

- **Cheapest first:** H1 alone is 80 LOC and unlocks runtime recovery for every agent. Could fire H1, validate, then decide on H2-H5 sequence with real data.
- **Pollution-chains first:** H3 + H4 are the worst-shape risks (they feed other LLMs). Could fire those first.
- **Parallelize:** H2/H3/H4 touch different files, could go simultaneously.

My lean is the sequence-as-listed because H1 is the prerequisite that makes self-teaching errors uniform across the platform, and the others are surgical fixes that benefit from that foundation.
