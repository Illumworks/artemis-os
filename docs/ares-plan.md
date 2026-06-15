# Ares — Plan (personal maker-agent + orchestration bridge)

**Status:** PLANNING (2026-06-15). Jon's favored named-agent direction (#4 in
`docs/named-agents-candidates.md`). This doc is the working plan; build phases are
sequenced, not yet started. Companion docs: `docs/named-agents-candidates.md`,
`docs/agent-slack-architecture.md` (Named Agent Standard), `docs/provider-routing-cost-plan.md`.

---

## 1. What Ares is

**The orchestrator-maker Jon plans with.** Plan together → Ares runs the build →
he auto-delegates chunks to sub-agents on *different providers* → they report to
him → he validates → he reports "done" to Jon or Artemis. He holds **durable
project memory** so he resumes across sessions with **zero re-briefing**. He works
in Jon's project world (builds Jon's initiatives, prototypes, docs, analyses, tools),
**not** production Artemis OS (that stays under the governed Lead/worker flow).

Two jobs from one primitive — *tasked → goes deep → returns a result* — where the
result is either a **briefing** (research) or a **built artifact** (making).

**The point is the bridge.** Today Jon lives in two worlds that don't share a brain:
the **build world** (Claude Code in the terminal — knows the code, blind to Artemis)
and the **assistant world** (Artemis — knows meetings/commitments, blind to what Jon
is building). Ares + a shared memory closes that gap.

**Good news:** most foundations already exist. Ares is an *evolution of the existing
`dev_projects/` module* (loop runner, WebSocket streaming, permission prompts), not a
from-scratch build.

---

## 2. The four capabilities

| # | Capability | The win | Status today |
|---|---|---|---|
| 1 | **Durable project memory** (no re-briefing) | Kills Jon's #1 pain | M3 scoped memory ✅ live; `dev_projects` models exist; NEW: persistent "project workspace" drawer (plan + progress + decisions + file-map) auto-loaded on resume |
| 2 | **Multi-provider sub-agent fleet** | Spreads load off Claude Max | Provider cascade ✅ hardened (claude-code→codex→lm-studio→anthropic); `feature_catalog` tiers ✅; `lm-studio` adapter ✅; NEW: per-task routing + local box wired in |
| 3 | **Auto-delegate → validate → report-up** | Kills copy-paste-into-terminals pain | `dev_projects` loop + `spawn_subagent` exist; NEW: **P4 delegate primitive** (named, multi-step, result-gathering) + validation/report step |
| 4 | **Named-agent wrapper** (persona/avatar/Slack) | Makes him a real teammate | Whole Named Agent Standard ✅ (Artemis/Callie use it); NEW: Ares persona + the memory bridge so Artemis can see his work |

---

## 3. Dependency / gate status (explicit)

| Gate | Status | Notes / where it's accounted for |
|---|---|---|
| **Named Agent Standard** | ✅ Built | All 8 capabilities exist as reusable infra → Phase 3 wrapper |
| **M3 scoped memory** | ✅ Live (2026-06-14) | The memory-sharing foundation Phase 0 needs |
| **`dev_projects/` module** | ✅ Groundwork built | loop_runner, ws, permissions → Ares evolves this |
| **Provider cascade + feature tiers** | ✅ Built/hardened | `resolver.py`, `feature_catalog.py`, `routing_candidates.py` → Phase 1 + §6 |
| **Session→memory bridge** ("early win") | ❌ Not built | Nothing feeds terminal/build activity into Artemis's memory yet → **built in Phase 0** |
| **P4 delegate primitive** | ⏳ Planned / next on roadmap | `spawn_subagent` is one-shot, not named delegation → **gates Phase 2** |
| **Deep-research capability** | 🟡 Partial | Anthropic `web_fetch`/`deep_research` available but NOT registered as agent tools → **wired in Phase 1** (the "returns a briefing" half of Ares) |

---

## 4. How "no re-briefing" works (the mechanism)

Claude Code makes Jon re-brief because **each terminal session starts blind** — it
only knows `CLAUDE.md` + the current window; nothing persists. Ares fixes this by
**writing project state into the memory keystone** — the plan, decisions, what's done,
open threads, key files — and **auto-loading it on resume**. "Pick up where we left
off" = Ares reads the project's memory drawer at session start.

The **session→memory bridge** is the same mechanism pointed at *current Claude Code
terminal sessions*: capture "what Jon & Claude Code are working on" into the keystone
so Artemis can answer "what am I working on?" — delivering the bridge's core value
*before* full Ares exists. (M3 scoping already lets this be stored safely.)

---

## 5. Decisions & advice

### Superpowers — YES, but scoped to a sandbox
Ares gets **broad autonomy inside Jon's project world**: write files, run code, spin
up sub-agents on any provider, install deps, iterate, self-correct — no per-step
permission. That's what makes him powerful and unblocks "just go build it."

Guardrails stay on the **three expensive-to-undo borders**:
1. Committing to **production Artemis OS** → stays in governed Lead/worker flow.
2. **Sending as Jon** (email/Slack) → behind the existing agency gate.
3. **Spending real money** (paid API / cloud) → gated.

Inside the sandbox: superpowers. At those three borders: the gate.

### Hardware — don't buy yet; the M3 Ultra is already the right box
- **Memory bandwidth, not "AI TOPS," drives local-LLM speed.** M3 Ultra (~800 GB/s)
  generates tokens *faster* than ASUS GX10 / GB10 (~273 GB/s) for models that fit in
  RAM — better for interactive coding sub-agents.
- **GX10 (GB10) buys:** 128GB + CUDA ecosystem (vLLM/TensorRT, easier *fine-tuning*,
  clusterable) — but slower decode + a Linux/CUDA ops world.
- **Local-model reality:** 32–70B local coders (Qwen2.5-Coder, DeepSeek-Coder) are
  meaningfully weaker than Sonnet/Opus on hard codegen. Division of labor: local =
  cheap/parallel/routine; Codex/Gemini = mid-volume; Claude = hard parts; Opus =
  planning + validation.
- **Call:** stand up LM Studio/Ollama on the M3 Ultra now (the `lm-studio` provider
  already plugs in), prove the local-sub-agent workflow on real tasks, **measure**.
  Buy only on a measured wall: "need fine-tuning / bigger models" → GX10 or cluster;
  "need faster Mac-native inference / more concurrency" → second Studio or **wait for
  M5 Ultra**. Don't buy ahead of a proven workflow.

---

## 6. Provider rebalancing map (quick win — independent of Ares, start now)

The framework already exists (`artemis/providers/feature_catalog.py` tiers; per-feature
overrides in `feature_routing_overrides`; the "Cost → Routing opportunities" UI tab).
**Today all 20 agents are pinned to `claude-code`.** Rebalancing is mostly *activation*.

**Cost reality:** `claude-code` = the Claude **Max subscription** (flat $, but
quota/rate limits). The real quota burn is **Opus planning sessions**, not the agents
(already on cheap haiku/sonnet). Goal = move routine/background/high-volume work OFF
Claude so the Max quota is reserved for planning, validation, and customer-facing work.

| Surface (feature_tag) | Current | Recommended target | Tier |
|---|---|---|---|
| `floating_artemis`, `agent_run` (customer/operator-facing), `workflow`, `marketing_brief`, `builder_propose_*` | claude-code | **stay Claude** | T1 |
| Opus planning sessions (human-facing) | Claude Opus | **stay** (highest value) | T1 |
| `marketing_scout` — judgment (legislative, federal_funding) | claude-code/haiku | **stay** (haiku) | T1 |
| `marketing_scout` — classification (board_minutes, regional_news, state_doe, linkedin, procurement, leadership_transition) | claude-code/haiku | **Gemini-flash / local Qwen** | T3 |
| `memory_consolidation`, `memory_graph_extraction` | T3 default | **Gemini** (strict JSON) / local | T3 |
| `trajectory_summary`, `meeting_summary` | T3 default | **Gemini** (1M ctx) / local | T3 |
| `signal_qualifier`, `okr_suggest_kr`, `okr_extract_activity` | low-stakes | **local / Gemini** | T3 |
| Slack channel-reply classifier (YES/NO gate) | anthropic (no key → silent) | **local Qwen / Gemini-flash** | T3 |
| `dev_projects_loop` (Ares' builds) | claude-code (critical) | **tiered**: plan=Claude/Opus, codegen volume=Codex/local | mixed |

**Enablement prerequisites — verified status (2026-06-15):**
1. **Codex CLI** — ✅ **DONE.** Installed (`~/.local/bin/codex`, v0.126.0-alpha.8) and
   authed via ChatGPT account (`~/.codex/auth.json`). T2 leg is ready.
2. **Gemini** — ❌ **NOT set.** The gemini adapter reads `GEMINI_API_KEY` / `GOOGLE_API_KEY`;
   neither is in `.env` or the server env. **Add `GEMINI_API_KEY`** to `.env` to enable the
   T3 cloud leg. (Jon believed this was done — it is not, where the app reads it.)
2. **Local LLM** — currently `LM_STUDIO_BASE_URL` is **hardcoded `http://127.0.0.1:1234`**
   in `artemis/providers/health.py` (the local model on the Mac mini). To use the Mac
   Studio instead: serve LM Studio/Ollama on `0.0.0.0:1234` there, make the base URL
   **env-configurable**, and point it at the Studio's LAN IP or Tailscale hostname (use an
   explicit address, not `localhost` — see the ::1 lesson). See §9.
3. Flip `feature_routing_overrides` / per-agent `provider` rows to the targets above.

**Biggest immediate savings (do first):** the background/high-volume T3 features
(memory consolidation, graph extraction, summaries, classification scouts, channel
gate) run constantly and don't need Claude — moving them frees Max quota right away.

---

## 7. Sequencing

- **Phase 0 — Durable project memory + session→memory bridge** *(unblocked now; M3 done)*.
  Ares/Artemis remember projects across sessions; no re-briefing. Biggest pain, smallest
  dependency. *(Accounts for gate: session→memory bridge.)*
- **Phase 1 — Multi-provider fleet + research capability.** Activate provider rebalancing
  (§6); wire Codex/Gemini/local into `dev_projects` orchestration with per-task routing;
  **register `web_fetch`/`deep_research` as agent tools** so Ares can return briefings.
  *(Accounts for gate: deep-research.)*
- **Phase 2 — Auto-delegate → validate → report-up** *(needs P4)*. Ares dispatches build
  chunks automatically, checks the work, reports completion to Jon/Artemis. Kills the
  copy-paste-into-terminals pain. *(Accounts for gate: P4 delegate primitive.)*
- **Phase 3 — Ares persona/avatar/Slack + memory bridge to Artemis.** He becomes a full
  named teammate; Artemis sees his work because they share memory.

The provider rebalancing (§6) is **independent of Ares** and can start immediately as
its own quota-saving win.

---

## 9. Account routing, privacy scope & machine topology

### Multi-account Claude routing (use the idle personal subscription)
Today the `claude-code` adapter just runs the `claude` binary on whatever account that
machine's CLI is logged into (it inherits the parent env; no per-call account selection).
Jon runs **two Claude subscriptions** — marketing (this build's CLI) and personal
(currently only via the claude.ai app, idle as a CLI). Goal: don't burn the marketing
quota on personal/Ares work; use the idle personal subscription for it.

**Feasible.** The `claude` CLI isolates auth by config home via **`CLAUDE_CONFIG_DIR`** —
log each account into its own config dir, then set `CLAUDE_CONFIG_DIR` per subprocess.
Extend the claude-code adapter to take a per-agent **"claude account"** setting so the
routing key becomes **(provider, account, model)**. Account allocation (Jon, 2026-06-15 —
neither account is *sole* automation, so this is within ToS):
- **Marketing account** → **Callie**, **Writing Studio**, and the marketing autonomous
  agents (scouts / qualifier / content).
- **Personal account** → **Artemis** (personal PA), **Ares** + some of his sub-agents,
  and **Jon's own projects**.

Needs: a small adapter change (set `CLAUDE_CONFIG_DIR` per agent), both accounts logged
into separate config dirs, and the per-agent account map above.

**Account-switch milestone:** once Artemis OS is solid, the *marketing-facing* app moves
fully onto the **marketing** account; Jon's **personal** account carries Ares + personal
Artemis. The per-agent account map is what makes that switch a config change, not a rebuild.

### Ares privacy — visible to Jon only (not coworkers)
Decision stands: **one app + gating**, not two apps. M3 scope enforcement
(`artemis/identity/scope_policy.py`) already isolates visibility — coworkers (marketing
humans, Callie) get marketing-shared + their own; they cannot see `personal` / `agent:artemis`.
**Ares (`dev_projects-0`) gets an owner-private scope** (e.g. `agent:ares` + Jon's owner/
personal scope), excluded from marketing-shared — so coworkers can't see Ares or his
projects, while Artemis (all-scopes) can. No new mechanism needed; just assign the scope.

### Machine topology (current → target)
- **Now:** Mac **mini** hosts Artemis OS + both CLIs + a local LLM at `127.0.0.1:1234`.
- **Target:** Mac **Pro** = daily driver; Mac **Studio M3 Ultra** = LLM box (LM Studio/Ollama
  on `0.0.0.0:1234`); Artemis OS host TBD (stays on mini, or moves). The link to a local LLM
  on another machine is just an **HTTP call to its address** — repoint `LM_STUDIO_BASE_URL`
  at the Studio's LAN IP / Tailscale hostname. Decide where the Artemis OS host + Ares
  orchestrator run (mini vs Pro vs Studio) as part of Phase 1.

## 8. Open questions / next decisions
- Phase 2 pull-forward? Auto-delegation is the copy-paste killer but gates on P4 — worth
  prioritizing P4 to unlock it sooner.
- Which local coder model(s) to standardize on for the fleet (after measuring on real tasks).
- Whether Ares' orchestrator runs on the Mac Pro (daily driver) or the Mac Studio (LLM box).
- Scope of the "project workspace" memory schema (what state persists per project).
- Where the Artemis OS host + Ares orchestrator run (mini vs Pro vs Studio).

---

## 10. Build specs (work items — brief-ready)

Each item is sized for a worker brief. Format: **goal · key mechanism/files · acceptance.**
Tracks R (rebalancing) and C (Claude accounts) are **independent of Ares** and can start now.

### Track R — provider/cost rebalancing (independent quick win)
- **R1 · Enable Gemini.** Add `GEMINI_API_KEY` to `.env`; confirm via `artemis/providers/health.py`.
  *Accept:* gemini health check passes; a T3 feature routes to gemini.
- **R2 · Local LLM endpoint configurable + on Studio.** Replace hardcoded `LM_STUDIO_BASE_URL`
  (`health.py`, adapter) with an env setting; serve LM Studio/Ollama on the Mac Studio
  `0.0.0.0:1234`; point the URL at the Studio's Tailscale/LAN address (explicit IP, not
  `localhost`). *Accept:* `lm-studio` health passes against the Studio; a T3 feature runs on it.
- **R3 · Apply the rebalancing flips.** Set `feature_routing_overrides` / per-agent `provider`
  for the §6 targets: classification scouts (board_minutes, regional_news, state_doe, linkedin,
  procurement, leadership_transition) → Gemini/local; `memory_consolidation`,
  `memory_graph_extraction`, `trajectory_summary`, `meeting_summary`, `signal_qualifier`,
  `okr_*` → Gemini/local. Keep judgment scouts + customer-facing on Claude.
  *Accept:* those features run off-Claude (verify in the Cost→Routing UI + a live run);
  judgment/customer paths unchanged.
- **R4 · Fix the channel classifier.** Route `_default_channel_classifier`
  (`integrations_slack_events.py`) to local/Gemini instead of the keyless Anthropic call.
  *Accept:* channel-reply gate works (not silent-default) once a T3 provider is live.

### Track C — multi-account Claude routing
- **C1 · Per-agent account in the adapter.** Teach `ClaudeCodeAdapter` to set
  `CLAUDE_CONFIG_DIR` per call from an agent "account" field; carry the account through the
  resolver/feature layer (routing key → provider, account, model). *Accept:* a unit test proves
  the subprocess env carries the chosen config dir.
- **C2 · Provision accounts + map.** Log marketing + personal into separate `CLAUDE_CONFIG_DIR`s;
  seed the per-agent account map (§9 allocation). *Accept:* a marketing agent consumes the
  marketing account; an Ares/personal agent consumes the personal account (verify via
  `claude` whoami per config dir + quota).

### Ares Phase 0 — durable project memory + session→memory bridge *(unblocked now)*
- **P0.1 · Project-workspace memory schema.** A per-project drawer holding plan, decisions,
  progress, open threads, file-map; scoped **owner-private** (`agent:ares` + Jon). Built on the
  M3 memory store + `dev_projects` models. *Accept:* a project's state round-trips to memory.
- **P0.2 · Auto-resume.** On project session start, load its drawer into context.
  *Accept:* resuming a project gives Ares prior context with no re-brief.
- **P0.3 · Session→memory bridge.** Capture Claude Code terminal-session activity ("what Jon &
  CC are building") into the keystone (owner scope). *Accept:* Artemis answers "what am I
  working on?" from memory.

### Ares Phase 1 — multi-provider fleet + research capability
- **P1.1 · Fleet routing in dev_projects.** Wire Codex/Gemini/local (Tracks R+C) into
  `dev_projects` orchestration with per-task provider/account routing. *Accept:* Ares dispatches
  a sub-task to a chosen provider/account.
- **P1.2 · Research tools.** Register `web_fetch` / `deep_research` in the agent tool registry
  (`floating_artemis/tool_registry.py`). *Accept:* Ares runs a research task and returns a
  sourced briefing.

### Ares Phase 2 — auto-delegate → validate → report-up *(needs P4)*
- **P2.1 · P4 delegate primitive.** Named, multi-step, result-gathering delegation (beyond
  one-shot `spawn_subagent`): dispatch → await → validated result. *Accept:* Artemis/Ares
  dispatch a named agent and receive a structured result.
- **P2.2 · Ares orchestration loop.** plan → fan out to multi-provider sub-agents → collect →
  **validate** → report completion to Jon/Artemis. *Accept:* a planned build runs end-to-end
  with no manual copy-paste; Ares reports "done" with a validation summary.

### Ares Phase 3 — named-agent wrapper
- **P3.1 · Persona + avatar + Slack identity** for Ares (Named Agent Standard).
- **P3.2 · Memory bridge to Artemis** (shared memory; overlaps P0.3) so Artemis sees Ares' work.
- **P3.3 · Ares-private scope end-to-end** — coworkers (Callie, marketing humans) cannot see
  `agent:ares`/owner-private; Artemis can. *Accept:* a coworker-scoped query excludes Ares data;
  Artemis-scoped includes it.
