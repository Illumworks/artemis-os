# Screen-Time Watch — Build Status (2026-06-19/20)

Shipped a national screen-time legislation/policy intelligence pipeline + dashboard,
separate from the marketing campaign pipeline. See `docs/screentime-watch-plan.md`
for design and `docs/screentime-watch-*.md` briefs for scope.

## What shipped (all merged to main, LIVE)
- **Isolated data** — `screentime_*` tables (migration `0102`), scrubbable
  (`purge_screentime_data`, retention window). NOT the marketing SignalQueue.
- **Pipeline** (`artemis/screentime/`) — national fan-out reusing existing scouts
  read-only (legislative, state_doe, board_minutes, regional_news) → topic-relevance
  gate (config-driven) → stance classify (config-driven 🟢/🔴/⚪) → store → per-state
  rollup. Tool-less classification on Codex (cheap). Failure-safe.
- **Page** — "Screen-Time Watch" (Marketing nav): inline-SVG 50-state heat map +
  searchable signal repository + owner-only scrub. Route `artemis/routes/screentime.py`.
- **Callie reporting** — posts a sourced, stance-grouped digest to **#screen-time-signals
  (C0BBYM8N26M)**; reuses Callie's voice/Slack path; dedup via memory marker. Big-move
  alert hook in the runner. LIVE (first digest posted 2026-06-19).
- **Pipelines page** — display-only `screentime.watch` row seeded for visibility (the
  real work runs in the isolated runner, not the shared executor).

## Bugs found + fixed along the way (test→fix→retest)
- **LegiScan client returned 0 for EVERY query** (`6df0dcb`) — parsed `searchresult.results`
  but LegiScan uses numbered keys + a summary; `BillSummary` model also mismatched
  (`bill_number`/no status). Was silently zeroing out the **campaign's** legislative
  source too. Fixed both.
- **Legislative findings had no source_url** (`138717c`) — getBill returns url/state_link
  but the Bill model dropped them; Callie correctly refused to post a sourceless digest.
  Fixed (also gives campaign signals source links).
- **Topic precision** — first runs surfaced literacy/reading-retention noise; added the
  screen-time topic-relevance gate; later all-neutral stance → added restriction-action
  classification. Final live batch: 17 real bills, 6 unfavorable.
- **Nav button missing** (`9824f34`) — registering the view ≠ a clickable button; the
  rail is static HTML + `RAIL_NAV_VIEW_MAP`. See [[reference-adding-app-page-nav]].
- **Playwright chromium not installed** — fixed `state_doe` (and all Playwright scouts).

## Remaining (parked per Jon — hold until Angela reviews the first digest)
Wire into startup as ONE batch (needs `main.py`/scheduler edit + coordinated restart):
1. `register_screentime_schedule` — auto-refresh sweep.
2. `post_screentime_digest` — weekly digest cron.
3. `seed_screentime_pipeline` — idempotent on boot (so the pipelines-page row survives resets).
Plus: tune the stance definition WITH Angela (config change, no deploy).

Coordination logged in `../claudeck-artemis/COORDINATION.md` (migration 0102, the
legislative fix affecting campaign, restarts). Memory: [[project-screentime-watch]].
