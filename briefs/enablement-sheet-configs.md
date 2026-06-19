# Enablement Indexing — Per-Sheet Configs (confirmed with Jon)

> **STATUS (2026-06-18): BUILT + proven end-to-end.** Server: `artemis/routes/enablement.py`
> (`POST /api/enablement/ingest`), schema widened in migration `0098`, Kai tools/persona updated.
> Apps Script: `apps-script/enablement-indexer.gs` + deploy runbook `apps-script/README-deploy.md`.
> Remaining = Jon's deploy: create the Cloudflare Access service token, paste the secret + token into
> the Apps Script CONFIG, run `installTrigger` + `runAll` on amiracentral@.


Source of truth for the Apps Script column mappings + Kai surfacing rules. Built one sheet at a
time, confirmed live with Jon against the actual downloaded files. Companion to
`briefs/enablement-indexing-handoff.md`.

Idempotency key for every record = `source_sheet + row` (hourly re-index upserts in place; mid-sheet
row inserts cause harmless re-embedding churn, never duplicates).

---

## 1. Amira Teacher Resources (INTERNAL)
- **File:** https://docs.google.com/spreadsheets/d/1iFS-jKJyjRX1xRQeg9Tzr3yFdXaOxjdo7_97w9wlxfU/edit
- **Tabs:** 20 total — **only `25-26 AIT` is in scope.** Ignore all other tabs.
- **`25-26 AIT` layout:** header = row 3, data starts row 4 (~173 rows).

| Use | Column |
|---|---|
| Search / index | B = Audience (`Students / Teachers / Admin / Families / All (General) / HMH`), C = Product (`AIT / Tutor / Assess / Instruct / Lectura / A+T / I+T`), D = Title |
| Display name | D = Title |
| Freshness | A = Date Added or Updated |
| Customer link #1 | **F = TRH Direct Link (EXTERNAL)** → label **"web link (share this)"** |
| Customer link #2 | **G = PDF Link (EXTERNAL)** → label **"PDF"** |
| Editable | H = Editable File (INTERNAL) — surface **only on explicit ask**, with a **"make a copy"** reminder |
| Ignored | E = TRH Page, I = Post to TRH? (internal workflow) |

**Surfacing:** Provide BOTH F and G, each clearly labeled. Editable (H) only when explicitly
requested. F/G are both external/customer-safe.

---

## 2. Training Decks SY26-27
- **File:** `1178t_lk8mCBZ6S-DQDSICwzIbeHbmVm-rVeixAsjcjo` — single tab `Sheet1` (gid 0), header = row 2, data rows 3–24.
- **Model:** one row = a training "package" (a scenario) carrying several labeled links. The sheet
  documents its own surfacing rules in the header text.

| Use | Column |
|---|---|
| Search facets | A = Training Type (Getting Started / Data Dive / Other), B = Products (Assess/Tutor/Instruct/combos + Spanish), C = Persona (Teacher/Admin), D = Customer (New/Returning) |
| Deck (primary asset) | E = Training Deck — **DEFAULT deliverable** for deck asks; view-only → hand a **force-copy** link (`/copy`) |
| Script (speaker notes) | F — **on request only**; never customer-facing. **BUT its text is indexed** for content search (Apps Script follows the F link → Google Doc text → searchable_text) |
| Handout – default | H = Customer Link — **DEFAULT** for handout asks |
| Handout – short | I = Tinyurl Customer Link — on explicit request only |
| Handout – editable | G = Internal Only, editable — **on request only**, flagged INTERNAL, force-copy |
| Customer webinar | J — customer-facing video |
| Freshness | K = Last Updated |
| Ignore | L = Edit Notes, row-1 🔴/🟢 legend |

**Default vs on-request:** Default = E (deck, make-a-copy) and H (customer handout). On request only =
F (script), G (editable/internal), I (tinyurl).

**Two content-indexing logics:**
1. **Script-text (all decks with a script):** index column F's linked Doc text → content-searchable
   (e.g. match "El Konin" to the deck that covers it). Cheap, one-time, cron-refreshed.
2. **Slide-text (decks with NO script, flagged via M):** column **M = "Ready for Indexing"**. When a
   deck row = `True`, Apps Script opens that row's deck (E), extracts **slide text** → searchable_text,
   and writes a `processed_at` timestamp back. Ignores stray `True` on blank rows (flag must sit on the
   deck's own row). Tighter cron (Missy edits these). Text only, no image OCR. Today: the
   **Differentiation Slides** deck (row 22, has no script) is the one qualifying.

**Demo accounts:** row 23 ("Demo Account for Training", a `secure.app.amiralearning.com` login) is NOT
skipped — store with `asset_type = demo_account` and build the piping. Kai *surfacing* demo logins
(profile-matched) is deferred to iteration 2; data flows now.

**Idempotency:** key on sheet + row (small sheet; row inserts cause harmless re-embed churn).

---

## 3. AIT Student Experience Video Library ("00 - New AIT Student Experience Video Library")
- **File:** `1o5pmUfLn0uAtXr5l1opYY7elfQWZCygRR2qgIlRslPM`
- **Tabs:** 25 total — **only `Consolidated Library` is in scope.** Ignore all others (incl. `Retire`).
- **Layout:** header = row 1, ~124 video rows. (Columns were edited after the 2026-06-18 meeting;
  this reflects the live sheet.)

| Use | Column |
|---|---|
| Idempotency key | **A = Number** (`STU-K-ISIP-0019`) — stable ID, key on this (not row number) |
| Search facets | C = Grade Level, E = Product (ISIP Assess / Instruct / Tutor), F = Language (English/Spanish), J = Micro-Interventions (the "El Konin sound box / graphemes" tags) |
| Display name + link | H = Video Name and Link (display text = name, hyperlink = video URL) |
| Defensive filter | D = Action — index only `Keep` (retired rows live in the `Retire` tab; today all 124 are Keep) |
| Ignored | G = SoR Okay? (**hidden on the sheet — ignore**), K = Subtest, L = Domain (redundant w/ name), I/M–S (dates, voice, notes, creator) |

**No transcription** — index metadata only; never crack the student videos.
**Idempotency:** key on column A.

---

## 4. Customer Video Walkthroughs ("Customer Video Walkthroughs - SY25-26")
- **File:** `12f-b1f3JNFWn5NG2ew0dDRxIzkgys_IP288yYq3udTI` (tab gid 402841425)
- **Tabs:** 4 — **only `Post-Sale Product Tour Video Scope` is in scope.** Ignore `OLD - Video
  Inventory` (old tracker), empty `Sheet9`, and `Product Tour Acceptance Criteri[a]`.
- **Layout:** header = row 1, ~106 walkthroughs (all have a customer link; 105 Done + 1 Sent for Feedback).

| Use | Column |
|---|---|
| Search facets | A = Category (How-To / Teacher Exp / Admin Exp / Student Exp / Product Overview), B = Audience, C = App (Assess/Instruct/Tutor/N-A), D = Walkthrough Title, E = "Needs to Include" (sparse, fold in where present) |
| Display name | D = Walkthrough Title |
| Customer link | **G = CUSTOMER LINK** (storylane.io share) — customer-facing, surface it |
| Freshness | I = Last Updated |
| Ignored | F Owner, H Due Date, J Status, K/L (empty), M On TRH?, N Sub Category |

**No transcription** — interactive tours; title + "Needs to Include" is enough to match.
**Idempotency:** no stable ID column → key on sheet + row.

---

## 5. Indexed Docs (Drive folder scan — evergreen)
- **Folder:** https://drive.google.com/drive/folders/1cBzZpBT1dsZFuCbNmh4oyOlYJK6EZGuT (`Indexed Docs`)
- **Rule:** anything in this folder is fair game for Kai and is **CSM-shareable**. Apps Script scans the
  folder on cron, indexes each Google Doc's **full text**, keyed by **Drive file ID** (rename/edit
  updates in place). Currently 4 docs. Drop a new doc in → searchable next run.
- **Surfacing:** single asset per doc (title + text + link); surface the doc link. No internal/editable
  split (the folder boundary is the safety mechanism — don't put internal/draft docs here).
- **TODO:** decide recursion (scan subfolders?) and whether to follow Drive shortcuts. Default: flat
  scan, Google Docs only.

---

## File IDs (for the Apps Script)
- [x] Sheet 1 — Amira Teacher Resources (INTERNAL): `1iFS-jKJyjRX1xRQeg9Tzr3yFdXaOxjdo7_97w9wlxfU` — tab `25-26 AIT`
- [x] Sheet 2 — Training Decks SY26-27: `1178t_lk8mCBZ6S-DQDSICwzIbeHbmVm-rVeixAsjcjo` — tab `Sheet1` (gid 0)
- [x] Sheet 3 — AIT Student Experience Video Library: `1o5pmUfLn0uAtXr5l1opYY7elfQWZCygRR2qgIlRslPM` — tab `Consolidated Library`
- [x] Sheet 4 — Customer Video Walkthroughs: `12f-b1f3JNFWn5NG2ew0dDRxIzkgys_IP288yYq3udTI` — tab `Post-Sale Product Tour Video Scope` (gid 402841425)
- [x] Folder 5 — Indexed Docs: `1cBzZpBT1dsZFuCbNmh4oyOlYJK6EZGuT`
