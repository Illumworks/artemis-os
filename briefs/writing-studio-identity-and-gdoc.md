# Design — Identity (Cloudflare + Google SSO) + Google Doc import/export

**Status:** ROADMAP design. Captured 2026-06-06 with Jon. Two questions that share ONE answer: **set up
Google SSO via Cloudflare Access** — it gives per-user identity (for comments/@mention/attribution) AND the
Google auth needed for Doc import/export. Infra/auth-sensitive: Jon owns the Cloudflare config; Lead builds
the app-side reading/verification. Sequence with the composer build.

## Current state (verified)
- App is fronted by **Cloudflare Access**, policy = Jon's email only. Internal API uses a single shared
  Bearer token (`require_token`, `ARTEMIS_TOKEN`). **No per-user identity in the app.**
- **Google Doc sync NOT built** — `compose_engine.py:382` hardcodes `has_linked_google_doc=False` ("Python
  rebuild: no Google Doc sync yet"). The Node app had it; unported.

## Q1 — Who's making a comment? → Identity via Cloudflare Access + Google SSO
We do NOT need to build a full account system. Cloudflare Access IS the identity provider:
- **Jon (Cloudflare config):** switch the Access policy to **Google login** and add the team (Angela, Josh)
  to the allowed list. (Jon's "change the Cloudflare credential.")
- **App (Lead build):** Cloudflare passes the authenticated user to the app on every request —
  `Cf-Access-Authenticated-User-Email` + a signed JWT `Cf-Access-Jwt-Assertion`. The app **reads + VERIFIES
  the JWT** against Cloudflare's public keys (so identity can't be spoofed by hitting the app directly,
  bypassing Cloudflare) and extracts email + name. That becomes the **current user**.
- **Lightweight team directory:** seed `users` from whoever logs in (email + Google display name) → powers
  comment authorship, @mention autocomplete, ping/notify targets, and per-user attribution everywhere
  (incl. the learn-from-edits "Angela approved this claim" and Gate-2 decisions).
- **Nuance:** the automated marketing PIPELINE runs headless (no human) → those actions attribute to
  "system / Amira," not a person. Only interactive UI actions carry a user.
- This unlocks comments + @mention + audit trails + per-user attribution across the whole app — worth doing
  once. Security: trust ONLY the verified Access JWT, never a raw header.

## Q2 — How does the Google Doc link import text? → Google Docs/Drive API (same Google integration)
- **Link/import:** paste a Google Doc URL → app fetches the doc via the **Google Docs/Drive API** → converts
  to the draft format (markdown/HTML preserving headings/bold/lists) → populates the draft body. Keep the
  doc↔draft link for re-import.
- **Export:** push the draft content back to a new or linked Google Doc (Docs API create/update).
- **Auth:** Google OAuth with Docs/Drive scopes — and since we're already doing Google SSO for identity, the
  **same Google integration** can carry the Docs/Drive scopes (read/write as the user), or a service account
  with shared access. Do identity + Docs together.
- **v1 scope:** one-way import (pull text in) + export (push out), link retained; **defer live two-way
  sync** (harder). Import brings the TEXT only — not Google's native comments (our comments are separate).
- Wire `has_linked_google_doc` through the compose engine (it already branches on it) so a linked doc
  informs the agent.

## Why these are one piece of work
Google SSO (Cloudflare Access) + Google Docs/Drive scopes = the same Google OAuth setup. Doing them together
gives: real per-user identity (comments/@mention/attribution) AND Doc link/import/export — both gated behind
the team's Google login. Jon flips the Cloudflare policy; Lead builds the JWT verification, the user
directory, and the Docs import/export.

## Q3 — Multiple people at once + collaboration (don't step on toes) — Jon 2026-06-06
Yes, once Google SSO is on, the team can use Artemis simultaneously (separate sessions; Postgres handles
concurrent requests). **Different drafts = no conflict.** The only risk is two people on the SAME draft.
Recommended handling, simplest → fullest:

- **v1 — Presence + soft-lock + version-guard (recommended, achievable):**
  - **Presence:** show who's viewing/editing a draft — Google-Docs-style avatars ("Angela is editing"),
    via a lightweight heartbeat (poll/SSE). Depends on the identity work (Q1).
  - **Soft-lock:** when one person is actively editing, others see "Angela is editing" and open in
    read-only/warn mode with a "request edit / take over" option — so two people don't type at once.
  - **Version-guard (optimistic concurrency):** each save carries the draft's version/updated_at; a stale
    save is rejected with "this draft changed — reload/merge" instead of silently clobbering. This is the
    real anti-toe-stepping safety net.
  - **Async collaboration already covered:** the comments + @mention/ping layer (composer design) lets the
    team collaborate without co-editing the same text — most "collaboration" happens there.
- **v2 (defer unless truly needed) — full real-time co-editing** (live multi-cursor, character-merge, à la
  Google Docs): requires CRDT/OT + websockets (e.g. Yjs). Big lift; usually overkill for a small marketing
  team that rarely co-writes the exact same draft live. Presence + soft-lock + version-guard meets the
  stated need ("see who's working, don't step on toes") without it.

**Recommendation:** ship v1 (presence + soft-lock + version-guard) with the identity work; revisit full
live co-editing only if the team finds they genuinely need simultaneous co-writing.

## Constraints
Auth-sensitive — Jon owns the Cloudflare credential/policy change; Lead never touches Cloudflare creds.
App must VERIFY the Access JWT (no trusting raw headers). Lossless (users append; no destructive auth
migration). Org dep rule (Google API client lib — nothing <7 days old). Sequence alongside the composer
(comments/@mention depend on identity).
