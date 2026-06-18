# Enablement Indexing — Handoff (Terminal/Opus to build WITH Jon)

**Context:** Kai is LIVE in #enablement-library but has no data. Sara + Missy are loading assets into Google
Sheets/Docs (+ some Google Slides). This builds the indexing pipeline that feeds Kai. Source of truth for the
data rules = the 2026-06-18 "Building Enablement Agents" meeting transcript (Jon has it). **Jon will chat
alongside the build to confirm exact column mappings — the sheets are open in front of him.**

## The decision — Apps Script → webhook → DB (NOT live tool calls)
- **Why:** live Google Sheets/Slides/Docs API calls per Kai query = slow, token-heavy, broad scopes. Instead,
  **pre-index** into the DB; Kai searches the DB (semantic + keyword) = snappy + cheap. Refresh on a cron.
- **Architecture:**
  1. **Apps Script** hosted on **`amiracentral@amiralearning.com`** reads each sheet/doc/slide NATIVELY
     (`SpreadsheetApp` / `DocumentApp` / `SlidesApp`), applies the per-sheet column config, normalizes one
     record per asset, and **POSTs a batch to an Artemis webhook**.
  2. **Apps Script time-driven trigger** = the cron (e.g., hourly or daily; tighter for the slide decks Missy
     edits). Re-runs + re-posts; server upsert is idempotent.
  3. **Server webhook receiver** validates + upserts into `enablement_assets` (with embeddings).
  4. **Kai** searches `enablement_assets` (existing `search_enablement_assets` / `get_enablement_asset` tools).
- This SUPERSEDES the earlier server-side Drive-CSV sync (`artemis/enablement/sync.py`) for the multi-sheet
  reality — Apps Script handles per-sheet logic + native Slides text, and the SERVER needs **zero Drive scopes**.

## Reuse (already built — don't rebuild)
- `enablement_assets` table + embed-on-write + Kai's read-only tools (`search_enablement_assets`,
  `get_enablement_asset`), registered for `agent_id="kai"`. Per-person memory is built. Kai is live.
- **Likely schema work:** the store was shaped for a simple ENABLEMENT_DB (title/summary/tags/link/transcript).
  The real data is richer — needs fields (or a flexible `metadata` JSON) for: `asset_type`, `audience` columns,
  `customer_link`, `editable_link`, `internal_link`, `speaker_notes_link`, `tags`, `searchable_text` (incl.
  slide text), `source_sheet`/`row` (the idempotency key), `surfacing` flags. Extend the store or add a JSON blob.

## The data — per-asset rules (CONFIRM exact columns with Jon; from the transcript)
1. **Amira Teacher Resources (Internal) sheet** — search on cols **B,C,D** (audience). Link to surface = col
   **G** (customer-facing). Col **H** (editable) = surface ONLY if explicitly asked for the editable version.
2. **Training Decks sheet** — search on cols **A,B,C,D** (product / teacher-admin / new-returning dropdowns).
   Customer-facing handout = col **H/I**; col **G** = INTERNAL link (CSM-only → flag as internal). Col **F** =
   speaker-notes script (surface on request only). Decks are VIEW-ONLY → Kai reminds "make a copy" (ideally the
   link auto-forces a copy via an app-script-rewritten URL). These decks are NOT slide-text-indexed (just A-D
   search) EXCEPT the differentiation deck (below).
3. **AIT Student Experience Video Library** — use ONLY the **"consolidated library" tab**. Search/index cols
   **C, F, H, J** (J = micro-intervention tags, e.g. "El Konin sound box / graphemes"). Col **H** = video link;
   use **H** as the name (it's richest). Do NOT transcribe/index the student videos themselves.
4. **Differentiation deck (Google Slides, rows ~22+ of the Training Decks sheet)** — the ONE deck whose SLIDE
   TEXT gets indexed (Missy updates it → needs a tighter cron). `SlidesApp` → each slide's text → markdown into
   `searchable_text`. Text only (no image OCR — decided). Trigger via a "ready for indexing" column (Jon
   proposed a col `M`=true + a `processed_at` writeback).
5. **Google Docs (various)** — easy: index the text.

## Surfacing rules (encode in data + Kai's prompt — Kai already exists, just extend his prompt)
- **Default to the CUSTOMER-FACING link.**
- **Editable version only on explicit request** ("can I have the editable version?").
- **Internal-only links** (e.g., Training Decks col G) → clearly flag as INTERNAL-ONLY to the CSM.
- **View-only decks** → remind to "make a copy" (or hand a force-copy link).
- **Kai never sends to customers** — he hands links to the CSM, who sends. (Per-person memory already built.)

## Build steps
1. **Webhook receiver** (server): authenticated endpoint (shared secret / Cloudflare Access service token — the
   app is behind CF Access, so the Apps Script POST must carry a bypass token). Accepts a batch of normalized
   asset records → validates → upserts `enablement_assets` (+ embed). Idempotent on the stable id (sheet+row).
2. **Apps Script** (on `amiracentral@`): per-sheet readers (column config above) + a `SlidesApp` text extractor
   for flagged decks + the normalize→POST. Add a **time-driven trigger** (cron).
3. **Schema:** extend `enablement_assets` (fields/JSON above) so the surfacing rules + slide text fit.
4. **Kai prompt/persona:** encode the surfacing rules (customer-facing default, editable-on-request,
   internal-only flag, make-a-copy reminder).
5. **Permissions (Jon):** `amiracentral@amiralearning.com` editor on all (shortcut) resources.

## Gotchas
- **Cloudflare Access:** the webhook must bypass CF Access (service token / a public `/webhook` path + shared
  secret) — the Apps Script can't do the CF Access browser login.
- **Idempotency:** key upserts on `source_sheet + row` (or a stable asset id) so cron re-runs update, not dup.
- **Slides = text only** (no OCR). If a deck is ever image-heavy + needs visual content, that's a separate
  PDF→vision path (NOT now).
- Don't surface internal/editable links unless the rule is met.
- The drive.readonly scope issue from the old plan is MOOT here (Apps Script runs as amiracentral@ with native
  access; server needs no Drive scope).
