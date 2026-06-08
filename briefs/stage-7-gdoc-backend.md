# Build brief — Composer Stage 7 BACKEND: Google Docs connect + import + export (Track B)

**Agent:** Codex or terminal (whichever frees first). **Branch:** `worker/gdoc-backend` off `main`. **Own git
worktree, cwd inside. Own test DB** (`artemis_test_gdoc`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` + `briefs/identity-gdoc-setup-runbook.md` (Track B). **Security-sensitive
(OAuth tokens) — store carefully; mock Google in tests, never hit live Google in CI.** New backend files + a
new router only — do NOT touch `composer-v5.js` or the comments-backend files.

## The point + scope (PER-USER — leverage Track A identity)
Let the composer **import** an existing Google Doc's text into a draft, and **export** a draft to a Google
Doc. **PER-USER**: each logged-in user connects their OWN Google account once; the app stores that user's
token (keyed by their user id from Track A) and uses **that user's** token for their import/export — so each
person operates on their own Drive/Docs. (This is barely more than a single connection because the current
user is already available via `get_current_user`, and it avoids everyone's exports landing in one Drive.)

## Config (in `artemis/config.py` + documented in `.env.example`; values live in `.env`, NOT committed)
- `ARTEMIS_GOOGLE_CLIENT_ID` = `612420684593-qnuhj6iab2bu8bd9d95ff9ek3on036mq.apps.googleusercontent.com`
- `ARTEMIS_GOOGLE_CLIENT_SECRET` = (set in `.env` on the box before live connect; **secret — never commit/log**)
- `ARTEMIS_GOOGLE_REDIRECT_URI` = `https://app.artemisos.me/api/google/oauth/callback`
- Scopes: `https://www.googleapis.com/auth/drive.file` + `https://www.googleapis.com/auth/documents`
  (request offline access so a refresh token is issued).

## 1. OAuth connect flow + token storage (migration off current head)
- `GET /api/google/oauth/start` → builds the Google consent URL (client id, redirect, scopes, `access_type=
  offline`, `prompt=consent`) and redirects.
- `GET /api/google/oauth/start` associates the flow with the current user (state param). `GET
  /api/google/oauth/callback?code=…` → exchange the code for access+refresh tokens; store them **for the
  current user**.
- **Token store:** table `google_credentials` keyed by `user_id` FK→users (ONE row per user): access_token,
  refresh_token, expiry, scope, connected_email, created/updated. Unique on user_id. Migration off head.
  Treat tokens as CREDENTIALS — server-side only, never returned to the FE, never logged. Auto-refresh the
  access token when expired (per-user refresh token).
- `GET /api/google/status` → `{connected: bool, email?}` for the **current user**. `POST
  /api/google/disconnect` → clear/revoke the **current user's** token.
- Import/export below use **`get_current_user`'s** stored token; if the user hasn't connected → 409/"connect
  Google first" (the FE prompts them to connect).

## 2. Import + export
- **Import:** `POST /api/writing-studio/drafts/{id}/google-doc/import` body `{docUrl}` (accept a Google Doc
  URL or id) → use the Docs API to read the doc, convert to the composer's **plain-text + light-markdown**
  format (headings → `#`, lists → `-`/`1.`, paragraphs) → store as the draft's content (via the existing
  draft update path / live_content) and link the doc id in metadata. Returns the imported content + linked
  doc id.
- **Export:** `POST /api/writing-studio/drafts/{id}/google-doc/export` → create a NEW Google Doc (or update
  the linked one if present) from the draft's current text → return the Doc URL. Store the linked doc id in
  the draft metadata.
- Use a mature Google client lib (`google-auth` + `google-api-python-client`, or `httpx` direct to the REST
  APIs) — pin versions, commit `uv.lock`, org dep rule (nothing <7 days old).

## Acceptance (verify the EFFECT — MOCK Google in tests; no live calls in CI)
- Migration up/down round-trips. `GET /api/google/status` → `{connected:false}` initially.
- OAuth: with a MOCKED token endpoint, `/oauth/callback` stores a credential; `status` → `{connected:true,
  email}`; refresh path swaps an expired token (mocked).
- Import: with a MOCKED Docs API returning a sample doc, `…/google-doc/import` converts it to text and sets
  the draft content (fresh GET of the draft shows it). Export: with mocked Docs API, `…/export` returns a Doc
  URL and links it. Paste both.
- Tokens never appear in responses or logs. `./scripts/check.sh` clean (note PRE-EXISTING failures
  separately).
- A LIVE smoke (real Google) is optional + only once the real secret is in `.env` — note it, don't gate CI on
  it.

## OUT OF SCOPE
The composer FE for Google Doc (terminal does the small header
affordance after — link/import/export buttons); the Drive picker UI. Don't touch composer-v5.js or comments
files.

## Constraints
Secrets server-side only (config/.env, never committed/logged/returned). Lossless for DRAFT content (import
goes through the normal content path; never destroys versions). Additive migration off current head. New
router + main.py registration. Pin Google deps + commit uv.lock. Isolated worktree + own test DB. **Do NOT
merge** — report branch + SHA + worktree + the mocked OAuth/import/export proofs + confirmation tokens aren't
exposed. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews (security) +
verifies + merges.
