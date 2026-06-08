# Codex brief — Identity Track A: Cloudflare Access (Google SSO) login → verified current user

**Agent:** Codex. **Branch:** `worker/identity-cf-access` off `main`. **Own git worktree, cwd inside. Own
test DB** (`artemis_test_identity`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md` +
`briefs/identity-gdoc-setup-runbook.md` (Track A). **Security-sensitive (auth) — verify signatures
properly; this gates who the app trusts.**

## The point
Artemis sits behind Cloudflare Access (Google SSO now configured). On every request Cloudflare adds a signed
JWT header `Cf-Access-Jwt-Assertion` identifying the logged-in user. This slice **verifies that JWT** and
turns it into a trusted current user + a users directory. Unlocks Stage 6 (comments/@mentions/attribution).
**No passwords touch our app — we only verify Cloudflare's token.**

## Config (non-secret; in settings so swapping to the org's Cloudflare later = a config change, no rebuild)
Add to `artemis/config.py` (pydantic-settings) + document in `.env.example`:
- `CF_ACCESS_TEAM_DOMAIN` = `jfila.cloudflareaccess.com`  (JWKS at `https://<domain>/cdn-cgi/access/certs`,
  issuer = `https://<domain>`)
- `CF_ACCESS_AUD` = `196c5861dc5fbe509186be11c6006510050ae562f93a52556d7ef9136042b7d6`
- `CF_ACCESS_ENABLED` (bool, default false) — when false (local dev), skip JWT + use the dev shim user.

## 1. JWT verification (`artemis/identity/cf_access.py` — new module)
- Fetch + **cache** the JWKS from `https://{team_domain}/cdn-cgi/access/certs` (refresh on cache miss / key
  rotation; don't fetch per request).
- Verify the `Cf-Access-Jwt-Assertion` header: RS256 signature against the JWKS, `aud` contains
  `CF_ACCESS_AUD`, `iss` == `https://{team_domain}`, `exp`/`nbf` valid. On any failure → reject (401).
- Extract `email` (+ `name`/identity if present) from the verified claims.
- Use a mature JWT lib (PyJWT[crypto] or python-jose — both years-old, fine under the org dep rule; commit
  `uv.lock`). Do NOT hand-roll crypto.

## 2. Users directory (migration off current head + model/repo)
- `users` table: `id`, `email` (unique, NOT NULL), `name` (nullable), `created_at`, `last_seen_at`. Lossless
  (no deletes). Migration chains off the current head (check `uv run alembic heads`).
- Repo: `get_or_create_user(session, email, name)` (upsert; bump `last_seen_at`).

## 3. FastAPI dependency + endpoint
- `get_current_user` dependency: if `CF_ACCESS_ENABLED`, verify the header (→ 401 if missing/invalid) →
  `get_or_create_user` → return the user. If disabled (local dev), return/create a fixed dev user
  (e.g. `dev@local`) so the app runs without Cloudflare in front. (Mirror the existing dev-friendly patterns;
  do not break local `uvicorn`.)
- `GET /api/me` → the current user (id, email, name) for the front-end to show who's logged in.
- Small front-end touch: surface the logged-in user (e.g. in the app header/footer where the user already
  shows) via `/api/me`. Keep it minimal — full per-feature wiring (comments authorship) is Stage 6.

## Acceptance (verify the EFFECT — mock the JWT, do NOT call real Cloudflare in tests)
- Generate a test RSA keypair; serve a fake JWKS; mint a token with the right `aud`/`iss` → `get_current_user`
  accepts + creates the user; `GET /api/me` returns it. Paste it.
- Wrong `aud` → 401. Expired → 401. Bad signature → 401. Missing header (enabled) → 401. Missing header
  (disabled/dev) → dev user. Cover each.
- `get_or_create_user` upserts (same email twice → one row, `last_seen_at` bumped; lossless).
- Migration up/down round-trips. `./scripts/check.sh` (note PRE-EXISTING failures separately).

## OUT OF SCOPE
Comments (Stage 6); the Cloudflare/Google console config (Jon's done it); Track B Drive/Docs OAuth; widening
the access policy (Jon owns who's allowed). Just: verify login → trusted current user + directory + `/api/me`.

## Constraints
Security-correct JWT verification (signature + aud + iss + exp — all of them). Lossless users. Config-driven
domain/AUD (so org migration is a config swap). Org dep rule (mature JWT lib; commit `uv.lock`). Don't break
local dev (dev shim). Isolated worktree + own test DB. **Do NOT merge** — report branch + SHA + worktree +
the accept/reject test matrix + `/api/me` output. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
Opus Lead reviews (security especially) + verifies + merges.
