# Callie Build Plan (Chapter 2 / P4 — second Named agent)

**Status:** SCOPE (Jon-aligned 2026-06-10). Grounds the P4 "Callie" build from `artemis-pa-build-plan.md`
and `agent-slack-architecture.md` against the actual code. Persona is committed
(`callie-personality-profile.md` v1.1.3). No build off this doc until Jon greenlights phase order.

## Goal
Bring **Callie** online as a second Named agent: always-on and conversational in **`campaign signals`
(C0B9CHVC7KQ)**, **`Marketing Campaigns`**, and **her own Slack DMs** — the analyst (not the ticker),
reporting only to Artemis, able to delegate to faceless workers. Artemis comes out of the marketing channels
once Callie holds them.

**Callie's Slack app (C0 done, 2026-06-10):** "Calliope", App ID `A0B9Q790Y9Y`, Client ID
`157781284437.11330247032338` — created by **duplicating Artemis's manifest**, so her app points at the
**same** events endpoint. Consequences for C2: events must be routed by **`api_app_id`** (Artemis vs Callie),
and HMAC verified with **her own signing secret**. Secrets (signing secret, bot OAuth token) are stored
**encrypted in the DB** (a second `integrations` row, via the OAuth install flow) — never in the repo/briefs.

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
- Relax `integrations` uniqueness (allow a 2nd Slack bot per workspace; distinguish by `bot_user_id`/app).
- **Dedicated per-bot endpoint (decided 2026-06-10):** Artemis stays on `/api/integrations/slack/events`;
  Callie gets `/api/integrations/slack/events/callie` (same handler mounted at a 2nd path, agent resolved from
  the path). Each path HMAC-verifies with **its own signing secret** and replies with **its own token** — no
  shared-handler `api_app_id` guessing. (Her manifest is an Artemis clone, so a distinct path is the clean
  separator. `api_app_id` routing on one endpoint is the viable alternative, not chosen.) Jon repoints her
  Request URL to the `/events/callie` path once C2 deploys it.
- `select_agent_for_session(api_app_id, channel_id, metadata)`: Callie's app → "callie"; Artemis's app → 
  "artemis". Include agent/app in the session key + metadata so the two bots never collide on a session.
- **Make DM scope agent-aware (fixes a slice-1 assumption):** `is_personal_slack_dm_session` currently treats
  ANY `D…` channel as personal/marketing-stripped. That is only right for **Artemis's** DM. Callie's own DMs
  must be **marketing-scoped**. Scope must key off agent_id: Artemis-DM → personal; Callie-DM/channels →
  marketing.
- Keep all P1 guards (bot-self filter per bot, dedupe, channel-appropriate auth).
- Acceptance (Lead verifies LIVE): Callie replies as herself (marketing-scoped) in `campaign signals`,
  `Marketing Campaigns`, and her own DM; Artemis's DM stays personal; no cross-talk, no echo.

### C3 — Callie's domain tools + content wiring — Codex (split, grounded 2026-06-10)
Read APIs all already exist (clean): Message Compass = `writing_rules.repository.get_source_by_profile_key(
…, "01_MESSAGE_COMPASS")`; claims = `list_claims(profile_id, status="approved")`; Coherence Map is embedded
in the Compass (no separate API). Tools register in `floating_artemis/tools/marketing.py` (gate
`[surface:marketing-os]`). Split:
- **C3a — analyst toolset (READY, do first):** `briefs/callie-c3a-analyst-toolset.md` — `get_message_compass`,
  `search_claims_register`, `get_campaign_performance` (synthesized from raw reads; no aggregate metric API
  yet), and `post_analyst_message` (posts synthesis to her channel via HER token, lint-clean). Makes Callie a
  real analyst.
- **C3b — finish notification routing:** post the pipeline Gate-2 *channel card* via Callie's token
  (`human_gate_executor.py`: refactor `_get_slack_token` → `_get_slack_token_for_agent(agent_id)`; marketing
  kinds post as Callie). Completes QW1 (which only suppressed the owner-DM).
- **C3c — retired-history handoff:** ingest the `callie_handoff_pending` Artemis-DM backlog
  (`slack-…-D0AN8CCJC4C-_`, ~246 msgs) into Callie's memory scope (`floating_artemis/memory.py` write path
  + `memory/store.py`). Unknown: scope-inheritance semantics — trace `memory/retrieval.py` first.
- **C3d — editable-draft body (deferred QW2). CORRECTED 2026-06-10 (no external backend / no Google Docs).**
  A composer "draft" IS a `campaign_deliverables` row (invoke.py:54), content in `deliverable_metadata`; the
  composer reads it via `_latest_draft_content`. The empty drafts = the deliverables pipeline wrote the
  composed body to a field/shape that `_latest_draft_content` does NOT read (deliverable 42 had it, 43-45
  didn't). Fix = ALIGN where the pipeline writes the composed body with where the composer reads it
  (`_latest_draft_content`), and ensure all generated deliverables get a composed body, not a stub shell. The
  `external.py` Stub/Real adapter + `ARTEMIS_WRITING_STUDIO_URL/TOKEN` are a separate, largely-unused
  abstraction — NOT required here. The composer's "Google Doc" button is an optional export, unrelated. So
  C3d is a contained pipeline/DB-shape fix, no backend decision. (Earlier note wrongly tied this to Google
  Docs — retracted.)

### C4 — Orchestration: report-up + delegate — Codex
- Escalation Callie→Artemis (a decision that needs Jon flows Callie→Artemis→Jon's DM, per the tightened
  rule in `agent-slack-architecture.md`); Artemis can query Callie.
- A delegate-to-worker tool (wrap `spawn_subagent` / named-agent invoke) so Callie farms scoped tasks out.
- Acceptance: Callie escalates a decision to Artemis; Callie delegates a draft to a worker and synthesizes it.

## Marketing notification routing (hard requirement, surfaced 2026-06-10)
The pipeline posts Gate-2 approval cards via `human_gate_executor.py` two ways using the **Artemis bot**:
(1) a **DM to each approver by email** — since Jon is the approver, this lands in his PERSONAL Artemis DM
(violating "Artemis DM = personal/ops only"); (2) a post to `marketing_campaigns_slack_channel`. This is a
SEPARATE path from the conversational loop — slice-1's personal-DM scoping does NOT catch it (slice-1 scoped
`handle_turn`; these cards are posted directly via `SlackClient`). **C2/C3 must route marketing gate
notifications to Callie's marketing channel (posted by Callie's bot), NOT Jon's Artemis DM**, and stop the
approver-DM-to-Jon for marketing gates. Until then, marketing approval cards will keep leaking into the
personal DM on every campaign.

## Deliverable -> Writing Studio draft body gap (surfaced 2026-06-10)
For campaign #18, the deliverables pipeline generated real content (it's in `campaign_deliverables.metadata`
and rendered in the Slack approval cards), but the editable Writing Studio drafts came out body-empty because
the **stub external writing adapter** (`StubWritingStudio`, the default when its backend env is unset) creates
title-only draft shells and never pipes the generated body into the WS draft. Result: Slack card has text, WS
draft is empty. Fix path: engage the real compose/external path so the generated body lands in the WS draft
(tie to C3 Writing Studio wiring). Content is NOT lost (recoverable from deliverable metadata).

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
