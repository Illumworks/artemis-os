# Enablement Indexer — Deploy Runbook

What this does: an Apps Script on **amiracentral@amiralearning.com** reads the 4
enablement sheets + the `Indexed Docs` folder and POSTs them to Artemis, which
embeds + stores them for **Kai** to search. Data mapping lives in
`briefs/enablement-sheet-configs.md`. Server code: `artemis/routes/enablement.py`.

## A. Server side (Artemis) — done by Claude/Jon on the Mac mini

1. Set the shared secret in `.env` (already a strong random value if Claude set it):
   ```
   ARTEMIS_ENABLEMENT_WEBHOOK_SECRET=<long-random-string>
   ```
2. Restart the app so it picks up the secret and the new `/api/enablement/ingest` route:
   ```
   launchctl kickstart -k gui/$(id -u)/me.artemisos.app
   ```
3. Confirm it's live (run on the mini; localhost bypasses Cloudflare):
   ```
   curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/enablement/ingest \
     -H "X-Enablement-Token: <the secret>" -H "Content-Type: application/json" \
     -d '{"source_sheet":"smoke","assets":[]}'
   # expect 200
   ```

## B. Cloudflare Access — let the Apps Script through the edge (one time)

The app sits behind Cloudflare Access; a script can't do the browser login, so it
authenticates with a **service token**.

1. Cloudflare **Zero Trust → Access → Service Auth → Service Tokens → Create**.
   Name it e.g. `enablement-appscript`. Copy the **Client ID** and **Client Secret**
   (the secret is shown once).
2. Open the Access **application** that protects `app.artemisos.me`
   (**Access → Applications**). Add a policy: **Action = Service Auth**, include
   **Service Token = enablement-appscript**. Save.
   (Tighter option: scope a separate Access app to the path `/api/enablement/ingest`
   and attach the service-token policy only there.)

## C. Apps Script — on amiracentral@ (one time)

1. amiracentral@ must be **editor** on all 4 sheets + the Indexed Docs folder. (Done.)
2. https://script.google.com → **New project**, signed in as **amiracentral@**.
3. Paste the entire contents of `enablement-indexer.gs`.
4. Fill in `CONFIG` at the top:
   - `WEBHOOK_URL` = `https://app.artemisos.me/api/enablement/ingest` (already set — don't overwrite it).
   - `ENABLEMENT_TOKEN` = the same secret as the server `.env`.
   - `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` = from step B1.
   - The sheet IDs + folder ID are already filled in.
4a. **Enable advanced services** (left sidebar → **Services (+)**):
   - **Sheets API** — REQUIRED. The AIT video library's links are Google smart chips, read via
     the Sheets API (`chipRuns`). Without it, AIT ingests 0.
   - **Drive API** — optional. Lets shortcut'd docs in the Indexed Docs folder contribute full
     text. Without it, shortcuts still index by name + link.
5. Run **`installTrigger`** once → authorize the scopes when prompted (Sheets, Docs,
   Slides, Drive, external requests). This also auto-runs **`setupTrainingDecksSheet`**,
   which turns the Training Decks "Ready for Indexing" column (M) into checkboxes, adds
   an "Indexed At" confirmation column (N), and auto-checks the deck rows that need
   slide-indexing (a deck with no script — i.e. the Differentiation deck), clearing any
   stray flag. No hand-editing of cells needed; Missy can check/uncheck rows afterward.
6. Run **`runAll`** once. Check **Executions** / **View → Logs** — each source logs
   `{upserted, archived, embedded}`. Slide-indexed decks get a timestamp in "Indexed At".

## D. Prove it

- In Kai's channel (#enablement-library), ask for something real, e.g.
  *"training deck for a returning Assess teacher"* or *"video showing blending"*.
  Kai should return the right asset with the **customer link(s) labeled**, hold the
  editable/internal links unless asked, and say "make a copy" for the decks.

## E. Notes

- **Re-runs are safe.** Each asset has a stable key; re-running upserts in place.
  Removing a row from a sheet soft-archives it (Kai stops surfacing it; nothing is deleted).
- **Cadence:** hourly. To change, edit `installTrigger()` and re-run it.
- **To pause/disconnect** (e.g. after the proof, to hand to Sara): Apps Script →
  **Triggers** (clock icon) → delete the `runAll` trigger. The data already ingested
  stays in Artemis. Re-run `installTrigger()` to resume.
- **Migrating off Jon's server later:** change `CONFIG.WEBHOOK_URL` to the new host and
  re-point the CF Access service token; nothing else changes.
