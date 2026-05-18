# BRIEF FOR CODEX — Dev Projects rebuild (Claude Code in-app)

You are Codex CLI driving an isolated, well-scoped slice in Jon's Artemis OS project. This brief is fully self-contained — assume zero prior conversation context.

---

## What Artemis OS is

A FastAPI + Postgres web app (`https://app.artemisos.me`) Jon uses as his personal operations OS. It has rails for Focus, Calendar, Meetings, Jira, OKR, Memory, Integrations, Marketing surfaces, and **Dev Projects** (the surface you're rebuilding).

- Repo: `/Users/artemis/Desktop/Artemis/artemis-os/` (Worker terminal works here)
- Lead's worktree: `/Users/artemis/Desktop/Artemis/artemis-os-lead/` ← **work in this worktree**
- Main branch is the integration branch; your branch should be `codex/dev-projects-rebuild`
- Python 3.11+, FastAPI, SQLAlchemy async, asyncpg, Pydantic 2, Alembic, pgvector
- Frontend is vanilla ES modules (no build step) loaded from `main.js`
- Tests are pytest; lint is `uv run ruff check`; types are `uv run mypy artemis` (strict)
- **Critical:** `uv run pytest` runs against `artemis_test` DB (forced by `tests/conftest.py`). Never override that.

---

## What you're building

**Goal: Rebuild the Dev Projects surface so it functions like the desktop Claude Code app, but lives inside Artemis OS.** Jon uses Dev Projects to drive code work via Claude Code CLI (and optionally Codex CLI) against folders on his Mac. The Node version had a passable version of this; the Python rebuild's current version is a stub.

### The Claude Code feel — what to copy

The desktop Claude Code app's loop, faithfully:

1. **Pick a project** (folder on disk) from a sidebar list — newly-added projects + recently-used appear at the top
2. **One chat surface per project** — streams in real time, tool-use cards inline, permission prompts inline, copy code blocks, render markdown including diagrams (highlight.js + mermaid already loaded as CDN globals)
3. **Session list** for the active project — resume any prior session in that project, archive, fork, delete
4. **Composer** at the bottom: textarea, file picker (`@`-mention paths from the project), image-paste, voice input (skip voice — out of scope here)
5. **Permission model**: side-effect tools (file writes, shell) require operator approval; toggle "bypass" per session
6. **Provider/model picker** per session — Claude / Codex / Anthropic-API / OpenAI-API / Gemini / LM Studio (registry already exists; just expose the picker)
7. **Parallel mode** (Pair / Trio / Quad): 2/3/4 independent project chats side-by-side. **Already shipped** in `public/js/ui/parallel.js` — you wire the new Dev Projects shell to the same parallel-mode hook so it benefits from it automatically.

### The added ask (uniquely-Artemis)

**A right-side annotation rail** that loads pages with an editable annotation surface — for web-dev projects. Treat this as a third-pane affordance:

- Left: project + session sidebar
- Center: chat
- Right: a slide-in panel that can:
  - Load a URL in an iframe (preview the local dev server, or a deployed staging URL)
  - Sit alongside an annotation textarea + "send to chat" button that appends "Re: <url>: <note>" to the composer
  - Persist annotations per session (stored on the session row in a `notes` JSONB field — schema below)

This rail is the differentiator. Don't skimp on it.

---

## Files you'll create / edit

### Backend (new + edits)

```
artemis/dev_projects/
  __init__.py
  models.py              # ORM: DevProject, DevSession, DevMessage, DevAnnotation
  repository.py          # async CRUD
  schemas.py             # Pydantic request/response shapes
  service.py             # session orchestration, message persistence
  loop_runner.py         # subprocess wrapper around `claude` / `codex` CLI binaries
  tools.py               # FA tool registrations (optional; defer if scope tight)

artemis/routes/dev_projects.py    # the HTTP endpoints (list below)

alembic/versions/0017_dev_projects.py    # migration for the four tables

tests/test_dev_projects.py         # ~25 tests
```

### Frontend (new + edits)

```
public/js/features/dev_projects.js          # main orchestrator (replaces existing stub)
public/js/components/dev-projects-sidebar.js
public/js/components/dev-projects-chat.js
public/js/components/dev-projects-composer.js
public/js/components/dev-projects-annotation-rail.js     # the right-side new feature
public/js/components/dev-projects-permission-card.js
public/js/components/dev-projects-session-row.js

public/css/features/dev-projects.css         # full styling (no separate Phase K wait)
```

### Files you MUST NOT touch

- `artemis/memory/*` — Worker M1 just shipped this; respect the verbatim invariant
- `artemis/integrations/*` — out of scope
- `artemis/floating_artemis/*` — has its own chat surface; Dev Projects is independent
- `artemis/providers/*` — already complete; you consume `get_adapter(provider_id)` and that's it
- `public/js/features/integrations.js`, `home.js`, anything outside the dev-projects scope
- `tests/conftest.py` — its data-loss safety guard is load-bearing

---

## DB schema (migration 0017)

```sql
CREATE TABLE dev_projects (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    path            TEXT NOT NULL UNIQUE,      -- absolute filesystem path
    last_opened_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at     TIMESTAMPTZ,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE dev_sessions (
    id              BIGSERIAL PRIMARY KEY,
    project_id      BIGINT NOT NULL REFERENCES dev_projects(id) ON DELETE CASCADE,
    title           TEXT,                       -- AI-summarized or user-set
    provider        TEXT NOT NULL DEFAULT 'claude-code',     -- claude-code | codex | anthropic | openai | gemini | lm-studio
    model           TEXT,                       -- provider-specific
    bypass_permissions BOOLEAN NOT NULL DEFAULT FALSE,
    notes           JSONB NOT NULL DEFAULT '[]'::jsonb,      -- annotation rail entries: [{url, note, created_at}, ...]
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at     TIMESTAMPTZ,
    fork_of         BIGINT REFERENCES dev_sessions(id) ON DELETE SET NULL,
    fork_at_message BIGINT
);
CREATE INDEX ix_dev_sessions_project ON dev_sessions(project_id, last_active_at DESC);

CREATE TABLE dev_messages (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES dev_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,              -- user | assistant | tool_result | system
    content         JSONB NOT NULL,             -- array of content blocks (text, tool_use, tool_result, image)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_dev_messages_session ON dev_messages(session_id, created_at ASC);

CREATE TABLE dev_annotations (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES dev_sessions(id) ON DELETE CASCADE,
    url             TEXT,                       -- the previewed URL
    note            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_dev_annotations_session ON dev_annotations(session_id, created_at DESC);
```

Round-trip (upgrade + downgrade) cleanly.

---

## REST API (`artemis/routes/dev_projects.py`)

All routes under prefix `/api/dev-projects`, JSON. No path duplicates with existing routes.

```
GET    /projects                          → list projects (active first, then archived)
POST   /projects                          → {name, path} → create
PATCH  /projects/{id}                     → rename / archive
DELETE /projects/{id}                     → soft-delete (archived_at = now())

GET    /projects/{id}/sessions            → list sessions in project
POST   /projects/{id}/sessions            → {provider, model?} → create
GET    /sessions/{id}                     → session detail + last 50 messages
PATCH  /sessions/{id}                     → rename, change provider/model, set bypass_permissions
DELETE /sessions/{id}                     → soft-delete
POST   /sessions/{id}/fork                → {at_message_id} → new session forked at that point

GET    /sessions/{id}/messages?cursor=    → paginated history (oldest-first within page; cursor = id)
POST   /sessions/{id}/messages            → {text, images?} → user message; kicks off a turn
                                            Server-side streams response via WS (see below)
POST   /sessions/{id}/permissions/{id}/approve → approve a pending tool call
POST   /sessions/{id}/permissions/{id}/deny    → deny

GET    /sessions/{id}/annotations         → list annotations
POST   /sessions/{id}/annotations         → {url, note} → save annotation
DELETE /annotations/{id}                  → remove
```

WebSocket (use the existing `artemis/ws/manager.py` pattern):
```
/ws/dev-projects/{session_id}      → server-pushed events: token deltas, tool_use, tool_result, permission_required, message_complete
```

Mirror the structure of `artemis/routes/floating_artemis.py` and `artemis/ws/routes.py` — those are the canonical patterns in this codebase.

---

## CLI subprocess wrapper (`artemis/dev_projects/loop_runner.py`)

For `claude-code` and `codex` providers: subprocess the binary with `--print --output-format json` (Claude Code) or equivalent flag set (Codex). The provider registry (`artemis/providers/registry.py`) already has `get_adapter("claude-code")` and `get_adapter("codex")` returning adapters that subprocess the binaries. **Reuse those adapters via the `stream()` method, do NOT write a parallel implementation.**

For API providers (`anthropic`, `openai`, `gemini`, `lm-studio`): use `get_adapter(provider_id).stream(request)` — they're all in `artemis/providers/` and conform to the same Protocol.

Tool-use round-trips: when the adapter yields a `StreamToolUseStart`/`StreamToolUseDelta` event, the runner:
1. Persists the tool_use block to `dev_messages`
2. Sends a `permission_required` WS event to the client
3. **Pauses** the loop pending operator approval (via `/permissions/{id}/approve` or `/deny`)
4. On approval: execute the tool (file write / shell), persist the `tool_result` to `dev_messages`, resume the loop with the result fed back

This is where the desktop Claude Code app's UX comes from — the tool-permission pause is the operator-trust gate.

---

## Frontend architecture

### Three-column layout

```
┌─────────────┬────────────────────────────┬──────────────────┐
│  Sidebar    │  Chat (active session)     │  Annotation rail │
│  Projects   │  ┌──────────────────────┐  │  ┌────────────┐ │
│  Sessions   │  │ Messages             │  │  │  URL input │ │
│             │  │                      │  │  ├────────────┤ │
│             │  │                      │  │  │  iframe    │ │
│             │  ├──────────────────────┤  │  │  preview   │ │
│             │  │ Composer             │  │  ├────────────┤ │
│             │  └──────────────────────┘  │  │  notes     │ │
│             │                            │  │  + send    │ │
└─────────────┴────────────────────────────┴──────────────────┘
```

Right rail is collapsible (default closed; open via toolbar icon in chat header).

### Components, briefly

- **`dev-projects-sidebar.js`** — top: "+ New project" button. List of projects (active first), each project expands to show its sessions. Active session highlighted. Click a session → loads it in the center pane.
- **`dev-projects-chat.js`** — message stream, markdown rendering (highlight.js + mermaid already global), tool_use cards inline, permission cards inline (red border, Approve/Deny buttons), provider/model picker in header, "fork from here" button on assistant messages.
- **`dev-projects-composer.js`** — autosize textarea, `@`-mention file picker (queries `GET /projects/{id}/files?q=...` — you'll need to add that route or piggyback on existing file-picker), image paste, Cmd+Enter to send.
- **`dev-projects-annotation-rail.js`** — toggle button in chat header opens this. URL input → iframe loads. Annotation textarea + "Send to chat" appends `Re: <url>: <note>` to composer + saves to `dev_annotations`. List of past annotations below with click-to-restore.
- **`dev-projects-permission-card.js`** — renders a pending tool-use card; shows tool name + args + impact estimate; Approve / Deny / "Approve and trust this tool for the session" (sets `bypass_permissions` for that one tool type).
- **`dev-projects-session-row.js`** — sidebar row component. Title (or "Untitled"), last-active timestamp, message count, status indicator (active/idle/archived).

### Wiring

In `public/js/features/dev_projects.js`:
- On surface mount, fetch projects, render sidebar
- On project click → fetch sessions, render
- On session click → fetch detail + messages, render chat
- WS connect to `/ws/dev-projects/{session_id}` for real-time
- On composer send → POST `/sessions/{id}/messages` (server streams response via WS)

### Parallel mode

Parallel mode (`parallel.js`) calls `enterParallelMode(N)` to split the canvas into N panes. The Dev Projects shell should be the content of each pane in parallel mode. The wiring is: when Dev Projects loads and parallel mode is active, mount N independent `dev-projects-chat` instances (one per pane), each with its own session selector. Reuse the existing `parallel.js` allocation endpoint — Dev Projects sessions can be created via `POST /api/parallel/sessions {count: N}` per the existing pattern.

---

## Quality acceptance checklist (you MUST tick every box before reporting done)

- [ ] All entry points work: clicking "Dev Projects" in rail → projects list renders; click project → sessions; click session → chat with full history; composer sends; assistant streams response
- [ ] Provider/model picker in chat header lets you switch between claude-code, codex, anthropic, openai, gemini, lm-studio. Picker reads `GET /api/floating-artemis/models` (already exists) — reuse it.
- [ ] Tool-use → permission card → Approve/Deny round-trip works. Denied tool stops the loop with an inline message. Approved tool executes and the loop resumes.
- [ ] "Fork from here" on an assistant message creates a new session with the conversation up to that point copied.
- [ ] Annotation rail: open via toolbar icon → enter URL → iframe loads → write a note → "Send to chat" appends to composer and persists to `dev_annotations`. Annotations list updates.
- [ ] Parallel mode (Pair/Trio/Quad): the rail toggle splits the canvas into N independent Dev Projects shells. Each has its own session selector + chat.
- [ ] Manual smoke I ran myself end-to-end (paste output in final report):
  - Create project pointing at `/Users/artemis/Desktop/test-project` (any folder)
  - Start a session with provider="claude-code"
  - Send "list the files in this directory"
  - Approve the bash tool call when it appears
  - Verify the response streams in
  - Open annotation rail, paste `http://localhost:3000` as URL, annotate "this looks broken", send to chat
  - Refresh page — session + messages + annotations all still there
- [ ] Diff scanned: no TODO placeholders, no "implement later" comments, no unused stubs
- [ ] Tests cover happy path AND: empty project list, deleting active session, permission denied mid-loop, fork at message, annotation persistence, WS reconnect mid-stream, provider switch mid-session
- [ ] All four gates green: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy artemis` (Success: no issues found)

---

## Out of scope (do NOT pull these into the slice)

- Voice input (Phase K)
- Cost/token dashboard per session (separate slice)
- File-tree explorer (annotation rail is the visual; file picker via `@`-mention is sufficient)
- MCP server config UI (separate slice)
- Multi-user collaboration on a session (single-user for V1)
- Mobile responsive (post-K)

---

## Verification commands to run before reporting done

```bash
cd /Users/artemis/Desktop/Artemis/artemis-os-lead
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run alembic upgrade head
uv run pytest                       # 1696 baseline + your new tests
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
```

For the live-app smoke test:
```bash
# Lead will fast-forward and bounce; you don't need to bounce the live app yourself.
```

---

## Reporting back

When done, write a final report:

- Branch name + final commit SHA
- Test count delta
- Last 5 lines of all four verification commands
- Manual smoke walkthrough output (verbatim)
- Every quality-checklist box ticked
- Files created / modified (table form)
- Judgment calls you made that weren't explicit in this brief
- Anything you deferred and why

Local-only git: commit on `codex/dev-projects-rebuild`. Do NOT push to remote. Do NOT open a PR.

The Lead Claude reviews your diff, runs the manual smoke himself, and merges into `main`. Don't worry about the merge.

---

**Final word:** Take your time. This is the surface Jon will spend the most time inside. The Claude-Code-feel matters more than feature count. Cut features before you cut polish.
