# AUDIT BRIEF — Marketing section (Py vs Node gap report)

**Role:** You are Codex running an **AUDIT ONLY**. Do not modify any code. Your deliverable is one markdown report.

**Why this brief exists:** The Marketing section of Artemis (Dashboard / Writing Studio / Campaigns / Signals Inbox / Approval Queue) has had significant prior work — see `docs/HANDOFF.md` history of campaign workspace state machines, Writing Studio Slice 4 work, etc. The Python rebuild has marketing models and routes (`artemis/marketing/`), but the user is now walking the personal-workspace rail and we don't know which Marketing surfaces actually function end-to-end vs which are scaffolding. This audit gives us a clean map of "Marketing reality" so we can sequence the rebuild work alongside personal-workspace work.

## The two worktrees

| | Path | What it is |
|---|---|---|
| **Node reference** (frozen, do not modify) | `/Users/artemis/Desktop/Artemis/claudeck-artemis/` | The original Node app. Ground truth on shape, behavior, data, endpoints. |
| **Python rebuild** (audit target) | `/Users/artemis/Desktop/Artemis/artemis-os/` | The active Python rebuild. Audit what's there, what's broken, what's missing. |

## Scope — five surfaces

Each appears in the sidebar under "MARKETING". The frontend loaders are in `public/js/features/home.js`. The Python backend lives largely in `artemis/marketing/`.

1. **Dashboard** — `MARKETING_DASHBOARD_VIEW` → `loadMarketingDashboard()`
2. **Writing Studio** — `WRITING_STUDIO_VIEW` → `loadWritingStudio()`
3. **Campaigns** — `MARKETING_CAMPAIGNS_VIEW` → `loadMarketingCampaigns()`
4. **Signals Inbox** — `MARKETING_SIGNALS_VIEW` → `loadMarketingSignals()`
5. **Approval Queue** — find the loader by grepping `approval` in the navigation/home modules

## What to check for each surface

For each of the five, produce a section in the report covering:

### A. Frontend
- Where is the page shell rendered? File path + function name.
- What API endpoints does it call? List every `fetch()` call inside the shell loader and its descendants. Use grep aggressively. **Pay attention to which endpoints are versioned/namespaced under `/api/marketing/*` vs other prefixes** — that's where contract drift hides.
- What response shape does the renderer expect?
- Are there Web Components specific to this surface? List them.
- Compare to Node's version of the same shell — is the Python frontend reading from a different endpoint, expecting a different shape, or missing a sub-component?

### B. Backend
- Python: which module under `artemis/marketing/` handles this? Routes file? Service layer? DB models?
- Does each endpoint the frontend calls exist in Python? For each, document: route registered (Y/N), return shape, DB tables touched.
- Node: corresponding `server/<thing>.js` or route file — what was the contract?
- DB row counts for tables related to this surface. Run via:
  ```bash
  cd /Users/artemis/Desktop/Artemis/artemis-os && .venv/bin/python -c "
  import asyncio
  from sqlalchemy import text
  from artemis.db import SessionLocal
  async def main():
      async with SessionLocal() as s:
          for tbl in ['<table1>', '<table2>']:
              r = await s.execute(text(f'SELECT COUNT(*) FROM {tbl}'))
              print(f'{tbl}: {r.scalar()}')
  asyncio.run(main())
  "
  ```

### C. End-to-end smoke
For each surface, try clicking through it in a real browser (assume the app is running on `http://localhost:8000`). Document:
- Does the page load without errors? If not, paste the console/network error.
- Does it render real data or just scaffolding/loading state?
- Are interactions wired (buttons, forms, modal open/close)? Pick one obvious interaction per surface and test it.
- Take a screenshot or describe the visible state in 2 sentences.

### D. Compared to Node
- What does the Node version do for this surface that the Python version doesn't yet?
- Key behaviors that **MUST** be preserved when rebuilding.

### E. Gap summary
Three buckets per surface:
- **Working** — what functions end-to-end
- **Broken** — what's wired but doesn't work
- **Missing** — what Node has and Python doesn't

### F. Suggested divvy
- **Lead** (architectural / cross-cutting / needs decisions)
- **Worker** (mechanical port from Node — clear contract)
- **Codex** (self-contained / well-specified)
- LOC estimate per surface

## Special focus: Writing Studio

Writing Studio has the most prior commits in `docs/HANDOFF.md` (Slice 1 through 4, Google Docs round-trip, autosync, finder rail, etc.). It is likely the most mature Marketing surface. **Pay extra attention to:**
- Does the Google Docs import/export work end-to-end? (test by importing a doc via the UI)
- Does the autosync preview actually preview?
- Are writing rules / examples / sources / profiles all populated from the SQLite migration?
- Is the active draft persisted across refresh?

## Special focus: Campaigns

`docs/HANDOFF.md` references "Campaign Workspace State Machine / Gate Wiring" (commit `2026-05-14`). Check whether:
- The state machine is actually invoked when transitioning campaigns
- Gates render the right copy / disable the right buttons
- The handoff between Marketing and Personal (e.g. "this campaign creates 3 Jira tickets") is real or stubbed

## Tools to use

```bash
# Marketing routes inventory
ls /Users/artemis/Desktop/Artemis/artemis-os/artemis/marketing/routes/
ls /Users/artemis/Desktop/Artemis/artemis-os/artemis/marketing/

# Frontend shell loaders
grep -n "loadMarketing\|loadWritingStudio\|MarketingDashboard\|MARKETING_" \
    /Users/artemis/Desktop/Artemis/artemis-os/public/js/features/home.js | head -50

# Find every Marketing API call from frontend
grep -rn "/api/marketing" /Users/artemis/Desktop/Artemis/artemis-os/public/

# Test an endpoint
curl -s http://localhost:8000/api/marketing/dashboard 2>&1 | head -c 500

# DB tables for marketing
psql -h localhost -U artemis -d artemis_os -c "\dt" | grep -iE "campaign|marketing|signal|approval|writing|asset"
```

## What you must NOT do

- Do not modify any file in either worktree
- Do not run `git` operations that change state
- Do not run alembic migrations, tests, or scripts that touch the database (read-only queries via `SELECT` and `\dt` are fine)
- Do not "fix" anything you find — your job is to map the territory, not change it

## Deliverable

A single markdown file at `/Users/artemis/Desktop/Artemis/artemis-os/audits/marketing-section-gap-report.md`.

Structure:

```markdown
# Marketing Section — Gap Report (Py vs Node)

Generated: <date>
Auditor: Codex

## TL;DR (one paragraph)
<5 sentences: state of Marketing slab overall, biggest blocker, smallest win, which surface is closest to ship-ready.>

## Per-surface audits

### 1. Dashboard
[A-F]

### 2. Writing Studio ← extra scrutiny
[A-F + special focus answers]

### 3. Campaigns ← extra scrutiny on state machine
[A-F + special focus answers]

### 4. Signals Inbox
[A-F]

### 5. Approval Queue
[A-F]

## Cross-cutting observations
- Patterns common across Marketing surfaces
- Shared infrastructure (signals → approval queue → campaigns) — is the plumbing connected?
- Tables that are populated vs empty
- Risk callouts (anything that looks like it'll wipe data or has destructive defaults)

## Recommended sequencing
Ordered list. Prefer wins that unblock multiple surfaces. Note which surface should ship first vs which can wait.

## Estimated total effort
LOC count + Lead/Worker/Codex split + half-day count.
```

## Quality bar before you report done

- [ ] Every surface covered with all six subsections (A-F)
- [ ] Writing Studio + Campaigns special-focus questions answered
- [ ] Every "doesn't exist in Python" claim verified by file check
- [ ] At least one end-to-end smoke test per surface paste-in
- [ ] Recommendations are actionable, not "investigate further"
- [ ] Report under ~1000 lines

## Where to start

1. Read this brief twice
2. List `artemis/marketing/` directory tree side-by-side with `claudeck-artemis/server/` files mentioning marketing/campaign/signals/approval/writing
3. Go top-down through the sidebar (Dashboard → Writing Studio → Campaigns → Signals Inbox → Approval Queue)
4. End with cross-cutting observations and sequencing recommendations
