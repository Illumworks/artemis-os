# Worker Brief — Encrypt Google OAuth tokens at rest (security)

**Owner:** terminal/Codex (backend). **Lead:** Opus verifies (re-confirm live Gmail+Calendar reads still work
post-migration) + merges. **Isolation:** own worktree (`worker/encrypt-google-tokens`), own test DB (name
contains `artemis_test`). Adds a migration → **Lead runs `alembic upgrade head` on prod post-merge.**
**Status:** READY. SECURITY — do NOT self-merge.

## Problem (verified 2026-06-14)
`artemis/google_docs/models.py:30-31` stores Google OAuth tokens in PLAINTEXT:
- `access_token: Mapped[str] = mapped_column(Text, nullable=False)`
- `refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)`
A `ya29.…` access token + a long-lived refresh token are readable directly in the DB. Plaintext since the
original Google table (migration 0076); the multi-account work (fafa334) inherited it. Every OTHER integration
credential is encrypted (`integrations/models.py`: `encrypted_credentials`/`encrypted_payload` BYTEA). Risk:
anyone with DB access — or a DB dump (e.g. `artemis-os-share.zip`) — has live access to Jon's Gmail/Calendar.

## Fix — reuse the EXISTING encryption helper (no new dependency)
Use `artemis/connectors/encryption.py` (Fernet via `cryptography`, key from `ARTEMIS_CONNECTOR_KEY`) — already
in the codebase. Do NOT add a new crypto lib or a second key mechanism.
1. **Storage:** encrypt `access_token` + `refresh_token` at rest (store the Fernet blob; column can stay `Text`
   holding base64 ciphertext, or move to BYTEA — match whichever is cleanest and consistent with the connectors
   pattern). Encrypt on write, decrypt on read.
2. **Migration:** add a migration that BACKFILL-ENCRYPTS the existing rows in place (the two live accounts —
   `jon.fila@` and `amiracentral@` — must keep working). Idempotent + reversible where possible. Confirm no row
   is left plaintext.
3. **Read sites:** decrypt at every consumer — `google_docs/` repo/client + the gcal + gmail clients (grep all
   readers of `access_token`/`refresh_token`). Token refresh flow must re-encrypt the new access_token it
   receives.
4. **Cleanup (roll in):** remove the unused `_GCAL_SCOPE` dead constant flagged during verification.

## Constraints / safety
- Do NOT break the two already-connected accounts. After the migration, the live Gmail read + Calendar sync
  must still work (Lead will re-verify live).
- Fail safe: if `ARTEMIS_CONNECTOR_KEY` is missing, fail loudly at startup/first-use (do NOT silently fall back
  to plaintext). Confirm the key is set in the prod `.env` before merge (Lead checks).
- Lossless: don't drop/regenerate tokens; encrypt the existing values.

## Ship gate (Lead verifies)
- No plaintext `ya29.`/refresh token in the DB after backfill (Lead spot-checks the live DB).
- Live Gmail read + Calendar sync still return real data for `jon.fila@`; Docs export still uses the marketing
  credential. Token refresh path re-encrypts correctly (test).
- `_GCAL_SCOPE` removed. Tests green. Migration applies cleanly forward.
