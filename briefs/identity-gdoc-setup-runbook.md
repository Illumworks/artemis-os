# Setup runbook — Google login (identity) + Google Docs access (unlocks Stages 6 & 7)

**Audience:** Jon (the console steps are YOURS — they're account/security settings I'm not allowed to touch;
I build everything on the app side + guide you click-by-click). Companion to the design in
`briefs/writing-studio-identity-and-gdoc.md`. Two tracks; do Track A first (it's the bigger unlock + simpler).

## The division of labor (important)
- **You do (console):** the Cloudflare + Google Cloud configuration. These are access-control / OAuth /
  account settings — I can't (and shouldn't) make them for you. I give exact steps; you click.
- **I/the build team do (code):** verify the login token, build the user directory, thread identity through
  the app, then build the Docs import/export. None of that touches your credentials.

---

## TRACK A — Google login via Cloudflare Access  → unlocks Stage 6 (comments, @mentions, multi-user)

Your app is already behind Cloudflare Access (locked to your email). We turn on Google as the login method
and widen who's allowed. Cloudflare handles the actual login; our app just *reads + trusts* the verified
identity Cloudflare passes it. **No passwords ever touch our app.**

### Your steps (Cloudflare Zero Trust + Google Cloud)
1. **Create a Google OAuth client** (Google Cloud Console → *APIs & Services → Credentials → Create
   credentials → OAuth client ID → Web application*). For "Authorized redirect URI," paste the callback URL
   Cloudflare gives you in step 2 (looks like `https://<your-team>.cloudflareaccess.com/cdn-cgi/access/
   callback`). Save the **Client ID + Client Secret**.
   - First time only: configure the OAuth consent screen as **Internal** (your Google Workspace org), so it's
     limited to amiralearning.com accounts.
2. **Add Google as a login method** in Cloudflare (Zero Trust dashboard → *Settings → Authentication → Login
   methods → Add new → Google*) and paste the Client ID + Secret from step 1.
3. **Widen the access policy** for the app (Zero Trust → *Access → Applications → [your app] → Policies*):
   change the rule from "only jon@..." to your team — e.g. **emails ending in `@amiralearning.com`**, or an
   explicit allow-list (you, Angela, Julie). This is the "change the Cloudflare credential" you mentioned.
4. **Send me two values** (from the Access app's settings): your **team domain** (e.g.
   `amiralearning.cloudflareaccess.com`) and the application's **AUD tag** (the Application Audience ID). I
   need these to verify logins on our side. (Neither is a secret.)

### What I build (after you send the two values)
- Verify the `Cf-Access-Jwt-Assertion` header Cloudflare adds to every request, against your team's public
  keys (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`), checking the AUD tag → extract the
  logged-in user's email/name. (Reject anything without a valid token — defense in depth behind Cloudflare.)
- A **users directory** (a row per person seen, keyed by email) + thread "current user" through the app.
- Local-dev shim so we can still run without Cloudflare in front (a dev header / fake user).
- **This is the identity foundation.** Once it lands, Stage 6 (comments) can attribute authors, do
  @mentions, and show who's editing.

---

## TRACK B — Google Docs / Drive access  → unlocks Stage 7 (import & export Google Docs)

This is a *separate* Google permission: logging in with Google (Track A) does NOT grant the app permission to
read/write your Google Docs. For that, the app needs its own Google authorization with Drive/Docs scopes.

### Your steps (Google Cloud Console — can reuse the same project as Track A)
1. **Enable APIs:** *APIs & Services → Library →* enable **Google Drive API** and **Google Docs API**.
2. **OAuth client for the app's Docs access:** create an OAuth client (Web) with the app's redirect URI
   (I'll give you the exact URL when we build it), scopes for Drive/Docs (start with **read-only** for import;
   add write for export). Keep it **Internal**.
3. **Send me** that Client ID + Secret (these ARE secret — share them the secure way, not in plain chat; I'll
   tell you where they go in the app config / `.env`).

### What I build
- A "Connect Google" flow (the user authorizes once; we store + refresh their token securely).
- **Import:** paste/pick a Google Doc → pull its text into the composer (Stage 7).
- **Export:** push a composer draft to a new or linked Google Doc.
- (Note: there's a Drive connector I already use to *read* your files as the assistant — that's for my use,
  not the same as the app letting any logged-in user import their own Docs. Track B is the app-level version.)

## Sequencing + scope
- **Track A first** — it's the foundation comments (Stage 6) AND the team-collaboration features all depend
  on, and it's mostly your config + a small verify build on our side.
- **Track B second** — a contained integration for Stage 7, once Track A's identity exists.
- Build the 3 standalone composer stages (3, 5, 8) in parallel / first; they don't wait on any of this.

## LIVE STATUS (2026-06-08) — Track A is ON for the demo
- The tunnel (`app.artemisos.me`) routes to **localhost:8000**, which is the demo instance.
- CF Access verification is enabled by running that instance with **process-env** vars (NOT `.env`, so the
  preview/dev instances stay open): `ARTEMIS_CF_ACCESS_ENABLED=true`,
  `ARTEMIS_CF_ACCESS_TEAM_DOMAIN=jfila.cloudflareaccess.com`, `ARTEMIS_CF_ACCESS_AUD=196c…b7d6`.
- **FOOTGUN:** if :8000 is restarted WITHOUT those env vars, app-level verification silently falls back to
  the dev shim — user **attribution** is lost (comments would say "dev"), though the Cloudflare gate (6-email
  policy) still blocks the public. So: always relaunch :8000 with those vars.
- Validated 2026-06-08: Jon logged in via Google (amiralearning) → got in. Direct localhost (no token) → 401.
- **Durability:** :8000 + cloudflared run as plain background processes — a reboot kills the demo. Before
  Friday, make both **launchd services** (env baked in) so the demo survives a restart. (Offered; pending.)
- Migrate to the org's Cloudflare later = swap `TEAM_DOMAIN` + `AUD`, no code change.

## Security notes
- No passwords or financial info ever enter our app — Cloudflare + Google own auth. I verify tokens, I don't
  store credentials.
- The Client **Secret** (Track B) is sensitive — it goes in server-side config (`.env`), never the front-end,
  never committed.
- Access policy (who's allowed) stays YOUR call in Cloudflare — I never widen it.
