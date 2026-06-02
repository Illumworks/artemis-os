# J6c — Meetings page rebuild (post-meeting workflow)

**Owner:** Worker (Sonnet)
**Scope:** ~400 LOC, frontend-heavy. Estimated: half-day.
**Depends on:** J6a (Granola integration — DONE), `/api/granola/*` legacy aliases (DONE).
**Blocker for:** Focus page walkthrough.

## Why

Jon's primary use of Meetings is **post-meeting** — review what happened, extract action items, push them into the right system. Not prep. The current J6a UI does the right thing for a "data viewer" but not for a workflow tool. After live testing on 2026-05-18 Jon called out:

1. List + post-meeting panel **stacks vertically** when it should be side-by-side. Layout bug.
2. Right panel has only one view (transcript dump). Needs **tabs**: **Actions** (default) and **Transcript**.
3. Transcript tab needs an **AI search composer** ("ask a question about this transcript").
4. **No way to act on extracted action items.** Each item should support: Convert to Jira / Update OKR KR / Slack reminder / Save as personal todo.

## Acceptance — what done looks like

### Layout
- [ ] Today canvas renders as **2 columns side-by-side** at viewport widths ≥ 900px. List left (col-span-4 equivalent), detail panel right (col-span-8). Mirror Past canvas (`meetings-past-canvas` grid).
- [ ] Empty-state ("Select a meeting") shows in the right panel, NOT below the list.
- [ ] Manual smoke: open Meetings page on a 1280×800 viewport, confirm visually side-by-side. Paste a screenshot in the report.

### Right panel — tabs
- [ ] Tab strip at the top of the right panel with two buttons: **Actions** (default, active on load) and **Transcript**.
- [ ] Active tab uses the existing `meetings-tab-btn.active` styling (mirror the Today/Past strip styles, not duplicate them).
- [ ] Switching tabs swaps panel content; transcript/action data is fetched once per meeting selection and cached locally — do not re-fetch when switching tabs.

### Actions tab (default)
- [ ] Renders the extracted action-item list (use existing `extractActionItemsFromText()` in `home.js`, or accept structured `result.action_items` if Granola provides it).
- [ ] Each action row has:
  - The action text
  - A **kebab menu** (`⋯`) with 4 follow-up options:
    - **Convert to Jira issue** — POSTs to a new `/api/meetings/{id}/actions/jira` route (see below). Pre-fills summary = action text, description = "From meeting: <title>\n<meeting date>\n\nAction: <text>", project = `MT` (Jira project key from `integration_configs`).
    - **Update an OKR key result** — opens a small picker modal listing current KRs; selection POSTs to `/api/okr/key_results/{id}/evidence` with `{note: action_text, source: "meeting:<meeting_id>"}`.
    - **Schedule Slack reminder** — opens a tiny "when?" picker (Now+1hr / Tomorrow 9am / custom). POSTs to `/api/slack/reminder` with `{text, when}`.
    - **Save as personal todo** — POSTs to a new `/api/todos` route (see below). Local-only.
  - After action: row shows a small inline status pill (e.g. "Sent to MT-123" with link, or "Reminded at 4pm") that persists for the session.
- [ ] If the action item has already been routed (via the new `meeting_action_routings` table — see Backend below), the row renders with the pill already showing.

### Transcript tab
- [ ] Renders the meeting transcript (existing `result.transcript`) in a scrollable pre block.
- [ ] **Above the transcript**, an AI search composer: single input + "Ask" button. Posting calls `/api/meetings/{id}/ask` with `{question}`, which returns `{answer, citations[]}` (citations are character-offsets into the transcript or quoted snippets — either fine). Render answer above the transcript. Don't replace the transcript; the composer is a Q&A overlay.
- [ ] Backend route uses the existing `resolve_adapter()` chain — Claude Code CLI → Codex → LM Studio → Anthropic — so it's free-by-default for Jon. System prompt: "Answer the user's question about this meeting transcript. Quote relevant lines verbatim. If the answer isn't in the transcript, say so." User message: `Transcript:\n<full text>\n\nQuestion: <q>`.

### Hero (Live mode)
- [ ] Keep the slim hero from the current Lead session (single row: badge / date / compact stats inline).
- [ ] Remove the "Prep Lens" and "Follow-up Pressure" columns entirely from the Today canvas. They're prep-mode artifacts and don't belong on a post-meeting surface.

### Backend additions
- [ ] New table `meeting_action_routings` (alembic migration):
  ```sql
  CREATE TABLE meeting_action_routings (
    id            SERIAL PRIMARY KEY,
    meeting_id    TEXT NOT NULL,
    action_text   TEXT NOT NULL,
    routed_to     TEXT NOT NULL,  -- 'jira' | 'okr' | 'slack' | 'todo'
    target_id     TEXT,           -- 'MT-123' for jira; KR id for okr; etc.
    target_url    TEXT,           -- deep link
    routed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, action_text, routed_to)
  );
  ```
  Idempotent — the UNIQUE constraint means clicking "Convert to Jira" twice doesn't double-create.
- [ ] New route `POST /api/meetings/{meeting_id}/actions/jira` — body `{action_text}`. Creates Jira issue via existing `JiraClient.create_issue(project=MT, summary=action_text, description=...)`. Writes a `meeting_action_routings` row. Returns `{ok, key, url}`.
- [ ] New route `POST /api/meetings/{meeting_id}/actions/okr` — body `{action_text, kr_id}`. Appends an `okr_activity` row tagged as evidence. Writes routing row. Returns `{ok}`.
- [ ] New route `POST /api/meetings/{meeting_id}/actions/slack` — body `{action_text, when}` (ISO timestamp). Uses existing Slack integration to schedule a DM. Writes routing row. Returns `{ok}`.
- [ ] New route `POST /api/meetings/{meeting_id}/actions/todo` — body `{action_text}`. Creates a local todo row (use existing `notifications` table or add a `personal_todos` table — your call, document in the report). Writes routing row. Returns `{ok}`.
- [ ] New route `POST /api/meetings/{meeting_id}/ask` — body `{question}`. Uses `resolve_adapter()` for LLM, returns `{answer, citations}`.
- [ ] New route `GET /api/meetings/{meeting_id}/routings` — returns existing routings so the frontend can render the persisted pills.

### Frontend organization
- [ ] All new logic in a NEW file `public/js/features/meetings.js` — extract from the current `home.js` block. `home.js` keeps the page-routing entry point, calls into `meetings.js` for everything else. (`home.js` is already ~7000 lines; we should not pile more in.)
- [ ] Web Components for the 4 follow-up modal pickers (one each for Jira / OKR / Slack / Todo). Light DOM, mirror existing component patterns.

## Quality acceptance — tick every box before reporting done

- [ ] All entry points work — clicking each meeting row on Today AND Past selects + loads it
- [ ] All 4 follow-up actions tested end-to-end against real integrations:
  - Jira issue actually appears in `MT` project (paste the issue URL in report)
  - OKR KR shows the new evidence row
  - Slack reminder visible in your Slack reminders queue
  - Todo persists across a page refresh
- [ ] Idempotency: clicking the same Convert-to-Jira twice produces ONE issue, not two
- [ ] Manual smoke output pasted **verbatim** in your report — including screenshots of the rebuilt page
- [ ] Tests: at minimum a route-test per new endpoint (happy + 1 failure mode). No need to test the frontend Web Components beyond a snapshot.
- [ ] `ruff check` + `mypy` clean
- [ ] Diff re-read twice. No `TODO` or stub responses in shipped code.
- [ ] Migration `alembic upgrade head` is reversible — `alembic downgrade -1` then `alembic upgrade head` produces the same schema

## Out of scope (separate brief)

- Calendar-driven **auto-fire** of post-meeting summary into memory → that's J6d.
- Bulk action operations ("convert all items").
- Cross-meeting summarization ("what did I commit to this week?") — comes after M2 memory is in.

## Where to start

1. Read this brief twice
2. Read `artemis/routes/meetings.py` and `public/js/features/home.js` lines 1225-1305 + 2860-2925 + 3037-3105 (current Meetings logic)
3. Run the app, click around the current Today tab to understand the behavior we're replacing
4. Build backend routes first (testable in isolation); frontend last
