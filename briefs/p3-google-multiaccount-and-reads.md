# Worker Brief — P3 Foundation: multi-account Google + Calendar/Gmail reads

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies live + merges.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/p3-google-multiaccount`; **commit your work
on the branch before reporting**, then do-NOT-merge-report. Adds a migration → **Lead applies `alembic upgrade
head` on prod post-merge** (`--reload` won't).
**Status:** READY. This is the **read/connection foundation** that must land before P3 agency-*writes*.

## Why (diagnosis, 2026-06-13)
- Only ONE Google account is connectable per user (unique `user_id` on `google_credentials`), currently
  `jon.fila@amiralearning.com` with **Docs/Drive scopes only** — **no Calendar, no Gmail.**
- → `gcal_events_cache` is **empty**; Artemis tells Jon he has "no meetings."
- → the meeting summarizer (matches GCal event → Granola → transcript → action-items) has no events to match,
  so **action-items never populate** → **the P2 commitments engine is starving** (it extracts from them).
- Gmail isn't connected at all. And marketing vs personal Google should be **separate accounts**.

## Account model (Jon's decision)
- **Personal** = `jon.fila@amiralearning.com` → Calendar, Gmail, personal docs → used by **Artemis**.
- **Marketing/central** = `amiracentral@amiralearning.com` → marketing docs/assets → used by **Callie/marketing**.

## Part 1 — Multi-account Google credentials (the architectural change)
- Relax the one-per-user constraint: allow **multiple `google_credentials` per user, keyed by a new
  `purpose`/`account_kind` column** (`personal` | `marketing`). Migration + unique on `(user_id, purpose)`.
- **Backfill** the existing `jon.fila@` row as `purpose='personal'`.
- `get_google_credential(user_id, purpose)` + **selection-by-context**: Artemis-personal ops → `personal`;
  Callie/marketing ops → `marketing`. Re-point the **Writing Studio Google-Docs export** to the `marketing`
  credential (so marketing assets land in the marketing Drive, not Jon's personal one).

## Part 2 — Connect flow + scopes (Jon re-consents during verification)
- A connect flow that takes a **purpose** so Jon can authorize a **second account** (`amiracentral@`, tagged
  marketing) without clobbering the personal one. Reconcile the two existing scope lists
  (`google_docs/client.py:GOOGLE_DOCS_SCOPES` + `routes/integrations.py` gcal scopes).
- **Personal scopes** add **Calendar** (`calendar` / `calendar.events`) + **Gmail** (`gmail.readonly` now;
  `gmail.send`/`gmail.compose` for the later writes phase) on top of Docs/Drive/userinfo.
- **Marketing scopes** = Docs/Drive/userinfo (no calendar/gmail needed there for now).

## Part 3 — Calendar read (unblocks meetings → Granola → commitments)
- Ensure calendar events **flow into `gcal_events_cache`** using the personal calendar-scoped creds. Investigate
  whether the existing path is broken (stale token / missing scope) or not built, and fix/build the fetch +
  the sync (extend `meetings/scheduler.py` / the gcal sync — do NOT add a new scheduler stack).
- Verify the **meeting summarizer** then matches events → fetches Granola transcripts → extracts action-items →
  and the **commitments engine ingests them**.

## Part 4 — Gmail read connection
- A Gmail **read** client + wire it as a usable read source for Artemis (recent messages/threads) on the
  personal account. (Compose/send is the *next* P3 slice — out of scope here.)

## Constraints
- Tokens encrypted (reuse the existing credential crypto); no hardcoded secrets. Don't break the existing
  Writing-Studio Docs flow (it just moves to the `marketing` cred). Per-user identity via the existing CF
  Access path. Commit the migration + lockfile if touched.

## Ship gate (Lead verifies LIVE; Jon performs the two consents)
- Jon **re-consents `jon.fila@`** (Calendar + Gmail added) + **connects `amiracentral@`** (marketing) → DB shows
  two `google_credentials` rows (personal + marketing) with the right scopes.
- **Calendar:** events appear in `gcal_events_cache`; asking Artemis "what meetings do I have this week"
  returns **real meetings** (not "none").
- **Granola:** transcripts + **action-items populate** for recent meetings → the commitments engine extracts
  them (verify a commitment is created).
- **Gmail:** a read returns Jon's recent mail.
- **Docs routing:** a marketing Writing-Studio export lands in the **marketing** account's Drive; a personal
  doc uses the personal account.
