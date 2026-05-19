# J10d — Connectors UI: surface `needs_reauth` distinctly + one-click reconnect

**Owner:** Worker (Sonnet)
**Scope:** ~80 LOC frontend + ~30 LOC backend. Half-day or less.
**Depends on:** J10e (OAuth refresh scheduler — already merged). The `integrations.status` column already takes the value `'needs_reauth'` when J10e detects a `REFRESH_TOKEN_EXPIRED` outcome.
**Blocks:** Nothing. Polish that closes a real recurring gap.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why this brief exists

J10e proactively refreshes OAuth tokens for Granola, Slack, GCal. When the **access token** is about to expire, J10e calls the provider's refresh endpoint, gets a new access token, and updates the integration row.

But: **refresh tokens themselves also expire** — Granola's refresh tokens are ~30 days, Slack's rotating refresh tokens roll on every use, GCal's never expire (in theory) but can be revoked. When this happens, J10e correctly catches the `invalid_refresh_token` response and:

1. Sets the integration row's `status` to `'needs_reauth'`
2. Sets `last_refresh_attempt_at` to the current timestamp
3. Leaves the encrypted credentials blob untouched (so we can inspect what was there if needed)

The integration row now correctly says "this connection needs re-OAuth" — but **the Connectors modal UI ignores the new status and still shows the integration as `active` / "Connected."** Result: user opens Meetings (or Slack triage, or Calendar), nothing works, opens Connectors expecting to see what's broken, sees everything green, gets confused.

We hit this exact pattern at session 2026-05-19. Will recur indefinitely across all OAuth providers as refresh tokens age out. This brief fixes the loop.

## Scope

### Backend — A. Expose the status in the integrations list response

- [ ] `GET /api/integrations` currently returns integration rows. Confirm the response shape includes `status` for each row. If not, extend the serializer to include it. The status enum should support at least: `active`, `disconnected`, `needs_reauth`. Other states (`pending`, `error`) can pass through as-is.

- [ ] (Optional, ~10 LOC) Add a `last_refresh_attempt_at` field to the response so the UI can show how recently we tried. Useful debugging signal for the user ("we tried 5 min ago"). Skip if response shape doesn't easily accommodate it.

- [ ] Confirm the existing `POST /api/integrations/{id}/refresh` endpoint (J10e Slice D) is what the "Try Reconnect" button should call. Verify by reading `artemis/routes/integrations.py` for the handler.

### Frontend — B. Render `needs_reauth` as a distinct state

The Connectors modal lives in `public/js/components/integrations-modal.js` (or wherever integrations are rendered — find via grep). Currently each integration row is either:
- Connected (green dot, shows account info, "Disconnect" affordance)
- Disconnected (gray, "Connect" CTA)

Add a third visual state for `status === 'needs_reauth'`:

- [ ] Amber/orange status indicator (use accent color from existing CSS variables — not red, since red implies fatal)
- [ ] Status label: "Needs reconnect" or "Re-authorization required" (pick one, match Artemis voice)
- [ ] Body text explaining what happened: "The connection expired and needs to be re-authorized. Click Reconnect to refresh your access."
- [ ] **Primary CTA: "Reconnect"** — clicking opens the same OAuth flow that the initial Connect button uses (PKCE redirect for Granola, OAuth bounce for Slack, etc.)
- [ ] **Secondary affordance: "Try refresh"** (small text button) — calls `POST /api/integrations/{id}/refresh`. If it returns `outcome: 'refreshed'`, the row should re-render as `active`. If it returns `'refresh_token_expired'`, keep the `needs_reauth` state and surface a small inline message ("Refresh failed — full reconnect needed").

### Frontend — C. Use the same status throughout the UI

If other surfaces in the app render a "Granola connected" / "Slack connected" indicator (e.g. the Focus page's small connection-status badges, the Status popover), make them aware of the `needs_reauth` state too. They should show a similar amber indicator instead of green when status is `needs_reauth`.

Investigate:
```bash
grep -rn "status.*active\|status.*connected\|integration.*status" public/js/ | head -30
```

Pick the smallest scope here — if there are many surfaces that read integration status, just fix the Connectors modal in this brief and queue surface-by-surface updates as separate small briefs. Don't bulk-rewrite everything.

### Backend — D. Add a verify endpoint (optional, only if useful)

The current `POST /api/integrations/{id}/refresh` endpoint exercises the refresh. There's value in also having a passive `GET /api/integrations/{id}/verify` that pings the provider's API with the current access token and returns `{valid: bool, reason?: str}`. This lets the Connectors modal show a real-time "is this still alive?" check without triggering a refresh attempt.

Skip this if it would push the brief over 150 LOC. The `/refresh` endpoint covers the main use case.

## Acceptance — what done looks like

1. Open Connectors modal with a `needs_reauth` integration (you can manually flip status in the DB to test: `UPDATE integrations SET status = 'needs_reauth' WHERE id = 6;`). The row renders with the amber indicator, the explanatory body text, and the Reconnect CTA.
2. Click Reconnect → the same OAuth flow opens that you'd get from a fresh Connect → completing the flow flips status back to `active` → the row re-renders accordingly.
3. Click "Try refresh" → backend `POST /refresh` fires → if successful, row updates to `active`; if refresh_token is also dead, row stays in `needs_reauth` with an inline error message.
4. Disconnected integrations still render correctly (no regression).
5. Active integrations still render correctly (no regression).
6. If you extended other surfaces (Focus badges, Status popover) to surface `needs_reauth`, verify those too.

## Quality acceptance gates

- [ ] Manual smoke output pasted verbatim in your report — including a screenshot of the `needs_reauth` state and a screenshot of the row after successful reconnect.
- [ ] `git diff --staged` before commit. Twice-bitten pattern.
- [ ] `pwd && git branch --show-current` before commit. CWD-trap defensive reflex.
- [ ] `ruff check` + `mypy` clean on the backend addition.
- [ ] No regression on Connectors modal's existing active / disconnected states.
- [ ] No regression on `GET /api/integrations` response shape — other consumers of that endpoint should keep working.

## Out of scope (separate briefs)

- **Auto-reconnect via stored credentials**: if a refresh_token expired but the user is still authenticated with the provider in their browser, we could potentially silent-OAuth without user interaction. Complex, separate brief.
- **Provider-specific re-auth flows**: some providers (e.g. Atlassian for Jira) have nuances in their OAuth re-flow. Handle on a per-provider brief if quirks emerge.
- **Notifications when an integration hits `needs_reauth`**: surfacing a toast or sidebar badge when a connection silently goes down. Useful but separate brief.

## Where to start

1. Read this brief twice.
2. Read `briefs/CONVENTIONS.md` — especially "CWD trap" and "Commit Discipline." Six commits in this project's history have been corrupted by skipping these.
3. Read `artemis/routes/integrations.py` for the J10e refresh endpoint shape.
4. Read `public/js/components/integrations-modal.js` (or wherever the Connectors modal is rendered) to map the existing component structure.
5. Backend first (response shape), frontend second.
6. Test with a manual status flip in the DB before committing.

## Why this is worth doing now

We've hit `needs_reauth` twice in two days (Granola once, Slack adjacent issues). Without J10d, every time a refresh_token ages out, the user sees:
- UI says "connected"
- Backend says "needs reauth"
- Surfaces depending on that connection silently fail or show stale data
- User has to dig through logs / curl endpoints to figure out what happened

J10d closes that loop. With it, user sees the amber state immediately, clicks Reconnect, problem solved. ~80 frontend LOC + 30 backend LOC for a class of recurring confusion that costs minutes per incident.
