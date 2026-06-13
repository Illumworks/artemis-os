# Worker Brief — Harden the personal→gcal integration mirror (no false revoke)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies live + merges.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/p3-harden-gcal-mirror`; **commit your work
on the branch before reporting**, then do-NOT-merge-report.
**Status:** READY. Small, focused robustness fix in `artemis/google_integration.py`.

## The bug (observed live 2026-06-13)
After Jon's two-pass Google re-consent, the `gcal` integration row ended up `status='revoked'` with
`scopes=NULL` **even though the personal credential has calendar scope** — which silently stops all future
calendar cache syncs (`list_active(provider='gcal')` returns nothing). Lead repaired the row by hand; this
makes it permanent.

Root cause: `sync_personal_google_integrations()` **revokes the gcal integration as a side effect of a
connect/re-consent** whenever the *consenting credential* lacks calendar scope:
```
if not has_calendar:
    UPDATE integrations SET status='revoked' WHERE provider='gcal' AND workspace_id=<email> AND agent_id='default'
    return
```
A re-consent sequence (or a token whose returned `scope` momentarily omits calendar) therefore tears down a
perfectly good, calendar-scoped integration.

## The fix — connect/re-consent must NEVER revoke
In `sync_personal_google_integrations()`:
- **Remove the revoke branch.** When the credential HAS calendar scope → upsert the gcal integration to
  `active` with the calendar scopes (unchanged). When it does NOT → **do nothing** (leave any existing
  integration as-is; do not revoke). Revocation belongs SOLELY to the explicit disconnect path
  (`revoke_personal_google_integrations`, called from `google_disconnect`) — leave that path untouched.
- Make the active-upsert idempotent and self-healing: a personal consent that includes calendar must always
  (re)activate the integration and (re)set its scopes, even if it was previously `revoked`. (`upsert_integration`
  should set `status='active'` on conflict — verify it does; if it doesn't reset status on update, fix that so a
  revoked row flips back to active.)
- Rationale: a stale `active` integration whose token lacks calendar fails *gracefully* (the sync logs + returns
  0) — a far better failure mode than silently revoking a working integration. True disconnects still revoke
  cleanly via the disconnect route.

## Tests (this is the point — reproduce the bug, prove it's fixed)
Add a regression test (mirror existing google-integration test style):
1. **Double consent stays active:** simulate two personal consents in sequence (both with calendar scope) →
   assert the gcal integration ends `active` with calendar scopes. (Today this can leave it revoked.)
2. **Re-consent heals a revoked row:** start with a `revoked` gcal integration row → run a personal consent
   with calendar scope → assert it flips back to `active` with scopes.
3. **Connect without calendar does NOT revoke:** existing `active` integration → a personal consent whose scope
   lacks calendar → assert the integration is **still active** (not revoked).
4. **Disconnect still revokes:** the explicit disconnect path → integration `revoked`. (Guard against regressing it.)

## Constraints
- Only touch the mirror logic + its tests. Don't change the disconnect route's behavior. No new scheduler.
- Isolated worktree; commit before reporting.

## Ship gate (Lead verifies)
- New tests pass. Live: a re-consent leaves `gcal` integration `active` with calendar scopes; a disconnect
  still revokes; the 2-min summarizer tick keeps `gcal_events_cache` fresh.
