# Brief: GCal token REFRESH fails 401 → calendar blind (for terminal / calendar-auto-refresh lane)

**Owner:** terminal (actively on Google Calendar auto-refresh). Filed by Opus Lead 2026-06-16.

## Symptom
Artemis's morning brief reports "no meetings today" while Jon is booked all day. The gcal integration is
`active` (jon.fila@amiralearning.com) with a refresh token present, but the **access token is expired** and
the **refresh is failing**, so `gcal_events_cache` is stale (38 old events; today's never synced).

## Root signal (from `~/Library/Logs/artemisos/app.err.log`)
Every gcal read path dies the same way — at the token **refresh**, not the API call:
```
Failed to sync gcal_events_cache → integrations/gcal/sync.py:64 sync_recent_gcal_events_cache
  → integrations/gcal/client.py:128 list_events → :64 _get → :46 _refresh
httpx.HTTPStatusError: Client error '401 Unauthorized' for url 'https://oauth2.googleapis.com/token'
```
Same for `find_recently_ended_meetings`. So calendar worked right after Jon's reconnect (fresh access token,
no refresh needed) but goes blind as soon as `_refresh` is required.

## Likely cause (verify)
`gcal/client.py:_refresh` posts `grant_type=refresh_token` with `self._client_id`/`self._client_secret`.
A 401 on a *fresh* refresh token is almost certainly **invalid_client** — the client used for refresh ≠ the
client that ISSUED the token. The connect/token-exchange was fixed this session to use the **DB client
`975559492379`** (we removed the `.env` Google-client override). If `_refresh` (or whatever builds the gcal
`Client`) still resolves `client_id`/`client_secret` from a different/stale source (env-first, a cached
config, or `resolve_gcal_config` vs `resolve_google_oauth_client_config` divergence), it sends the wrong
client on refresh → 401. This is the documented two-resolver mismatch (memory: working client = 975559492379).

## Suggested fix
Make `_refresh`'s client_id/secret come from the SAME resolver/source as the OAuth connect/token-exchange
(the DB client 975559492379), so the refresh client matches the issuing client. Capture the Google error
BODY on 401 (it'll say `invalid_client` vs `invalid_grant`) to confirm — the connect path now surfaces it
(commit `47dc2ba`), the gcal client `_refresh` still swallows it via bare `raise_for_status()`. Then re-run
`sync_recent_gcal_events_cache` and confirm today's events populate. (If it's `invalid_grant`, the refresh
token was revoked → Jon must reconnect; but the connect just succeeded, so invalid_client is the bet.)

## Note
Marketing credential `amiracentral@` (purpose=marketing) is also expired + was 401 earlier — Jon hasn't
reconnected it; that's a separate pending user action, not this bug.
