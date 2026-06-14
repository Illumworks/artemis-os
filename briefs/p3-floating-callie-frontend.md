# Worker Brief — Floating Callie frontend (D11 UI; completes M3)

**Owner:** terminal (frontend). **Lead:** Opus verifies in a real browser + merges.
**Isolation:** own worktree (`worker/floating-callie-frontend`); commit before reporting; do-NOT-merge.
**Status:** READY. Independent of the P3 backend briefs — can fire any time.

## Why
M3 (merged + live) already enforces this on the BACKEND: `create_session` resolves `agent_id` server-side from
the caller's identity — owner → `artemis`, any other authed user → `callie` — and a marketing caller's memory/
tools are gated to Callie's scope (D11). **But the floating WIDGET still presents the Artemis persona to
everyone.** This brief makes the UI match: marketing teammates should SEE Floating **Callie** (name, avatar,
greeting, color), the owner sees Floating **Artemis** — unchanged. This is cosmetic/UX; the security is already
enforced server-side, so this cannot create a leak — but it removes the confusing "why is Artemis talking to
me" experience for the team.

## Contract (already available — do not change the backend)
- `getSession(sessionId)` / `createSession(...)` in `public/js/core/floating-artemis-api.js` return the session
  JSON, which includes the server-resolved `metadata.agent_id` (`"artemis"` or `"callie"`). TRUST THIS VALUE —
  it is authoritative and server-set. Do NOT let the client pick or override the persona.
- Backend persona source of truth: `*-personality-profile.md` (artemis v1.2.2, callie v1.1.3) and
  `load_agent_profile(agent_id)`. If a small JSON/endpoint is needed to feed name/avatar/greeting/accent-color
  to the frontend by agent_id, add a tiny read endpoint (owner-agnostic, returns only display fields) rather
  than hardcoding Callie's identity in JS.

## Build
1. After session create/resolve, read `metadata.agent_id` and render the widget persona from it: assistant
   **name** (Artemis vs Callie), **avatar**, **greeting/empty-state**, and any accent color/branding in the
   floating widget header + message attribution. Locate the floating widget render (search `public/js/` — the
   floating-artemis surface; `parallel.js` handles its WS events) and the persona/header element.
2. Default/fallback persona = **Callie**, NOT Artemis (fail toward least-privilege presentation, mirroring the
   backend's fail-closed `agent_id` default). Never show Artemis branding to a non-owner.
3. Do not touch the OKR/marketing/tool gating or the WS event plumbing — persona/display only.

## Verify (REQUIRED — real browser; the floating UI is hard-won, see SESSION-STATE composer warning)
- As the OWNER identity: widget shows **Artemis**, full behaviour unchanged (regression check).
- As a MARKETING identity (simulate a non-owner CF-Access session / a session whose server-resolved
  `agent_id="callie"`): widget shows **Callie** — name, avatar, greeting. Confirm no Artemis branding leaks
  into the marketing view. Screenshot both.
- Confirm nothing about memory/tool access changed (M3 backend untouched).

## Report back
Branch + commit; the files changed; before/after screenshots (owner→Artemis, marketing→Callie); confirmation
the persona is driven by the server-resolved `metadata.agent_id` and defaults to Callie; real-browser
regression note for the owner's existing widget. Do NOT merge.
