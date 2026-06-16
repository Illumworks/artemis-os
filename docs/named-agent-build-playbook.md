# Named-Agent Build Playbook

How to add a Named Agent (like Callie or Kai). Distilled from building **Kai (Chiron)** end-to-end on
2026-06-16 (persona → live in Slack in one session). The floating-agent loop is **parameterized by
`agent_id`**, so adding an agent is mostly config + (for a new domain) a tool set. **No new events endpoint,
no new turn loop.** Read this before building the next agent (Hestia, Sales, etc.).

## What's config vs real build
- **Config (fast):** persona file, scope-policy entry, surface allowlist, Slack app + integration row.
- **Build (only for a new domain):** the agent's tool set + any data store/sync its tools read (Kai needed an
  `enablement_assets` store + a Sheet→DB sync; a purely-conversational agent needs neither).

## Steps
1. **Persona** — `\<agent_id\>-personality-profile.md` at repo root. `load_agent_profile(agent_id)`
   (`artemis/floating_artemis/personality.py`) finds it via the `{agent_id}-personality-profile.md` fallback.
   Add the agent to `_AGENT_DEFAULTS` for the display name + persona_core.
   - **GOTCHA:** `_parse_voice_corpus` only matches ASCII straight quotes. **Curly/typographic quotes** (“ ”)
     in a persona's example phrases → `voice_corpus` ends up empty (the phrases still reach the prompt via
     `profile_text`, so it's cosmetic). Use ASCII quotes in persona files, or extend the parser.
2. **Scope** (`artemis/identity/scope_policy.py`) — add `allowance_for_agent_\<id\>()` returning a fail-closed
   `ScopeAllowance` (only the scope kinds it needs; NO `personal:*`, `agent:artemis`, or `allow_all` unless
   intended). Wire it in `allowed_scopes_for_agent()` before the deny fallback. Mirror `callie`.
   **Re-verify adversarially:** the agent gets ONLY its scopes; existing agents unchanged; unknown/empty → denied.
3. **Surface allowlist** (`artemis/floating_artemis/session_scope.py`) — add `"\<id\>": frozenset({...})`
   (empty set = no web surfaces; tools register unconditionally).
4. **Tools** — create the agent's tool module; register in `build_authorized_tool_registry`
   (`artemis/floating_artemis/tool_registry.py`) gated on `agent_id == "\<id\>"`. **For a read-only agent,
   register ONLY read tools** — that's the structural guarantee it can't create/act (Kai = 2 read tools, no
   creation). Verify the registry contains exactly the intended tools and nothing inherited.
5. **Slack app + integration row** —
   - Duplicate an existing agent's Slack manifest (**Callie's is current/complete** — it already has the
     private-channel events).
   - **Event Subscriptions → Request URL:** `https://app.artemisos.me/api/integrations/slack/events/\<id\>`
     — the `/events/{agent_id}` route is parameterized; **no new endpoint needed.**
   - Subscribe to `app_mention` + `message.channels`. **GOTCHA (bit Callie AND Kai): PRIVATE channels fire
     `message.groups`, not `message.channels`** (and need `groups:history` scope). If the agent works in any
     private channel, add `message.groups`. (@mentions work via `app_mention` regardless; plain channel
     messages need the right `message.*` event.)
   - Get `team_id` + `bot_user_id`: `curl -H "Authorization: Bearer <bot_token>" https://slack.com/api/auth.test`.
   - Insert the row: `repo.upsert_integration(session, provider="slack", workspace_id=<team_id>,
     agent_id="\<id\>", encrypted_credentials=encrypt_credentials(creds), bot_user_id=<bot_user_id>,
     display_name="<Name>")` then `await session.commit()`.
     - **GOTCHA: use `artemis.integrations.crypto.encrypt_credentials` (returns BYTES)** for the integrations
       table (bytea). NOT `artemis.connectors.encryption.encrypt_credentials` (returns str). Two crypto
       modules exist; picking the wrong one fails decrypt at read time.
     - `creds` = `{bot_token, signing_secret, bot_user_id, allowed_channel_ids: [...], listen_channel_messages: true}`.
     - **`signing_secret` is REQUIRED for non-artemis agents** — `_resolve_agent_slack_config` raises
       ValueError without it. Get it from the Slack app → Basic Information → Signing Secret.
     - **Per-agent channel allowlist** comes from `creds.allowed_channel_ids` (or row metadata) and OVERRIDES
       the hardcoded `_default_allowed_channel_ids` (which only lists Callie's channels). Set
       `listen_channel_messages: true` so the agent answers plain channel messages, not just @mentions.
   - **Invite the bot** to its channel(s) (`/invite @Name`).
6. **Activate:** insert the row (read live from DB) → **RESTART the app** (loads the merged agent code:
   scope/tools/persona — the row alone isn't enough) → Jon does the Slack-side (Request URL, events, invite)
   → **live-test the real loop:** @mention AND a plain channel message (the plain one verifies `message.groups`
   for private channels). Check `~/Library/Logs/artemisos/app.err.log` if no reply.
7. **Per-person memory is free** — speaker attribution (`[SPEAKER]`) is built into the named-agent loop, so a
   new agent remembers individuals' prior asks automatically.

## Verification discipline (don't skip)
- Scope is security-critical → re-verify the deny paths yourself (`allowed_scopes_for_agent` direct call),
  don't trust a worker's report.
- Read-only agent → confirm the tool registry has ONLY read tools.
- Live-test the actual Slack inbound→reply loop, not just unit tests.

## Reference build (Kai)
Store/sync/shell/tools: `artemis/enablement/`. Persona: `kai-personality-profile.md`. Registration map:
`docs/kai-agent-registration-map.md`. Plan: `docs/enablement-kai-build-plan.md`.
