# Enablement + Kai (Chiron) — Build Plan

**Context:** First team onboarded onto Artemis OS beyond marketing (per Mark's directive to open the tool to
other teams). Enablement has many docs/videos/images being consolidated into one Drive folder. We build
**Kai (Chiron)** — a read-only knowledge concierge that helps the Enablement team find assets and answers in
Slack. Persona: `kai-personality-profile.md`. Sara's initial-index runbook: `docs/enablement-seed-runbook-for-sara.md`.

## Principle
**Knowledge bridges teams; creation stays gated.** Kai is a **read-only information router** with NO creation
tools — he cannot make campaigns or content, only find/link/answer. That structurally lets other teams use the
platform without anyone creating work in the wrong lane. (Mirrors the marketing RBAC + the surface lockdown.)

## Architecture (data flow)
```
Drive folder (assets) + /Transcripts (video transcripts, via Descript)
        │  (AI indexing: doc summary / image caption / transcript summary)
        ▼
ENABLEMENT_DB Google Sheet   ← human-readable index + source of truth (one row per asset)
        │  (cron sync: rows + full transcript text)
        ▼
Artemis DB (Kai's search brain)   ← fast + semantic search over summaries AND full transcripts
        │
        ▼
Kai (named agent)  ←→  "enablement library" Slack channel (C0BB17EJLKC) + DMs
```
- **Sheet = source of truth** (humans can read/edit; the new content writer + Sara live here).
- **DB = Kai's search copy** (so retrieval is fast/semantic and doesn't burn tokens hitting the Sheet API).
- **Full transcripts** live as files (linked in the Sheet's `Transcript Link`) and their text is ingested into
  the DB for deep search; the Sheet holds only the summary.

## Phases
**Phase 0 — Seed (Sara, runbook above).** Docs + images + video-transcripts → `ENABLEMENT_DB`. Runs on her
Claude subscription (no metered API). Videos: Descript transcript → Claude summary → `/Transcripts` + linked.
DONE-signal: ENABLEMENT_DB populated.

**Phase 1 — Sheet → DB sync (us).** A cron job reads `ENABLEMENT_DB` (Sheets API — we have Google creds) +
pulls each `Transcript Link` file's text, and upserts into a new enablement-assets store in the Artemis DB
(asset row + summary + tags + link + transcript text), with embeddings for semantic search. Idempotent
(re-run safe; keyed by Drive file id / row). Frequency: e.g. every 15–30 min, or triggered after indexing.

**Phase 2 — Kai agent (us, mostly config + retrieval tools).**
- **Agent shell = config:** persona file (`kai-personality-profile.md`), a Slack app + `integrations` row
  (`agent_id="kai"`, channel `C0BB17EJLKC`) — the named-agent loop is already parameterized (Callie was added
  this way, "no new code"). Add a `scope_policy` entry: Kai = enablement-scoped (read), reports to Artemis,
  NO personal/agent:artemis, NO creation tools.
- **The real build = Kai's retrieval tools** (analogous to Callie's analyst toolset): `search_enablement_assets`
  (semantic + keyword over the DB store, returns top matches with title/summary/link), `get_asset` (details +
  link), and a "what's in the library on <topic>" overview. All read-only. Output lint-clean (no em-dash/emoji).
- Kai answers grounded ONLY in the library; says honestly when something isn't there + offers to flag a gap.
- Per-person memory comes free with the named-agent loop (speaker attribution).

**Phase 3 — Ongoing auto-indexing (us).** Replaces Sara's manual pass for NEW assets.
- **Trigger:** Google Drive `changes`/push watch on the folder (near-real-time) → indexer; plus a **nightly
  reconcile cron** that scans for anything the webhook missed (belt-and-suspenders).
- **Indexer:** per new file → detect type → summarize: **docs** = Claude/Gemini long-context; **images** =
  vision; **videos** = **Descript API** (DECIDED). New video → download from Drive → `POST /jobs/import/
  project_media` (direct upload: `content_type`+`file_size` → signed URL → `PUT` bytes; **import into the ONE
  shared project_id** "Enablement Library Transcripts" so Descript stays tidy — filenames unique per project)
  → poll `GET /jobs/{job_id}` or `callback_url` → `POST /export/transcript` (txt) → summarize the transcript.
  Auth `Authorization: Bearer <DESCRIPT_TOKEN>` (token scoped to the Drive; reuse the same token Sara used for
  the seed). Cost draws from the Descript plan's media-minutes (no per-call API fee). Writes the
  `ENABLEMENT_DB` row + transcript file, then Phase-1 sync picks it up.
- **Human-in-loop:** full-auto, but stamp `needs_review` on low-confidence/research items for a periodic
  human spot-check (no friction for routine adds). Also backfills any `pending_video` rows from the seed.

**Phase 4 — Cross-team sharing (fast-follow, after Kai is proven).** Assets carry a scope tag:
`enablement-only` (default) or `shared`. Kai answers based on asker's team + asset scope; `shared` assets are
surfacable cross-team. Agent-to-agent: Artemis can route a cross-team question to Kai; Callie could pull a
`shared` enablement asset. Knowledge flows between agents; humans still create only in their own lane.

## Key technical notes / decisions
- **New DB store** for enablement assets (table + embeddings) — separate from marketing memory; enablement
  scope. Reuse the existing memory/embedding + retrieval infra where possible.
- **Sheets read:** use the Google creds already configured (the `612420684593` "Artemis Google Docs Access"
  client; Sheets API scope may need adding to consent — check). Transcript files read via the Docs/Drive API
  the app already uses.
- **Named-agent add:** persona + Slack app + `integrations` row + `scope_policy` Kai entry; verify Kai's tool
  registry contains ONLY read/retrieval tools (no create/agency tools) — this is the safety guarantee.
- **RBAC:** enablement team → Kai + enablement library only; ties into the multi-team access model (and the
  open "hide connectors / role-aware surfaces" pins in `writing-studio-team-feedback-2026-06-16.md`).

## Open items / dependencies
- Confirm Sheets API scope is granted to the Google client (for Phase 1 sync).
- Confirm Kai's Slack app token/registration (Jon duplicated Callie's manifest → install + `integrations` row).
- Invite Kai's bot to `C0BB17EJLKC`.
- Cross-team sharing direction (Phase 4) — siloed first per Jon.
- Ongoing-video path: DECIDED = **Descript API** (new connector: Bearer token scoped to the Drive, all
  imports into one shared project_id, direct-upload from Drive, export transcript txt). Add `descript`
  provider config (token) like the other connectors. Same token serves Sara's seed (Option B).
