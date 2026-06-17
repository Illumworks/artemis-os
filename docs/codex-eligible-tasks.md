# Codex-Eligible Tasks (tool-less → cost-reduction routing)

**Status:** ANALYSIS DONE (2026-06-16, read-only inventory). **Flips DEFERRED** until terminal finishes the
Callie scout/`get_signal` fixes (avoid collision), then apply + live-verify. Companion: `docs/provider-routing-cost-plan.md`,
`docs/ares-plan.md` §6/§10, [[project-provider-cost-reality]].

## The rule
**Codex CLI cannot run tools.** So only **tool-LESS** tasks (a plain `complete()` / `complete_with_fallback()`
/ `run_turn(tools=None)` — no tool registry, no `run_with_tools()`, no MCP loop) can move to Codex. Tool-using
turns must stay on a tool-capable provider (claude-code today). Gemini is free-tier rate-limited. So the cost
lever = **route tool-less work to Codex**, keep tool-using agentic turns on Claude.

## ✅ Codex-eligible (tool-less) — ranked by volume × safety × effort
Most are a **config flip** (per-agent `provider` column or `feature_routing_overrides`), not a code change.
Verify output quality on a small sample before committing each.

1. **Classification marketing scouts** (board_minutes, regional_news, state_doe, linkedin, procurement,
   leadership_transition) — highest volume (1000+/day under full runs), binary/structured classification.
   `scout_runner.py:108` (`_call_llm` → `complete_with_fallback`, no registry). Per-agent `provider` flip.
2. **Slack channel YES/NO gate** (Callie relevance gate) — `integrations_slack_events.py:119` (5-token
   classifier). **Currently BROKEN** (direct `AnthropicAdapter()`, no key → defaults silent). Fix + route to
   Codex/LM-studio. Any competent model works. *Urgent regardless of provider.*
3. **`okr_extract_activity` + `okr_suggest_kr`** — `routes/okr.py:574,508`. Low-volume, low-risk. Catalog
   already targets T2/T3.
4. **`pipeline_canvas_ai`** — `pipelines/assistant/turn_handler.py:335` (explicit `tools=[]`). User
   accepts/rejects proposals, so quality is gated. Catalog already targets T2/Codex.
5. **`trajectory_summary` + `skill_distiller`** — `trajectory_summarizer.py:164`. Background, post-hoc, low
   stakes.

### Tool-less but better on GEMINI than Codex (JSON-strict / long-context)
- **`memory_consolidation`** (`consolidator.py:230`), **`memory_graph_extraction`** (`graph_extractor.py:200`)
  — strict JSON; Gemini's schema adherence is the known-good path. (graph_extraction is also currently broken —
  fix the direct-SDK call first.)
- **`meeting_summary`** (`summarizer.py:387`) — long transcripts; Codex has no 1M window, will truncate >~32K
  tokens. Gemini Flash safer. Check avg transcript length before moving.
- Keep these **Gemini-first with Codex/claude fallback**, not Codex-primary.

### Tool-less but quality-critical → leave on Claude for now
- **`writing_studio_compose`** (`writing_studio.py:798`, `run_turn` no tools), **`campaign_brief_assembler` /
  `campaign_initiation`** (`brief_assembler.py:480`), **`meetings_qa`** (`routes/meetings.py:752`) — all
  tool-less but customer-facing writing/Q&A. Codex viable only if quality validates; default keep T1.
- **`workflow` executor steps** (`workflow_executor.py:68`) — tool-less but hardcoded claude-code (latent bug);
  customer-facing steps, test first.

## ❌ NOT Codex-eligible (tool-using — must stay tool-capable)
- **`floating_artemis`** — Artemis/Callie/Kai chat turns; full MCP tool catalog per turn (`chat.py:816,893`).
- **`agent_run` / pipeline agent node** — `run_with_tools()` via MCP (`builders/executor.py:512`).
- **Judgment marketing scouts** (legislative, federal_funding, signal_processor) — fetch/write tools in
  `agent.tools`.
- **`builder_propose_agent` / `builder_propose_skill`** — ⚠️ **CORRECTION:** the catalog + R3 plan mark these
  T2/Codex, but they are **tool-USING** (grounding tool loop, up to 5 iterations, `agent_builder.py:910,946`).
  **Codex cannot run them.** Fix the catalog entry + the cost plan — this is the biggest plan-vs-reality gap.

## ❓ Needs a Lead/Jon decision
- **`spawn_subagent`** — the spawned sub-turn is tool-less (`tools/core.py:408`), but it's invoked from inside
  a tool-capable session; routing the sub-turn to Codex needs a refactor, not a flip.
- **`mcp_sandbox`** — the eval call (`mcp_server.py:769`) may itself invoke tools during evaluation; needs a
  focused read before routing.

## Next actions (after terminal's scout fixes land)
1. Fix the catalog/cost-plan: `builder_propose_*` are tool-using, NOT Codex (correct the wrong T2 target).
2. Apply Codex flips for the safe set (#1-#5 above) via per-agent `provider` / `feature_routing_overrides`;
   live-verify accuracy on a small sample each before committing.
3. Fix + route the broken Slack gate and graph_extraction direct-SDK calls.
4. Keep the JSON-strict/long-context four on Gemini-first.
