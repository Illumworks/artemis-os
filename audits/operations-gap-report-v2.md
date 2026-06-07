# Operations Section — Gap Report v2 (Py vs Node)

Generated: 2026-05-20  
Auditor: Codex  
Scope: Python rebuild at `/Users/artemis/Desktop/Artemis/artemis-os`, compared against the frozen Node reference at `/Users/artemis/Desktop/Artemis/claudeck-artemis`.  
Mode: audit/report refresh. No application code was changed.

## TL;DR

Operations is no longer blocked by the original trailing-slash failure. J10 fixed no-slash list compatibility for the builder collections, and J11 moved Agents Operations much closer to Node parity: instruction files, supporting files, assigned skills, run aliases, enriched detail, and package policy fields now exist in Python. Meetings, Daily Brief, Slack signals/triage, Dev Projects, and OAuth token refresh have also landed as real Python slices.

The big remaining Operations gaps are now narrower and clearer. Automations is still not ported as a first-class backend. Memory has a stronger M1 lossless foundation and MCP/read-side internals, but still has no UI HTTP route layer. Skills still lacks Node's lifecycle/import/category/template package behavior. Workflows have CRUD/execution basics, but not latest-run compatibility or full background run parity.

Verification note: a focused test run of slash compatibility, J11 Agents parity, Daily Brief, Slack signals/triage, and token refresh produced 104 passed / 2 failed. The failures appear outside this audit's report-writing scope: Slack permalink expected `slack.com` while code now returns `amiralearning.slack.com`, and a migration test references a missing `.claude/worktrees/...` directory.

## Evidence Snapshot

| Slice / surface | Current evidence |
|---|---|
| J10 trailing-slash compat | Builder and Marketing list routers now register both `@router.get("")` and `@router.get("/")`; `tests/test_trailing_slash_compat.py` guards this. |
| J11 Agents Operations parity | `artemis/routes/builders/agents.py` now includes instruction file, files, skills, active/recent/search run aliases, run-by-id, run context, and enriched detail. |
| Agent package policy | `Agent` model and schemas now persist `fallbackProvider`, `fallbackModel`, `memoryPolicy`, `permissionMode`, and `outputContract`. |
| Skills assignment | `agent_skills` join table and `POST /api/skills/{slug}/assign` / `unassign` exist. |
| Meetings J6c/d/e | `/api/meetings/*` supports overview, list, summary, transcript lazy persistence, action routing, ask, and persisted routings; scheduler starts in app lifespan. |
| Daily Brief J7 | `/api/daily-brief`, `/generate`, and `/history` exist with persisted snapshots. |
| Slack J8/J9/J9b/J9d | `/api/slack/signals`, mention triage list, resolution, name resolution path, direct/channel/group mention filtering, and bot-filter behavior are represented in tests and routes. |
| M1 Memory | `raw_inputs`, hash chain, archive/backup modules, and restore drill tests exist; Memory UI HTTP routes still do not. |
| Dev Projects v2/v3 | `/api/dev-projects/*` and `/ws/dev-projects/{sessionId}` exist; frontend module is a Claude Desktop/Claude Code-style project/session shell. |
| J10e token refresh | App lifespan starts `start_token_refresh_scheduler()`; provider refreshers cover Slack, Google Calendar, and Granola. |

## Per-surface audits

### 1. Automations

#### A. Frontend

The Operations Automations page is still implemented in `public/js/features/operations-shell.js`. It calls `listAutomationsApi()`, `createAutomationApi()`, `updateAutomationApi()`, `deleteAutomationApi()`, `runAutomationApi()`, `listAutomationRunsApi()`, and approvals helpers from `public/js/core/api.js`.

The page expects a real automation registry with `id`, `name`, `description`, `status`, `trigger_type`, `schedule_config`, `target_type`, `target_id`, model/provider policy, approval policy, output config, metadata, and `latest_run`. It also reads Agents and Workflows as target pickers.

Separately, `public/js/features/home.js` now contains an explicit temporary Automations bridge into Workflows. That bridge is honest copy: it says Artemis does not have a first-class Automations workspace yet and reuses saved workflow inventory as the closest available surface.

#### B. Backend

Still missing as a backend surface. There is no mounted `artemis/routes/automations.py`, no automation model, and no automation run model in the Python app. `rg` finds frontend callers but no Python `/api/automations` route.

Approvals exist and now support no-slash list compatibility, but that does not give Automations its own registry, scheduler, or run records.

#### C. Stuck Loading

Automations no longer shares the old global trailing-slash class of failures; its problem is simpler: `/api/automations` is absent. The Operations page catches the failed load and renders "Could not load automations." The separate Workflows bridge can give a truthful fallback path, but the real Automations page is still unavailable.

#### D. Compared To Node

Node had `server/routes/automations.js` with `automations` and `automation_runs`, latest-run embedding, active/paused/archived states, approval-gated run creation, and headless agent/workflow dispatch.

Python has none of that yet. The available Workflows bridge is a UX stopgap, not a port of Node automation semantics.

#### E. Gap Summary

Working: frontend shell, explicit Workflows bridge, generic approvals storage.

Broken: all `/api/automations*` calls still fail because the route family is unmounted.

Missing: tables, repository, CRUD, manual run, run history, scheduler semantics, approval-resume side effects, archive-not-delete behavior, latest-run list embedding.

#### F. Suggested Divvy

Owner: Worker, with Lead review on approval and background-run semantics.

Suggested slice: port Node automation storage and route contract after Workflows and Agents are stable targets. Keep the Workflows bridge until `/api/automations` can return a truthful empty list.

### 2. Skills

#### A. Frontend

Skills still render through `loadSkillsShell()` in `operations-shell.js`, with edit/import UX split between that file and `public/js/features/skill-edit-modal.js`. The frontend still expects more than basic CRUD: statuses (`approved`, `proposed`, `archived`), categories, templates, URL/ZIP import, approval/archive transitions, usage metadata, and agent assignment.

The J11 assignment path is now real enough for the Agents panel to show linked skills and for assignment/unassignment to round-trip via API.

#### B. Backend

`artemis/routes/builders/skills.py` now supports both no-slash and trailing-slash list:

- `GET /api/skills`
- `GET /api/skills/`
- `POST /api/skills/`
- `GET /api/skills/{slug}`
- `PATCH /api/skills/{slug}`
- `DELETE /api/skills/{slug}`
- `POST /api/skills/{slug}/assign`
- `POST /api/skills/{slug}/unassign`

The model remains DB-only and relatively minimal: `slug`, `name`, `description`, `instructions`, `tools`, `kind`, `sourcePath`, `ownerUserId`, timestamps. `agent_skills` exists as a many-to-many join.

#### C. Stuck Loading

The original no-slash load failure is fixed. Skills should no longer fail solely because `/api/skills?kind=approved` lacks a trailing slash.

The next visible limitations are semantic rather than transport-level: status/category filters are still squeezed through `kind`, and category/template/import routes are absent.

#### D. Compared To Node

Node's skills surface was package-like: metadata plus editable disk bodies/helpers, front matter parsing, categories, lifecycle states, URL/ZIP import, templates, usage counting, and assignment. Python has CRUD and assignment, but not package import/lifecycle parity.

#### E. Gap Summary

Working: no-slash list compatibility, DB-backed skill CRUD, agent assignment/unassignment, linked skills in agent detail.

Broken: frontend concepts of `status` and `category` do not map cleanly to the Python `kind` field.

Missing: lifecycle routes, categories, templates, import-url, import-zip, disk package behavior, helper files, front matter parsing, usage/success metrics.

#### F. Suggested Divvy

Owner: Worker.

Suggested slice: decide DB-only versus package-backed skills. If package behavior matters for Codex/Claude-style skill portability, port the Node package parser and keep DB rows as index metadata.

### 3. Agents

#### A. Frontend

Agents still load through `public/js/features/agents.js` and the Operations Agents tab in `operations-shell.js`. The frontend expects list/detail CRUD, execution, chains/DAGs, run observability, instruction file editing, supporting files, assigned skills, and package policy controls.

The API helper layer already targets the Python split route families: `/api/agents`, `/api/agent-chains`, `/api/agent-dags`, `/api/agent-runs`, plus Node-compat aliases under `/api/agents/runs/*` and `/api/agents/context/:runId`.

#### B. Backend

This is the largest improvement since the original audit.

`artemis/routes/builders/agents.py` now supports:

- `GET /api/agents` and `/api/agents/`
- CRUD for individual agents
- `GET /api/agents/{agent_id}/runs`
- `GET /api/agents/{agent_id}/instruction`
- `PUT /api/agents/{agent_id}/instruction`
- `DELETE /api/agents/{agent_id}/instruction`
- `GET /api/agents/{agent_id}/files`
- `GET /api/agents/{agent_id}/skills`
- `GET /api/agents/runs/active`
- `GET /api/agents/runs/recent`
- `GET /api/agents/runs/search`
- `GET /api/agents/runs/{run_id}`
- `GET /api/agents/context/{run_id}`

The model/schema now includes the J11 package policy fields: fallback provider/model, memory policy, permission mode, and output contract.

#### C. Stuck Loading

The known Agents spinner root cause from the original audit is fixed. `/api/agents`, `/api/agent-chains`, and `/api/agent-dags` now register no-slash list aliases, and the parity tests cover the new subresources.

Residual loading risks are now data/state risks rather than route-registration risks: empty chains/DAGs, no supporting files, unavailable provider credentials, or executor failures.

#### D. Compared To Node

Python now preserves most of Node's Operations-facing agent contract while using Postgres for definitions and run records. File-backed instruction/supporting-file behavior is implemented under `~/.artemis/agents` by default, with `ARTEMIS_AGENTS_DIR` override for tests.

Remaining drift: Node stored more builder definitions as JSON/package files, while Python stores the primary definitions in Postgres. That is an intentional rebuild choice, but it means package export/import semantics are still not equivalent.

#### E. Gap Summary

Working: list compatibility, CRUD, execution routes, run aliases, context aliases, instruction file read/write/delete, supporting-file listing, assigned skill listing, enriched agent detail, package policy fields.

Broken: no major frontend-blocking route mismatch observed in the audited paths.

Missing: full package import/export, richer supporting-file management beyond list, deeper provider/package validation, and any Automations integration that targets agents.

#### F. Suggested Divvy

Owner: mostly complete for current Operations parity. Future owner depends on scope.

Suggested next step: add export/import and supporting-file upload/delete only when Jon starts treating agents as portable packages rather than local DB rows.

### 4. Workflows

#### A. Frontend

Workflows load through `public/js/features/workflows.js` and the Operations Workflows tab. The frontend expects list/detail CRUD, run, run history, and latest-run lookup for detail panels.

The Automations bridge also leans on Workflows as the current closest proxy for saved operational procedures.

#### B. Backend

`artemis/routes/builders/workflows.py` now supports no-slash and trailing-slash list:

- `GET /api/workflows`
- `GET /api/workflows/`
- `POST /api/workflows/`
- `GET/PATCH/DELETE /api/workflows/{workflow_id}`
- `GET /api/workflows/{workflow_id}/runs`

Execution wiring lives in `artemis/routes/builders/execution.py`, with workflow execution backed by `workflow_runs` and context rows.

Still missing: `GET /api/workflows/{id}/runs/latest`, which `public/js/core/api.js` calls and treats as nullable on 404.

#### C. Stuck Loading

The original no-slash list failure is fixed. A missing latest-run endpoint should degrade to null rather than block the page, based on the helper behavior.

#### D. Compared To Node

Node returned quickly from workflow run creation and executed in the background through a runner. Python has execution wiring and run records, but the audit did not verify equivalent background/asynchronous semantics for the full UI contract.

Node also exposed latest-run as a convenience endpoint; Python still does not.

#### E. Gap Summary

Working: CRUD, list compatibility, run history, execution wiring.

Broken: latest-run helper still points at an unimplemented endpoint.

Missing: latest-run endpoint, background run parity if needed for long workflows, integration with first-class Automations.

#### F. Suggested Divvy

Owner: Codex or Worker.

Suggested slice: add `GET /api/workflows/{id}/runs/latest` and a small route test. Treat background semantics as a separate decision only if the current execution path blocks the UI.

### 5. Memory

#### A. Frontend

The Memory UI still lives in `public/js/features/memory-shell.js` and expects the broad Node HTTP contract:

- list/search/top/stats
- embeddings status/ensure
- archive export/import
- SQLite backup compatibility
- evidence and drawer lookup
- entity and graph neighborhood views
- update/create/supersede-delete
- optimizer endpoints
- skill promotion

The status endpoint still marks `memory-shell` unavailable.

#### B. Backend

The internal backend is much stronger than the original audit described. M1 added the lossless foundation pieces:

- `artemis/memory/raw_inputs.py`
- `artemis/memory/hashchain.py`
- `artemis/memory/archive.py`
- `artemis/memory/backup.py`
- tests for raw inputs, hash chain, archive, backup/restore drill

The older B-slice modules also remain present: storage, retrieval, embeddings, consolidation, graph extraction, and read-side MCP server.

However, there is still no mounted `/api/memory` FastAPI route module in `artemis/main.py`.

#### C. Stuck Loading

Memory is still unavailable from the UI because `/api/memory*` is absent. The old `/api/agents` side blocker is fixed, but the Memory shell's own HTTP contract is still missing.

#### D. Compared To Node

Node exposed the complete Memory UI route contract over its keystone. Python has a cleaner and more lossless internal foundation, plus MCP read-side access, but not the browser-facing route layer.

The important difference from v1 of the audit: Memory is no longer "internals exist but thin"; M1 makes provenance and recoverability stronger. The gap is route/productization, not raw data durability.

#### E. Gap Summary

Working: memory store/retrieval/consolidation/graph internals, MCP read-side, raw input provenance, hash chain, archive, backup/restore tests.

Broken: Memory UI HTTP calls still 404 because no route module is mounted.

Missing: `/api/memory` route family, Node-compatible response shapes, UI stats/search/top wrappers, evidence/drawer endpoints, archive/backup HTTP routes, graph HTTP routes, optimizer routes, lossless supersession HTTP behavior.

#### F. Suggested Divvy

Owner: Lead + Worker.

Suggested slice: Lead defines the HTTP compatibility contract over the M1/B internals; Worker implements route wrappers and tests. Keep the lossless rule explicit in every destructive-looking endpoint.

### 6. Meetings

#### A. Frontend

Meetings now has a dedicated frontend module, `public/js/features/meetings.js`, with Today and Past tabs, action routing controls, transcript panel, and ask-over-meeting UI.

It calls `/api/meetings/overview`, `/api/granola/meetings`, `/api/meetings/{id}/summary`, `/api/meetings/{id}/routings`, `/api/meetings/{id}/ask`, and action routes for Jira, OKR, Slack, and todo.

#### B. Backend

`artemis/routes/meetings.py` supports overview, list, single meeting detail, persisted summary, lazy transcript backfill, action routing, ask, routings, and personal todos. `artemis/meetings/scheduler.py` starts from app lifespan and runs the calendar-driven summarizer.

J6e transcript persistence ties summary records to `raw_input_id`, aligning the meeting path with M1 provenance.

#### C. Stuck Loading

The surface should render a connected/not-connected state rather than crash. If Granola is not configured, the routes return a structured `not_connected` response.

#### D. Compared To Node

This is now a native Python rebuild rather than a Node holdover. It preserves the important user-facing behavior: past meeting browsing, transcript access, action extraction/routing, and post-meeting summary persistence.

#### E. Gap Summary

Working: overview/list/detail, summary, transcripts, action routes, ask, routings, scheduler.

Broken: no clear blocker found in code inspection.

Missing: production smoke with live Granola credentials and real downstream Jira/Slack/OKR actions.

#### F. Suggested Divvy

Owner: QA/manual smoke.

Suggested slice: run a live connected walkthrough after credentials are present: Past tab search, open transcript, ask question, route one action to todo and one to Jira/Slack.

### 7. Daily Brief

#### A. Frontend

`public/js/core/api.js` calls `/api/daily-brief` and `/api/daily-brief/generate`. The surface is integrated as a Focus/Operations-adjacent daily planning tool.

#### B. Backend

`artemis/routes/daily_brief.py` supports:

- `GET /api/daily-brief`
- `POST /api/daily-brief/generate`
- `GET /api/daily-brief/history`

It persists snapshots through `artemis/brief/repository.py` and generates from source gatherers.

#### C. Stuck Loading

No route-level blocker found. Empty state is explicit: `{"brief": null, "exists": false}`.

#### D. Compared To Node

The Python port now has the core Node-style behavior: instant latest snapshot, explicit generation, and history metadata.

#### E. Gap Summary

Working: persisted snapshots, generation endpoint, history endpoint, resilient source gathering tests.

Broken: focused test run surfaced warnings from mocked async source calls, but no Daily Brief test failure.

Missing: live end-to-end generation with real provider credentials and current source availability.

#### F. Suggested Divvy

Owner: QA/manual smoke.

Suggested slice: generate one real brief with connected Jira/Calendar/Slack/OKR and confirm the saved snapshot reloads without another LLM call.

### 8. Slack Signals And Triage

#### A. Frontend

The frontend calls `/api/slack/signals`, `/api/slack/signals/mentions`, and `/api/slack/signals/mentions/{id}/resolve` for the Focus Rail Slack card and triage list.

#### B. Backend

`artemis/routes/slack.py` exposes the Slack signals summary, unresolved mention list, and resolve endpoint. Supporting modules cover Slack API calls, signal counting, triage formatting, mention type classification, name resolution, and bot filtering.

#### C. Stuck Loading

The route intentionally returns 200 with `connected: false` when Slack is not connected, so the UI has a stable not-connected state.

#### D. Compared To Node

The Python rebuild is now native rather than a placeholder. J9/J9b/J9d moved it from counts-only toward an actionable triage list with name resolution and noise filtering.

#### E. Gap Summary

Working: signals summary, unresolved triage list, resolve action, direct/channel/group filtering, name-resolution path, bot filtering.

Broken: one existing focused test expects generic `https://slack.com/...` permalinks while the code returns the Amira workspace URL. That may be a stale test rather than a product bug.

Missing: live connected smoke and decision on canonical permalink host.

#### F. Suggested Divvy

Owner: Worker for test alignment, QA for live smoke.

Suggested slice: update the permalink expectation if `amiralearning.slack.com` is intentional; then smoke a real direct mention through list, resolve, and count decrement.

### 9. Dev Projects

#### A. Frontend

`public/js/features/dev_projects.js` and the `dev-projects-*` components implement a Claude Desktop/Claude Code-style project/session shell with project picker, session list, composer, permission cards, annotations rail, and WebSocket updates.

#### B. Backend

`artemis/routes/dev_projects.py` exposes project CRUD/archive/permanent delete, folder browse/validate/pick compatibility, session CRUD/fork/archive, messages, permissions, annotations, file listing/search, and `/ws/dev-projects/{sessionId}`.

#### C. Stuck Loading

No route-level blocker found in code inspection. Tests cover empty project list, project/session/message/annotation happy path, permissions, history resume, fork, archive, provider switch, defaults, and folder browsing.

#### D. Compared To Node

This is not merely a port of the old Node Dev Projects. It is a v2/v3 rebuild with a more Codex/Claude-style UI and local permission-gated tool loop.

#### E. Gap Summary

Working: backend, WebSocket, project/session persistence, tool permission flow, annotations, file browsing, frontend shell.

Broken: no obvious blocker in inspected paths.

Missing: broad manual browser QA across v3 UI states and real provider execution beyond tested local file/tool flows.

#### F. Suggested Divvy

Owner: QA/manual smoke.

Suggested slice: browser walkthrough with a real repo, one permitted command, one denied command, a forked session, annotations, and reload persistence.

## Cross-cutting observations

- J10 meaningfully changed the audit baseline: list-route slash compatibility is now guarded by route introspection tests.
- J11 moved Agents from "known broken" to one of the stronger Operations surfaces.
- Status inventory is better but still optimistic in places: `writing-studio` is marked available even though the full UI still calls missing overview/draft routes; `memory-shell` remains correctly unavailable.
- Automations remains the one pure Operations backend absence.
- Memory is internally healthier than the UI suggests. The next Memory work should avoid rewriting internals and instead productize them through HTTP.
- Several frontend helpers still encode Node-era contracts and TODOs. Some are harmless fallbacks; others, like Writing Studio overview and campaign deliverable query routes, are still hard blockers in Marketing.

## Recommended sequencing

1. Keep Agents as "parity mostly done" and only add export/import when needed.
2. Port Automations as the next Operations feature because the UI still exposes it and the bridge is intentionally temporary.
3. Add Workflow latest-run compatibility.
4. Decide Skills package strategy, then add lifecycle/import/category routes.
5. Define Memory HTTP contract over M1/B internals and port the UI route family.
6. Run live manual smokes for Meetings, Daily Brief, Slack triage, and Dev Projects once credentials and a stable browser session are available.
