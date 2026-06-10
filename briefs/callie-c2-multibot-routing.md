# Worker Brief — Callie C2: Multi-bot Slack Routing (dedicated endpoint)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies LIVE + merges.
**Status:** READY once Callie's bot token is captured (Jon installs her app). **Branch:**
`worker/callie-c2-multibot-routing`. **Builds on:** C1 (merged, e41f2d9 — `load_agent_profile`, `agent_id`
in session metadata, `resolve_surface_scope`). **Plan:** `docs/callie-build-plan.md` (phase C2).

## Goal
Make **Callie** conversational as her own bot in `campaign signals` (C0B9CHVC7KQ), `Marketing Campaigns`,
and her own DMs — marketing-scoped, reusing the C1 persona-parameterized loop. Artemis (DM) is unchanged.

## Callie's Slack app
"Calliope", App ID `A0B9Q790Y9Y`. Manifest is an Artemis clone (same scopes/events). Jon installs it to the
workspace to produce a **Bot User OAuth Token** + provides her **Signing Secret**. Store both **encrypted in
the DB as a second `integrations` row** (the OAuth install flow, mirroring Artemis) — never in repo/chat.

## Scope

### 1. Dedicated per-bot endpoint
Add `POST /api/integrations/slack/events/callie` (Artemis keeps `/api/integrations/slack/events`). Factor the
existing `slack_events` handler so the agent/app is a parameter; mount it at both paths. The Callie path:
- Verifies HMAC with **Callie's** signing secret (per-app), not Artemis's.
- Resolves replies with **Callie's** bot token.
- Tags inbound as agent="callie".
Keep `url_verification` working on both paths (app-agnostic challenge echo).

### 2. Second Slack integration + token resolution
- Relax the `integrations` uniqueness constraint (`models.py:27` `UniqueConstraint(provider, workspace_id)`)
  to allow two Slack bots per workspace — distinguish by `bot_user_id` (and/or an `agent`/kind field).
  Alembic migration; commit lockfile discipline.
- Token/secret resolution selects the right integration for the agent (artemis vs callie).

### 3. Agent-aware routing + session keying
- `route_inbound` (and `_handle_mentionable_event`): carry the agent through. Set `agent_id="callie"` in the
  FA session metadata for Callie's events (C1's `handle_turn` already loads persona by `agent_id`).
- **Include the agent/bot in the session key** so the two bots never collide
  (e.g. `slack-callie-{team}-{channel}-{bucket}`), and so Callie's reply token + persona are unambiguous.
- **Bot-self filter per bot:** `_is_bot_authored` must use Callie's `bot_user_id` on her path (the echo
  guard is per-bot).

### 4. Agent-aware surface scope (fixes a slice-1 assumption)
`is_personal_slack_dm_session` currently treats ANY `D…` channel as personal/marketing-stripped. That is only
right for Artemis. Make scope agent-aware: **Artemis DM → personal (marketing stripped); Callie (channels AND
her own DMs) → marketing scope** (the marketing surfaces, inverse of personal). Update `session_scope.py`
accordingly, keyed on agent_id.

### 5. Authorization (Callie is team-facing, unlike Artemis)
Artemis's inbound is allowlisted to Jon. Callie is a marketing teammate: she should respond to anyone in her
configured channels (`campaign signals`, `Marketing Campaigns`) and in her DMs. So Callie's gate = "is this
one of Callie's channels / a DM to Callie", not the Jon-only allowlist. Keep dedupe + the bot-self filter.
(If we later want a marketing-team allowlist for her DMs, leave a seam; not required now.)

## Constraints
- Do NOT regress Artemis's P1 path (bot-self filter, Jon allowlist, dedupe, identity) or slice-1 personal DM.
- Lossless; no deps <7 days; ruff + mypy strict; `./scripts/check.sh`.
- Secrets encrypted in DB only.

## Tests
- Callie's path verifies with her signing secret, rejects Artemis-signed payloads; Artemis's path unchanged.
- A `campaign signals` message → routed to a "callie" session, marketing-scoped, persona = Callie; reply via
  Callie's token. Artemis DM still personal/Jon-only.
- Two bots in the same workspace don't collide on session keys; bot-self filter per bot kills echo.
- P1 + slice-1 regression suites stay green.

## Acceptance (Lead verifies LIVE)
After deploy + Jon repoints Callie's Request URL to `/events/callie` (and Retry verifies green): Jon (or a
teammate) messages `campaign signals` → Callie replies as herself, marketing-scoped, no marketing leakage
into Artemis's DM, no echo. Then C3 wires her domain tools (Writing Studio reads, performance, analyst
posting) + the retired-history handoff.
