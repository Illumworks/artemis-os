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

**Enablement prerequisites — verified status (updated 2026-06-15, all GREEN):**
1. **Codex CLI** — ✅ **DONE.** Installed (`~/.local/bin/codex`) and authed via ChatGPT account
   (`~/.codex/auth.json`). T2 leg is ready.
2. **Gemini** — ✅ **DONE.** `GEMINI_API_KEY` now in `.env`; gemini health = "key configured".
3. **Local LLM** — ✅ **DONE.** `LM_STUDIO_BASE_URL` is now env-configurable (R2, `7e5fe97`) and
   pointed at the Mac Studio's Tailscale IP; lm-studio health passes (20ms over Tailscale). The Mac
   mini's local LLM was unloaded to free RAM for the app/PG. (Use an explicit address, not
   `localhost` — the ::1 lesson.)

**→ Only R3 (apply the flips + Gemini-429 fallthrough) remains. See the RESUME block in §10.**
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

> ### ⏩ RESUME — Provider rebalancing (paused 2026-06-15, R3 is next)
>
> Paused to chase the asyncpg connect-timeout instability bug
> (`briefs/instability-asyncpg-connect-timeout.md`). Resume here when that's done. This block is
> the **settled state** from the working session — read it before re-opening R3.
>
> **Done + committed:**
> - **R2** (`7e5fe97`) — LM Studio base URL is env-configurable (`ARTEMIS_LM_STUDIO_BASE_URL`);
>   worker also fixed the old `localhost`→`127.0.0.1` IPv6-hang default.
> - **C1** (`4d1db1a`) — per-agent Claude account via `CLAUDE_CONFIG_DIR`. **Scope gap:** covers the
>   `run_with_tools` (scout/agent) path only, **not** the Floating-Artemis/Builder `complete()` chat
>   path — so "personal Artemis → personal account" needs a small follow-up (see C-followup below).
> - **Prereqs Jon completed live:** `GEMINI_API_KEY` in `.env`; `ARTEMIS_LM_STUDIO_BASE_URL` pointed at
>   the Mac Studio's Tailscale IP; the Mac mini's local LLM **unloaded** (it competed with the app/PG
>   for RAM and was likely contributing to the stalls).
> - **All 4 providers verified live from the app on the mini (2026-06-15 ~20:22Z):** claude-code
>   v2.1.170 ✅ · codex v0.129 ✅ · gemini "key configured" ✅ · lm-studio 20ms, models loaded ✅ (so the
>   mini reaches the Studio over Tailscale, not just a local curl). This means R1 + R2 prereqs are
>   satisfied — only the R3 flips remain.
>
> **Local models on the Studio (M3 Ultra, 96GB) — settled 2-resident set:**
> - Coder (keep loaded): **`qwen/qwen3-coder-next`** → Ares codegen + code-shaped tasks.
> - General (load this): **`qwen3.5-35b-a3b`** (8-bit) → signal qualifier, Slack channel YES/NO gate,
>   and the $0/private fallback when Gemini throttles.
> - Leave `Qwen3-Coder-30B-A3B-Instruct` unloaded (backup coder). The `nomic-embed` model can be
>   unloaded — the app embeds locally with minilm. **3 loaded = too much; 2 is the ceiling.**
>
> **Settled allocation (supersedes the §6 table where they differ; Jon-confirmed):**
> - **Claude (account split, quality-critical):** Floating Artemis → *personal*; Callie + marketing
>   agent runs + campaign briefs + Writing Studio compose → *marketing*; judgment scouts (legislative,
>   federal_funding) → *marketing*; Builder proposals + Ares planning/validation → *personal*.
> - **Gemini 2.5 Flash (cloud, free tier):** strict-JSON background + long-context summaries + public
>   classification scouts (board_minutes, regional_news, state_doe, linkedin, procurement,
>   leadership) + OKR activity extraction + memory consolidation/graph-extraction + trajectory/meeting
>   summaries. Jon confirmed: public-facing scout data + OKR extraction to cloud is fine.
> - **Local Qwen (Studio):** signal qualifier + channel gate (general model) + Ares codegen (coder
>   model) + $0/private fallback for Gemini. Caveat: single-stream per model on decode — concurrency
>   queues; that's why bursty work goes to Gemini first.
> - **Codex:** codegen volume + the overflow lane when Gemini is throttled or local is queued (no
>   free-tier cap, not single-stream). Jon OK'd Codex for coding + other tasks.
>
> **⚠️ R3 SCOPE UPDATE (2026-06-15, Jon's call): GEMINI + CODEX ONLY — NO local/lm-studio.**
> Jon unloaded the Studio models (can't run two large ones at once); **wire lm-studio only after Ares
> is live/ready to test.** So all R3 cascades are **Gemini → claude-code** (skip local entirely; don't
> add a dead hop). The local targets in the allocation (signal qualifier, Slack gate) were already
> no-ops/deferred anyway. Food-for-thought for the multi-account track: **2 Codex accounts + 2 Claude
> accounts** (Jon raised this; revisit with Track C).
>
> **Findings while starting R3 (2026-06-15):** (a) Gemini was 404ing — the model map pointed at the
> RETIRED `gemini-2.5-flash-preview-05-20`; **FIXED `e9011ee`** → stable `-latest` channels
> (`gemini-2.5-flash`→flash-latest, `gemini-2.5-flash-lite`→flash-lite-latest), default flash-lite-latest;
> verified live. (b) `cost_events` shows **everything still on Claude/anthropic** — the catalog
> "gemini-first" defaults do NOT translate to real Gemini usage, so moved features need EXPLICIT
> overrides + live verification, not assumption. (c) `signal_qualifier` is pure Python (no LLM) — drop;
> `okr_extract_activity` not wired to any call site — defer; Slack channel gate is hard-coded Anthropic
> (needs a code refactor) — optional R4.
>
> **R3 STATUS (2026-06-15 end of session): infra DONE + committed; live flips ATTEMPTED then REVERTED.**
> - ✅ **Gemini-429 safety net** (`6c011b7`): runtime fallthrough Gemini→claude-code on
>   429/503/5xx/connection via `complete_with_fallback`, wired at the 5 call sites (scout `_call_llm`,
>   memory consolidator, graph_extractor, meetings summarizer, trajectory summarizer). 400 re-raises. No
>   lm-studio. Each call site defaults primary to claude-code when no active override → inert until flipped.
> - ✅ **Gemini model-map fix** (`e9011ee`) + **fence-strip** (`206a40b`): Gemini was 404ing (retired
>   preview) AND wraps JSON in ```json fences. Both fixed; verified Gemini returns clean parseable JSON live.
> - ⏪ **Scout flips + the 4 feature overrides: APPLIED then REVERTED.** Live test (`run_scout` on Gemini)
>   surfaced a deeper layer: **the scout output contract is implicitly Claude-tuned.** Gemini parses fine
>   now, but emits `reasonCodes` as bare strings + an extra `districtId` field, which the scout's
>   `normalize_intake_payload` / Pydantic validator REJECTS (so every item rejected → would silently
>   degrade the signal pipeline). Validation failure is NOT a provider error, so the 429 net does not
>   catch it. Reverted all 6 scout rows to claude-code/claude-haiku-4-5 and deactivated the 4 overrides.
>   Nothing left on Gemini. cost_events confirmed Gemini DID serve the calls (tokens recorded) before revert.
>
> **→ REAL NEXT STEP (the actual R3 completion): per-feature Gemini OUTPUT HARDENING.** The prompts/schemas
> were tuned to Claude's formatting. To move each feature to Gemini, either (a) make the validator lenient
> (coerce string `reasonCodes` → expected shape; tolerate/parse extra fields like `districtId`/
> `campaignFamily`), and/or (b) make the prompt explicit about the exact JSON schema. Do it ONE feature at
> a time with a live `run_scout`/call + cost_events proof that it (i) lands on Gemini AND (ii) produces a
> VALID accepted result — not just a 200. Start with one classification scout; the memory/meeting features
> have the same Claude-tuned-schema risk and need the same per-feature validation before flipping.
> Caveat reminder: GEMINI + CODEX only; no lm-studio until Ares.
>
> **C-followup:** route Floating Artemis (personal Artemis chat) → personal account — the `complete()`
> path C1 didn't cover.
>
> **Rate-limit note:** start on Gemini free tier with 429-fallthrough; a paid key is pennies/mo at this
> volume and lifts the limit ~100× if we ever see real throttling.

### Track R — provider/cost rebalancing (independent quick win)
- **R1 · Enable Gemini.** ✅ **DONE 2026-06-15.** `GEMINI_API_KEY` in `.env`; health = "key configured".
- **R2 · Local LLM endpoint configurable + on Studio.** ✅ **DONE 2026-06-15** (`7e5fe97`).
  `ARTEMIS_LM_STUDIO_BASE_URL` env setting, pointed at the Studio's Tailscale IP; lm-studio health
  passes (20ms). Mini's local LLM unloaded.
- **R3 · Apply the rebalancing flips. ← NEXT (paused for the instability bug; see RESUME block above).** Set `feature_routing_overrides` / per-agent `provider`
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
- **C1 · Per-agent account in the adapter.** ✅ **DONE 2026-06-15** (`4d1db1a`). `CLAUDE_CONFIG_DIR`
  set per call. **Scope gap:** covers the `run_with_tools` (scout/agent) path only — the
  Floating-Artemis/Builder `complete()` chat path still needs the C-followup (route personal Artemis →
  personal account).
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
