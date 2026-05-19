# J6e — Meetings page: double-column Past tab + persisted transcripts

**Owner:** Worker (Sonnet)
**Scope:** ~120 LOC. Half-day or less.
**Depends on:** J6c (Meetings rebuild — DONE), J6d (calendar-driven auto-summary — DONE).
**Blocks:** Marketing slab walkthrough.

> **Important:** All file paths in this brief are relative to the repo root. The harness controls the worktree. Do NOT treat absolute paths in comments or examples as "the repo." Write to the worktree you were spawned in.

## Why

Two leftover items from Jon's walkthrough of the J6c Meetings rebuild:

1. **Past tab is still single-column** — the canvas renders meetings as a vertical list with the transcript opening *below* the selected row. J6c shipped Today as a 2-column layout (list left, post-meeting panel right). The two tabs should be symmetric — same mental model, same click behavior.

2. **Transcripts aren't persisted** — clicking a past meeting re-fetches the transcript from Granola live. This is slow, fragile (token-expiry hazard we already lived through), and prevents future memory consolidation from indexing transcripts.

J6d's `meeting_summaries` table already persists `summary` + `action_items`. Extending it to also store the full transcript is a small additive change with three downstream wins:

- Past tab loads transcripts instantly from local DB
- Survives Granola token expiry
- Future memory consolidation (M2+) has a corpus to index against

## Scope

### Backend — extend `meeting_summaries`

- [ ] Alembic migration: `ALTER TABLE meeting_summaries ADD COLUMN transcript TEXT NULL`.
  - Use the next sequential revision number. Run `ls alembic/versions/` first to confirm.
  - **Before committing:** run `git diff --staged` to verify both the new file AND any `revision`/`down_revision` edits to existing migration files are in the staged blob. (Migration chain corruption from missed-stage edits has bitten us once already.)
- [ ] Update `artemis/meetings/models.py` (or wherever `MeetingSummary` ORM model lives — find it via grep) to declare the new column.
- [ ] In the J6d auto-summarizer (`artemis/meetings/summarizer.py`), after fetching the transcript from Granola, write it to the new column along with the summary + action items. Single atomic insert.
- [ ] Backfill: for any existing `meeting_summaries` rows where `transcript IS NULL`, fetch and persist on next read (lazy backfill in `GET /api/meetings/{granola_id}/summary`).
- [ ] Update `GET /api/meetings/{granola_id}/summary` response to include `transcript` field.

### Frontend — Past tab symmetry + cached reads

- [ ] In `public/js/features/meetings.js` (or wherever J6c's `renderMeetingsGranolaTodayCanvas` lives), find the Past tab renderer (`renderMeetingsPastCanvas`). Update it to use the same 2-column grid as Today (4-col list left + 8-col detail right, mirror the `meetings-past-canvas` grid CSS).
- [ ] Move the row-click handler so both Today and Past use the same `handleMeetingsRowClick(meetingId, meetingTitle)` — should already work via event delegation on `[data-meeting-id]`; verify by clicking a Past row and confirming the right panel fills.
- [ ] In `handleMeetingsRowClick`, fetch from `/api/meetings/{id}/summary` first. If 200, render Summary / Action items / Transcript sections from the cached row (instant). If 404, fall back to live `fetchGranolaTranscriptApi(meetingId)` as today, then warn in console that the summary wasn't pre-generated.
- [ ] If `transcript` field is present on the summary response, render it; if missing but `summary`/`action_items` exist, render those and fetch transcript lazily.

### CSS

- [ ] `meetings-past-canvas` already has the 2-column grid (Today reuses it). No new CSS needed unless the Past tab had a different stylesheet override — grep for `meetings-past` in `public/css/` to confirm.

## Acceptance — what done looks like

- [ ] Past tab renders 2-column on viewport widths ≥ 900px (mirror Today)
- [ ] Clicking a Past meeting fills the right panel — Summary / Action items / Transcript sections — without re-fetching from Granola if the summary exists locally
- [ ] First load of an un-summarized Past meeting falls back to live Granola fetch and **persists the transcript on completion** so the next click is instant
- [ ] J6d auto-summarizer writes `transcript` alongside `summary` + `action_items` going forward
- [ ] DB inspection: `SELECT COUNT(*) FROM meeting_summaries WHERE transcript IS NOT NULL` is ≥ 1 after the test
- [ ] `alembic downgrade -1 && alembic upgrade head` reproduces the schema cleanly
- [ ] Screenshot of the new Past tab layout pasted in your report

## Quality acceptance gates

- [ ] Manual smoke output pasted **verbatim** in your report (curl on `/api/meetings/{id}/summary`, screenshots of both tabs)
- [ ] No regression on Today tab — open and verify both tabs still work side-by-side
- [ ] `ruff check` + `mypy` clean
- [ ] `git diff --staged` reviewed before commit — confirm migration file content matches the rename, no orphan `revision="0017"` strings
- [ ] Tests: at minimum a route-test for the `/summary` endpoint with `transcript` populated and a migration up/down round-trip test

## Out of scope (separate briefs)

- Full-text search across stored transcripts → comes with M3+ memory work
- AI-driven re-summarization when a meeting is "updated" in Granola → not a real flow yet
- Cross-meeting analysis ("what did I commit to this week?") → M2+ memory consolidation

## Where to start

1. Read this brief twice
2. `grep -n "MeetingSummary\|meeting_summaries" artemis/` to map J6d's existing code
3. `ls alembic/versions/` to confirm next revision number
4. Backend first (migration → model → summarizer write → route response), frontend second
5. Run the app locally and click both tabs end-to-end before reporting done
