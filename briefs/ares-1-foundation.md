# Brief — Ares #1: Foundation (rename Forge + register the owner-private agent)

**Owner:** terminal opus (Lead). May decompose to Sonnet workers in isolated
worktrees. **Read first:** `docs/ares-architecture.md` + `docs/named-agent-build-playbook.md`.

**Goal:** Ares exists as an owner-private named agent, lives in the renamed
**Forge** surface, and responds in Slack (DM + #forge). No coding capability yet —
that's Brief 2. This is the low-risk identity/access foundation (Kai is the template).

## Scope (in-lane only)

1. **Rename the surface display label** "Dev Projects" → "Forge":
   - `public/js/core/navigation.js:96` (the `label` in `PRIMARY_NAV_DESTINATIONS`).
   - KEEP the slug `dev-projects` (id stays `DEV_PROJECTS_VIEW`) — routing is slug-driven.
   - Sweep for other user-visible "Dev Projects" strings (e.g. `public/js/panels/dev-docs.js`)
     and update copy only. Do NOT rename routes, tables, or the slug.

2. **Register Ares as a named agent** (follow the playbook steps 1–6):
   - `personality.py`: add `ARES_PERSONA_CORE` (inline ASCII-quoted string, distilled
     from the existing `ares-personality-profile.md`) + an `_AGENT_DEFAULTS["ares"]`
     entry (display_name "Ares", persona_core, profile_filename
     `ares-personality-profile.md`). The persona MUST include the shared
     **"acting means calling a tool"** discipline (it's injected globally via
     `_build_system_prompt`, so no per-persona copy needed — just don't contradict it).
   - `artemis/identity/scope_policy.py`: add `allowance_for_agent_ares()` (copy the
     Kai template) granting **owner-private** scope — `agent:ares` + Jon's
     `personal:*`. Decide whether Ares may READ `agent:artemis` (recommended: yes,
     so Ares shares Artemis's context) but coworkers/other agents must NOT read
     `agent:ares`. Wire into `allowed_scopes_for_agent()` before the deny fallback.
   - `artemis/floating_artemis/session_scope.py`: `_AGENT_SURFACE_ALLOWLIST["ares"]
     = frozenset({"dev-projects"})`.
   - Tools: for THIS brief, register only the safe read tools Ares needs to converse
     (`query_memory`, etc.). Coding tools come in Brief 2. Do NOT give Ares
     marketing/enablement/OKR tools. Register via `tool_registry.build_authorized_tool_registry`
     (an `agent_id == "ares"` branch if needed; do NOT disturb Kai's special-case registry).

3. **Slack integration (DM + #forge):**
   - Jon will create the Ares Slack app + install it and provide team_id, bot_user_id,
     tokens, signing_secret (coordinate — this is the human-in-the-loop step).
   - Insert the integration row with `artemis.integrations.crypto.encrypt_credentials`
     (bytes!), `agent_id="ares"`, `display_name="Ares"`, signing_secret set.
   - Event routing endpoint: `/api/integrations/slack/events/ares` (mirror Kai/Callie).
   - For the private **#forge** channel: subscribe to `message.groups` + add
     `groups:history` scope (private channels don't fire `message.channels`).

## Constraints / gotchas
- Owner-private like Artemis, NOT marketing-scoped like Callie. Verify
  `allowed_scopes_for_agent("ares")` grants ONLY intended scopes (call it directly).
- Circular imports: lazy provider imports in any Ares tool module.
- ASCII quotes only in the persona.
- After merge: restart with `-k`, verify pid changed + `/healthz` 200.

## Verification (observe the EFFECT, per our standard)
- `import artemis.main` succeeds.
- `allowed_scopes_for_agent("ares")` returns the owner-private allowance; a
  coworker/Callie scope set does NOT include `agent:ares`.
- Forge label shows in the nav; the surface stays owner-gated (non-owner → 403).
- LIVE: @mention Ares in #forge and DM him — he replies in persona, on-topic, once
  (no double-reply, no reply to edited messages). Confirm via the EFFECT (a real
  Slack reply), not logs.
- Unit test: Ares registered with the intended scope + surface allowlist + tool set.

**Deliverable:** committed; report files changed, the scope/surface/tool decisions
made, Slack wiring status (what Jon still needs to do app-side), and the live @mention/DM result.
