# Callie Build Plan (Chapter 2 / P4 — second Named agent)

**Status:** SCOPE (Jon-aligned 2026-06-10). Grounds the P4 "Callie" build from `artemis-pa-build-plan.md`
and `agent-slack-architecture.md` against the actual code. Persona is committed
(`callie-personality-profile.md` v1.1.3). No build off this doc until Jon greenlights phase order.

## Goal
Bring **Callie** online as a second Named agent: always-on and conversational in the **marketing Slack
channels**, the analyst (not the ticker), reporting only to Artemis, able to delegate to faceless workers.
Artemis comes out of the marketing channels once Callie holds them.

## Named Agent Standard — Callie checklist
1. Persona + avatar — persona DONE (v1.1.3); **avatar TODO (Jon generates/approves)**.
2. Memory — keystone, scoped to her marketing domain (incl. the retired DM marketing history handed to her).
3. Proactivity — analyst nudges/digests; full proactivity ties to P2 (later).
4. Agency behind the propose→confirm gate — she drafts/proposes; side-effects gated.
5. Slack presence — **her own bot/token**, live in the marketing channels.
6. Orchestration-awareness — reports up to Artemis; delegates to workers; bounded.
7. Defined domain + authority — marketing lane only; autonomy levels per her persona (0-3).
8. Self-improving (designed-in) — her persona/prompts are discrete evolvable units; capture traces (P6 later).

## What already exists (reuse, don't rebuild)
- The FA agent loop (`floating_artemis/chat.py::handle_turn`) is ~85% persona-generalizable.
- `session_scope.py` already splits DM (`D…`) vs channel (`C…`) and scopes surfaces.
- Marketing tools (`floating_artemis/tools/marketing.py`) exist, gated by marketing surfaces.
- `spawn_subagent` (ephemeral workers) + an `agents` table (`builders/models.py`) exist for delegation.
- The integration model has `bot_user_id`; inbound receiver + P1 guards (bot-self filter, dedupe) are live.

## What's net-new
- Persona-parameterized loop (load persona/voice/scope by `agent_id`).
- Multi-bot Slack routing (a second token; route channel→Callie, DM→Artemis; pick the right reply token).
- Writing Studio **read** tools (Message Compass, claims register, Coherence Map) + performance reads.
- Callie's analyst Slack posting (synthesis, NOT re-announcing the raw ticker).
- Escalation (Callie→Artemis) + a delegate-to-named-worker tool.

## Human dependency (Jon, like P1)
**Create Callie's Slack app + bot token**, name/avatar = Callie, add her to `campaign signals`
(C0B9CHVC7KQ) and `Marketing Campaigns`, subscribe to the channel message + app_mention events, point the
Request URL at our events endpoint. We store her token as a second Slack integration. Needed for phase C2.

## Phases

### C1 — Generalize the agent loop (no behavior change) — Codex, NO token needed
Parameterize the loop by `agent_id` ("artemis" default):
- Extract Artemis's hardcoded `_PERSONA_CORE` (chat.py:57-93) and the DM "orchestrator" line so persona core,
  full profile, voice corpus, and display name are loaded per agent.
- `personality.py`: a `load_agent_profile(agent_id) -> (profile_text, voice_corpus, persona_core)`; parameterize
  `select_voice_samples` by corpus.
- `_build_system_prompt` takes the agent's persona; `handle_turn` reads `agent_id` from session metadata
  (default "artemis"). **Default path = byte-for-byte Artemis behavior** (verify: existing FA suite green).
- Acceptance: all current FA/Slack tests green; Artemis unchanged; a unit test proves a "callie" agent_id loads
  Callie's persona + marketing scope. **This is the foundation; build first, ships safely on its own.**

### C2 — Multi-bot Slack routing — Codex, needs Callie's token (C0)
- Relax `integrations` uniqueness (allow a 2nd Slack bot per workspace; distinguish by `bot_user_id`/kind).
- `select_agent_for_session(channel_id, metadata)`: marketing channels → "callie"; Jon's DM → "artemis".
- `route_inbound`: include bot/agent in session key + metadata; resolve the **correct reply token** for the
  bot the event targeted; keep all P1 guards (bot-self filter per bot, dedupe, channel-appropriate auth).
- Callie's surface scope = marketing surfaces (the inverse of the personal-DM scope).
- Acceptance (Lead verifies LIVE): Callie replies in `campaign signals` as herself, marketing-scoped; Artemis's
  DM still personal; no cross-talk, no echo.

### C3 — Callie's domain tools — Codex
- Writing Studio **read** tools: `get_message_compass`, `search_claims_register`, `check_coherence`; campaign/
  pipeline **performance** reads. Gate by marketing surface.
- Analyst Slack posting tool (synthesis/digests/nudges) — explicitly NOT the raw signal ticker or approval
  cards (those stay with the pipeline; see `briefs/slack-signal-routing.md`).
- Acceptance: Callie can pull brand sources + claims, tier a claim, and post a synthesized recommendation.

### C4 — Orchestration: report-up + delegate — Codex
- Escalation Callie→Artemis (a decision that needs Jon flows Callie→Artemis→Jon's DM, per the tightened
  rule in `agent-slack-architecture.md`); Artemis can query Callie.
- A delegate-to-worker tool (wrap `spawn_subagent` / named-agent invoke) so Callie farms scoped tasks out.
- Acceptance: Callie escalates a decision to Artemis; Callie delegates a draft to a worker and synthesizes it.

## Cross-cutting / fold-in
- **Retired DM history handoff:** the `callie_handoff_pending` backlog (tagged in slice 1) becomes Callie's
  marketing memory when she's live (C2/C3).
- **Channel re-scoping:** fold `briefs/slack-signal-routing.md` — `incoming signals` = pipeline ticker;
  `campaign signals` = Callie. Remove Artemis from the marketing channels once Callie is steady (Jon).
- **Avatar:** Jon generates/approves Callie's image.
- **Proactivity (P2) + self-evolution (P6):** layer on later; C1's parameterized persona is the evolvable unit.

## Recommended start
**C1 now** (pure refactor, no token, ships safely, unblocks everything). In parallel, Jon creates Callie's
Slack app/token (C0) so C2 can follow immediately. C3/C4 after she's conversational.
