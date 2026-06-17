# Codex-Eligible Tasks (tool-less → cost-reduction routing)

**Status:** ANALYSIS (2026-06-16, read-only). **Flips DEFERRED** until terminal finishes the Callie
scout/`get_signal` fixes, then apply + live-verify. Companions: **`docs/provider-output-hardening.md`
(AUTHORITATIVE provider-move status/plan)**, `docs/provider-routing-cost-plan.md`, `docs/ares-plan.md` §6/§10,
[[project-provider-cost-reality]].

## Two axes — BOTH required to move a task off Claude
1. **Tool-less.** Codex CLI cannot run tools, so only tasks that make a plain `complete()` /
   `complete_with_fallback()` / `run_turn(tools=None)` (no registry, no `run_with_tools`, no MCP loop) can go
   to Codex at all.
2. **Output-contract portable.** Tool-less is NECESSARY but NOT SUFFICIENT. The feature's prompt/schema must
   survive a non-Claude model. Many features here are **Claude-tuned** and already **broke on Gemini and were
   reverted** (e.g. scouts returned `reasonCodes` as strings + extra `districtId` → Pydantic rejected). Codex
   would hit the same wall. Fixing those = the per-feature **output-hardening** workstream in
   `provider-output-hardening.md` — and that work targets **Gemini** (JSON adherence / long context), not Codex.

So: a true **Codex candidate today** = tool-less **AND** simple/portable output (no Claude-tuned schema).

## Authoritative provider-move status (from `provider-output-hardening.md`)
- ✅ **Already LIVE on Gemini** (hardened): `trajectory_summary`, `memory_graph_extraction`. Done — not candidates.
- 🛑 **`memory_consolidation` — STAYS ON CLAUDE** (Jon, 2026-06-16). Hardening is ready but it was deliberately
   held: Gemini "got funny" on it and it's too important to risk. **Do not move** (Gemini or Codex).
- ⬜ **6 classification scouts + `meeting_summary` — still on Claude, need output-hardening FIRST.** Their
   Claude-tuned schema must get the Layer-C tolerant validators before moving to ANY non-Claude provider; the
   planned vehicle is **Gemini** (meeting_summary also needs long context). **NOT a simple Codex flip.**

## ✅ Genuine Codex candidates today (tool-less + simple/portable output)
1. **Slack channel YES/NO gate** (Callie relevance gate) — `integrations_slack_events.py:119`. A 5-token
   YES/NO with no schema → trivially portable. **Also currently BROKEN** (direct `AnthropicAdapter()`, no key →
   defaults silent). **Fix + route to Codex/LM-studio. Urgent regardless of provider.** Clear #1.
2. **`okr_extract_activity` + `okr_suggest_kr`** — `routes/okr.py:574,508`. Tool-less, low-volume, low-risk.
   Catalog already targets T2/T3. *Verify output shape is simple before flipping.*
3. **`pipeline_canvas_ai`** — `turn_handler.py:335` (explicit `tools=[]`). User accepts/rejects proposals, so
   quality is gated. Catalog already targets T2/Codex. *Verify output shape.*
4. **`trajectory_summary` / `skill_distiller`** — note trajectory_summary is **already on Gemini**; Codex is a
   fine *fallback* but no need to move it again. skill_distiller (free-text-ish) is a low-risk Codex option.

**Codex's other real role:** the adapter is functional and wired as a **rate-limit fallback** for Gemini
(unused so far). That's valuable even before any primary flips.

## ⏳ Tool-less but NOT movable yet (need hardening or held)
- **Classification scouts (6)** — need Layer-C tolerant validators first; planned for **Gemini**, not Codex.
- **`meeting_summary`** — hardening + long context → Gemini.
- **`memory_consolidation`** — stays on Claude (held).
- **`writing_studio_compose`, `campaign_brief_assembler`, `campaign_initiation`, `meetings_qa`,
  `workflow` steps** — tool-less but customer-facing/quality-critical; keep on Claude unless quality validates.

## ❌ NOT Codex-eligible (tool-USING — must stay tool-capable)
- **`floating_artemis`** (Artemis/Callie/Kai chat turns) — full MCP tool catalog (`chat.py:816,893`).
- **`agent_run` / pipeline agent node** — `run_with_tools()` via MCP (`builders/executor.py:512`).
- **Judgment scouts** (legislative, federal_funding, signal_processor) — fetch/write tools.
- **`builder_propose_agent` / `builder_propose_skill`** — ⚠️ **CORRECTION:** catalog + R3 plan mark these
  T2/Codex, but they are **tool-USING** (grounding tool loop, `agent_builder.py:910,946`). **Codex cannot run
  them.** Fix the catalog entry + cost plan — biggest plan-vs-reality gap.

## ❓ Needs a Lead/Jon decision
- **`spawn_subagent`** — sub-turn is tool-less but invoked from a tool-capable session; routing it = a refactor.
- **`mcp_sandbox`** — eval path may itself call tools (`mcp_server.py:769`); needs a focused read.

## Next actions (after terminal's scout fixes land)
1. Fix the catalog/cost-plan: `builder_propose_*` are tool-using, NOT Codex.
2. Fix + route the **broken Slack gate** (#1) to Codex/LM-studio — do this regardless; it's broken now.
3. Try Codex on OKR helpers + pipeline canvas (verify output quality on a sample).
4. Wire Codex as the **Gemini rate-limit fallback** for the already-hardened features.
5. Everything schema/long-context (scouts, meeting summary) → the **Gemini** output-hardening track, not Codex.
   `memory_consolidation` stays on Claude.
