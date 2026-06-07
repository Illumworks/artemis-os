# Marketing Section Gap Report

Generated: 2026-05-18  
Scope: Python rebuild at `/Users/artemis/Desktop/Artemis/artemis-os`, compared against the frozen Node reference at `/Users/artemis/Desktop/Artemis/claudeck-artemis`.  
Mode: audit only. No application code was changed and no DB-changing tests, migrations, approvals, signal decisions, or campaign transitions were executed.

## Executive Summary

The Marketing shell is present and the Python backend has partial C2/C3 route coverage, but the section is not yet end-to-end ready. The Dashboard and Campaigns surfaces can read the live candidate endpoint; Writing Studio hard-fails on load; Signals and Approvals fall back to demo content because the frontend calls no-slash list endpoints while the Python routes are registered on `/`; and deeper campaign/deliverable/content-asset interactions still point at Node-era contracts that are missing or differently shaped in Python.

The most important gap is Writing Studio. The frontend is the full Node-era Writing Studio application, including Google Docs import/export, autosync preview, draft library, folders, rules/examples/sources, training candidates, edit history, versions, regeneration, and active-draft restoration. The Python route currently exposes only three write endpoints: create stub draft, submit review, and receive lifecycle events. The migrated writing rules/examples/sources data exists in SQLite/Postgres-backed tables, but the Marketing Writing Studio UI does not load it because it calls `/api/writing-studio/*` endpoints while the available CRUD lives under `/api/writing-rules/*`.

The second major gap is Campaigns. The live candidate list loads, but selecting the live candidate in the browser left the workspace blank with only "Select a campaign to open its workspace." The backend `/advance` route mutates only `decision_state`; it does not invoke a full campaign workspace state machine, gate model, decision log, deliverable state transitions, approval creation/resumption, or Marketing-to-Personal handoff. The frontend also has explicit TODOs and calls to missing endpoints such as `/writing-handoff`, `/campaign-deliverables?campaignId=...`, `/content-assets/links?campaignId=...`, and content asset archive.

## Evidence Snapshot

Read-only endpoint probe against the running local app:

| Endpoint | Result | Interpretation |
|---|---:|---|
| `/api/campaign-ops/candidates` | 200 | One live candidate exists and loads. |
| `/api/writing-studio/overview` | 404 | Writing Studio primary loader is missing. |
| `/api/writing-studio/drafts` | 404 | Draft library route is missing. |
| `/api/signal-queue?status=in_inbox` | 404 | Frontend no-slash call fails. |
| `/api/signal-queue/?status=in_inbox` | 200 | Backend list exists only with trailing slash. |
| `/api/approvals?status=pending` | 404 | Frontend no-slash call fails. |
| `/api/approvals/?status=pending` | 200 | Backend list exists only with trailing slash. |
| `/api/content-assets?status=draft` | 404 | Frontend no-slash call fails. |
| `/api/content-assets/?status=draft` | 200 | Backend list exists only with trailing slash. |
| `/api/campaign-deliverables?campaignId=1` | 404 | Frontend contract is missing. |
| `/api/campaign-deliverables/1` | 200 | Backend lists by candidate path, not query. |
| `/api/scouts/runs` | 200 | Scout runs route exists, no rows. |
| `/api/signal-criteria/rulesets` | 200 | Ruleset route exists, no rows. |

Read-only DB inventory:

| Table | Rows |
|---|---:|
| `signal_queue` | 1 |
| `campaign_candidates` | 1 |
| `campaign_deliverables` | 1 |
| `writing_profiles` | 1 |
| `writing_folders` | 1 |
| `writing_rules` | 2 |
| `writing_examples` | 7 |
| `writing_sources` | 9 |
| `approvals`, `rulesets`, `territory_config`, `scout_runs`, `content_assets`, `campaign_briefs` | 0 each |

Browser smoke summary:

| Surface | Visible result |
|---|---|
| Dashboard | Loads "Marketing Campaign OS" with 1 live campaign, 0 pending approvals, and 3 signals. Signal count is demo fallback because the signal list call fails. |
| Writing Studio | Shows "Could not load Writing Studio. Failed to load Writing Studio." |
| Campaigns | Shows "All Campaigns 1" and one live `human_gate_1` candidate. Clicking it marks it active but no workspace content renders. |
| Signals Inbox | Shows "Demo data" and three mock signals. "Add Signal" opens the form locally; write submission was not clicked. |
| Approval Queue | Shows three disabled demo approval cards. No live approvals render. |

## 1. Dashboard

### A. Frontend Status

`loadMarketingDashboard()` renders a static dashboard immediately, then `_fetchAndPatchDashboard()` overlays live campaign candidates from `fetchCampaignOpsOverview()` and best-effort approvals/signals counts from `listApprovalsApi()` and `listSignalQueueApi()` (`public/js/features/marketing-os.js:1819`, `public/js/features/marketing-os.js:1826`). This means the page appears functional even when parts of the API fail.

The live campaign path works because `fetchCampaignOpsOverview()` maps `/api/campaign-ops/candidates` to a campaigns-shaped object. Approval and signal counts are not fully live because their frontend calls use `/api/approvals?...` and `/api/signal-queue?...`, while the backend list routes are mounted at `/api/approvals/` and `/api/signal-queue/`.

### B. Backend Status

The candidate backend is real enough for the dashboard: `GET /api/campaign-ops/candidates` returns one candidate (`artemis/marketing/routes/campaign_ops.py:56`). Approvals and signal list routes exist but require trailing slashes (`artemis/marketing/routes/approvals.py:36`, `artemis/marketing/routes/signal_queue.py:164`). Rulesets and territory config are empty, so signal qualification-derived dashboard metrics are not meaningful yet.

### C. End-to-End Smoke

Browser: Dashboard opened from the Marketing nav. It displayed "Marketing Campaign OS", "1 campaign · 0 pending", one live campaign card, and the candidate stage `human_gate_1`. The signal count stayed at `3`, matching the mock list length rather than the one live signal in the DB.

Endpoint evidence: `/api/campaign-ops/candidates` returned 200 with one candidate; `/api/approvals?status=pending` and `/api/signal-queue?status=in_inbox` returned 404; the trailing-slash variants returned 200.

### D. Compared To Node Reference

The Node reference treated Marketing Dashboard as a coordination view over live campaign candidates, unified approvals, signal queue, scout/ruleset state, and campaign workspace status. Python has the candidate read path but not the same unified approval richness or signal list compatibility. The dashboard is therefore a partial live overlay on a static mock foundation.

### E. Gaps And Risks

The dashboard can overstate readiness because failures are intentionally swallowed and replaced by demo values. "1 campaign" is live, but "3 signals" is mock. Pending approval count currently reports zero because the failing no-slash approval call is caught and treated as an empty list, not as unavailable data.

### F. Suggested Divvy

Frontend: normalize Marketing list API calls to match Python routes or add an API helper that handles trailing slash compatibility. Add a visible "demo fallback" or degraded source indicator per metric, not only whole-section fallback.

Backend: add no-slash aliases for list routes or configure strict slash behavior consistently. Expand dashboard-compatible serializers only after the underlying entities are real.

QA: add a smoke test that asserts Dashboard count provenance for candidates, signals, and approvals separately.

## 2. Writing Studio

### A. Frontend Status

The frontend is the full Node-era Writing Studio. `loadWritingStudio()` calls `fetchWritingStudioOverview()` and `fetchGoogleOverviewApi()` before rendering the workspace (`public/js/features/writing-studio.js:107`). It then restores an active draft from a handoff, selected draft ID, existing state, or the first draft (`public/js/features/writing-studio.js:120`). It fetches the selected draft through `fetchWritingDraft()` (`public/js/features/writing-studio.js:137`).

The frontend imports and wires a much broader API surface: Google Docs create/import/unlink/export, autosync export/import/inspect/reconcile, folders, prompt/compose/invoke, versions, regeneration, edit history, training candidates, rules, examples, sources, and seed import (`public/js/features/writing-studio.js:3`). The API helper explicitly notes that `/api/writing-studio/overview` is not yet ported (`public/js/core/api.js:625`), then calls many `/api/writing-studio/*` endpoints (`public/js/core/api.js:626` through `public/js/core/api.js:987`).

### B. Backend Status

The Python `/api/writing-studio` route currently exposes only:

| Route | Status |
|---|---|
| `POST /api/writing-studio/drafts` | Create draft from `candidate_id`; returns a stub/local deliverable shape. |
| `POST /api/writing-studio/drafts/{draft_id}/submit-review` | Submit local deliverable for Gate 2 review. |
| `POST /api/writing-studio/drafts/{draft_id}/events/{event_kind}` | Receive approved/rejected/revised lifecycle event. |

These are visible in `artemis/marketing/routes/writing_studio.py:39`, `artemis/marketing/routes/writing_studio.py:79`, and `artemis/marketing/routes/writing_studio.py:96`. There is no `GET /overview`, no draft library `GET`, no draft detail `GET`, no update/delete, no versions, no edit history, no training candidates, no Google Docs round-trip routes, and no autosync routes.

The writing rules data is present but mounted elsewhere. `/api/writing-rules/profiles`, `/folders`, `/rules`, `/examples`, and `/sources` exist (`artemis/routes/writing_rules.py:77`, `artemis/routes/writing_rules.py:125`, `artemis/routes/writing_rules.py:184`, `artemis/routes/writing_rules.py:250`, `artemis/routes/writing_rules.py:310`). The Writing Studio frontend does not call those paths; it calls `/api/writing-studio/rules`, `/examples`, and `/sources` (`public/js/core/api.js:951`, `public/js/core/api.js:969`, `public/js/core/api.js:978`).

### C. End-to-End Smoke

Browser: Opening Writing Studio from the Marketing nav displayed "Could not load Writing Studio. Failed to load Writing Studio." This is consistent with `GET /api/writing-studio/overview` returning 404.

Special focus results:

| Focus item | Result |
|---|---|
| Google Docs import/export | Frontend controls/API helpers exist, but backend routes are missing. |
| Autosync preview | Frontend state and API helpers exist for export/import/inspect/reconcile, but backend routes are missing. |
| Rules/examples/sources populated from migration | Data exists in DB counts, but the Writing Studio UI cannot load it through current paths. |
| Active draft persisted across refresh | Frontend restoration logic exists, but cannot be verified because overview and draft detail routes 404. No active draft survives to render. |

### D. Compared To Node Reference

The Node reference has `server/routes/writing-studio.js` and supporting `server/writing-studio-*` modules for draft library, adapter/invoke, Google Docs sync, autosync, events, regeneration, training candidates, and edit history. The Python rebuild currently has only the campaign deliverable stub/event bridge slice. From a user perspective, Writing Studio is not ported yet.

### E. Gaps And Risks

Writing Studio is the highest-risk Marketing gap because it blocks content production and Gate 2 review. The frontend currently fails closed at initial load, so even existing migrated voice assets are unreachable. Backend draft IDs are also conceptually split between local `campaign_deliverables.id` and external draft IDs; the route doc notes `submit-review` expects the local integer ID while events expect an external string ID (`artemis/marketing/routes/writing_studio.py:84`, `artemis/marketing/routes/writing_studio.py:101`). That split needs a stable adapter contract before the UI can safely persist active draft state.

### F. Suggested Divvy

Backend owner: implement a minimal `/api/writing-studio/overview` aggregator that returns drafts, folders, campaigns, rules, examples, sources, profiles, training candidates, and sync config in the shape expected by the frontend. Add draft list/detail/update routes before touching Google Docs.

Frontend owner: either point rules/examples/sources panels at `/api/writing-rules/*` or keep `/api/writing-studio/*` and make backend aggregate there. Add a degraded empty state only after initial overview can return 200.

Integration owner: port Google Docs import/export and autosync after the draft library contract is stable. Treat active-draft restoration as an acceptance test: create/select draft, refresh, same draft remains selected with content.

## 3. Campaigns

### A. Frontend Status

`loadMarketingCampaigns()` renders the campaign workspace from static data, then patches live candidates from `fetchCampaignOpsOverview()` (`public/js/features/marketing-os.js:1858`). The static campaign objects are still large and detailed (`public/js/features/marketing-os.js:46`), while live candidates are merged through `_mergeWithStatic()`. For live-only candidates with no static match, the browser showed a sparse candidate row.

The frontend has campaign actions wired through Node-era helper functions: decision/advance, promote/reopen, writing handoff, brief assembly, brief load, deliverables, content assets, links, rulesets, territory config, and scout runs (`public/js/features/marketing-os.js:7`). Several helpers explicitly call missing or mismatched endpoints. For example, `promoteCampaignCandidateApi()` and `reopenCampaignCandidateApi()` send actions `promote` and `reopen` through `/advance` (`public/js/core/api.js:248`), but Python accepts only approve/reject/monitor/request_changes. `createCampaignWritingHandoffApi()` calls `/writing-handoff`, with a TODO stating it is not ported (`public/js/core/api.js:262`).

### B. Backend Status

Python has:

| Route | Status |
|---|---|
| `GET /api/campaign-ops/candidates` | Works; one live candidate. |
| `GET /api/campaign-ops/candidates/{id}` | Works for numeric candidate IDs. |
| `POST /api/campaign-ops/candidates/{id}/brief/assemble` | Real assembler writes `campaign_briefs`. Not tested because it mutates DB. |
| `POST /api/campaign-ops/candidates/{id}/advance` | Works only for approve/reject/monitor/request_changes. |

The `/advance` implementation updates only `decision_state` and `updated_at` (`artemis/marketing/routes/campaign_ops.py:199`). It does not create a decision event row, derive `workspace_state`, update gate status, create approvals, or move deliverables. The route comment acknowledges missing Node endpoints like `/overview`, `/writing-handoff`, `/decision`, `/promote`, and `/reopen` (`artemis/marketing/routes/campaign_ops.py:9`).

Campaign deliverables are mismatched. Frontend calls `GET /api/campaign-deliverables?campaignId=...` (`public/js/core/api.js:822`); backend supports `GET /api/campaign-deliverables/{candidate_id}` (`artemis/marketing/routes/campaign_deliverables.py:30`). Content assets are similarly partial: frontend calls no-slash asset list, `/archive`, `GET /links?campaignId=...`, and `DELETE /links/{campaignId}/{assetId}` (`public/js/core/api.js:868` through `public/js/core/api.js:914`); backend has trailing-slash list, create/update/get, `POST /links`, and `DELETE /links/{link_id}` (`artemis/marketing/routes/content_assets.py:39`, `artemis/marketing/routes/content_assets.py:82`, `artemis/marketing/routes/content_assets.py:98`).

### C. End-to-End Smoke

Browser: Campaigns opened and showed "All Campaigns 1" with a single `human_gate_1` candidate. Clicking the candidate marked it active but did not render a workspace; the main panel still said "Select a campaign to open its workspace." No write actions were clicked.

Endpoint evidence: `/api/campaign-ops/candidates` returned one candidate with `stage: human_gate_1`, `decisionState: approved`, and `workspaceState: created`. `/api/campaign-deliverables?campaignId=1` returned 404, while `/api/campaign-deliverables/1` returned the stub deliverable.

Special focus results:

| Focus item | Result |
|---|---|
| State machine invoked when transitioning | Not implemented in Python route; `/advance` only sets `decision_state`. |
| Gates render/disable correctly | Demo cards show gates; live candidate workspace did not render, so live gate rendering could not be verified. |
| Marketing ↔ Personal handoff | No real handoff observed. Writing handoff route is missing; Jira bridge may exist in Personal APIs, but Campaigns does not appear to create real Jira tickets from campaign state. |

### D. Compared To Node Reference

Node had richer campaign workspace state derivation, candidate decision history, deliverable state, unified approvals, Writing Studio handoff, and campaign workspace gate wiring. The Python rebuild has the beginning of storage and read/write candidates, but not the same campaign state machine or cross-surface handoff. The Node build spec described campaign state machine and deliverable state machine as validated or partial; Python is behind that behavior.

### E. Gaps And Risks

The live candidate shape is not enough for the current workspace renderer. A sparse live candidate can appear in the list but fail to open an actionable workspace. The allowed backend actions are also incompatible with frontend promote/reopen actions, so some UI buttons will 400 if clicked. Because `/advance` does not own a real state machine, a candidate can become `approved` while remaining in a stale or inconsistent workspace/gate state.

### F. Suggested Divvy

Backend owner: port the Node campaign candidate state machine as a pure service first, then make `/advance` call it. Persist decision events append-only. Add route compatibility for `/writing-handoff`, deliverable list by `campaignId`, and content asset link list/delete contracts or update the frontend to match Python.

Frontend owner: fix live-candidate workspace rendering before adding more actions. Disable or hide promote/reopen/writing-handoff controls unless the backing route exists. Add explicit live/demo provenance in each campaign tab.

Integration owner: define Marketing-to-Personal handoff acceptance criteria: campaign approval creates either a real Jira issue, a durable stub record with visible status, or a deliberate "handoff unavailable" state. Do not leave it implied.

## 4. Signals Inbox

### A. Frontend Status

`loadMarketingSignals()` renders mock signals immediately, then attempts `listSignalQueueApi({ status: 'in_inbox' })` (`public/js/features/marketing-os.js:1900`). Because `listSignalQueueApi()` calls `/api/signal-queue?...` with no trailing slash (`public/js/core/api.js:2130`), the live list fails and the demo skeleton remains.

The add-signal form opens locally. Submitting it calls `createSignalApi()` (`public/js/features/marketing-os.js:1954`), which posts to `/api/signal-queue` (`public/js/core/api.js:2144`). Python does not expose that route; it exposes `/api/signal-queue/intake`.

Approve, reject, snooze, and qualify helpers mostly map to Python routes once a real signal is rendered, but the page never reaches real signal render because of the list mismatch. Archive helper calls `/archive`, while Python names the archive-like route `/ask`.

### B. Backend Status

Python signal backend is one of the stronger Marketing slices. It supports structured intake, trailing-slash list, single signal get, qualify, approve, reject, snooze, and ask/archive (`artemis/marketing/routes/signal_queue.py:60`, `artemis/marketing/routes/signal_queue.py:164`, `artemis/marketing/routes/signal_queue.py:191`, `artemis/marketing/routes/signal_queue.py:207`, `artemis/marketing/routes/signal_queue.py:236`, `artemis/marketing/routes/signal_queue.py:311`, `artemis/marketing/routes/signal_queue.py:342`, `artemis/marketing/routes/signal_queue.py:382`).

However, there are no active rulesets. Qualification therefore returns `no_active_rulesets` (`artemis/marketing/routes/signal_queue.py:222`) unless rulesets are seeded. The one live signal is visible through `/api/signal-queue/?status=in_inbox`, but not through the frontend path.

### C. End-to-End Smoke

Browser: Signals Inbox opened with "Demo data" and three mock signals. The "Add Signal" button opened the form; submit was not clicked because it would be a write action and the audit brief forbids DB-changing actions.

Endpoint evidence: `/api/signal-queue?status=in_inbox` returned 404; `/api/signal-queue/?status=in_inbox` returned 200 with one live signal.

### D. Compared To Node Reference

Node had signal queue routes aligned with the frontend and tied into deterministic qualification, active rulesets, candidate promotion, and campaign creation. Python has many of those backend pieces, but the frontend adapter mismatch prevents the UI from seeing them. Ruleset seeding is also absent, so qualification cannot yet behave like the Node reference.

### E. Gaps And Risks

The UI currently teaches the user to interact with demo signals while a real signal exists in the DB. Manual signal submission is wired to the wrong route. If the list path is fixed, qualification may still fail due to zero active rulesets, so the next visible issue will be scoring readiness rather than list loading.

### F. Suggested Divvy

Backend owner: add route aliases for no-slash list and `POST /api/signal-queue` as a compatibility wrapper over `/intake`, or intentionally update frontend helpers to use `/intake` and trailing slash.

Data owner: seed at least one active ruleset and minimal territory config so `qualify` has a real happy path.

Frontend owner: when live list fails, show "live unavailable" rather than "No real signals yet." That copy is currently misleading when the API contract is broken.

## 5. Approval Queue

### A. Frontend Status

`loadMarketingApprovals()` starts with an empty skeleton, calls `listApprovalsApi({ status: 'pending' })`, and falls back to static demo approvals if the call fails or returns an empty list (`public/js/features/marketing-os.js:2082`). `listApprovalsApi()` calls `/api/approvals?...` with no trailing slash (`public/js/core/api.js:1973`), so the live call fails against Python.

When live approvals do exist, the frontend can render a simple unified approval card and call `decideApprovalApi()` with `decision: approve` or `decision: reject` (`public/js/features/marketing-os.js:2111`). The API helper warns that Python approvals do not resume automation or workflow runs (`public/js/core/api.js:1984`).

### B. Backend Status

Python supports trailing-slash list, get, and decision routes (`artemis/marketing/routes/approvals.py:36`, `artemis/marketing/routes/approvals.py:54`, `artemis/marketing/routes/approvals.py:66`). The schema is intentionally simpler than Node unified approvals and returns `targetType`, `approvalKind`, and `payload` as null (`artemis/marketing/routes/approvals.py:8`, `artemis/marketing/routes/approvals.py:118`).

There are currently zero approval rows. No live approval creation was exercised because the audit brief forbids DB-changing actions. Writing Studio submit-review and campaign gates appear to be the likely producers, but Writing Studio is not loadable and campaign gates are not active end-to-end.

### C. End-to-End Smoke

Browser: Approval Queue opened with three disabled demo cards: Florida OBC Gate 1, Florida OBC Gate 2, and Maryland expert validation. No live approval row rendered.

Endpoint evidence: `/api/approvals?status=pending` returned 404; `/api/approvals/?status=pending` returned 200 with `[]`.

### D. Compared To Node Reference

Node's approval route was a unified approval layer with target type, approval kind, payload, and side effects such as automation/workflow continuation and Writing Studio Gate 2 handling. Python records a simple approve/reject decision on a pending approval and stops there. It is useful as a storage slice but not a complete approval queue workflow.

### E. Gaps And Risks

Approvals are currently not a reliable gate mechanism for Marketing. The frontend cannot list them through its current helper, the DB has none, the schema lacks rich routing fields, and decisions do not trigger the downstream state changes that make approvals operational.

### F. Suggested Divvy

Backend owner: decide whether Python should recreate Node `unified_approvals` semantics or keep the simpler table and add a separate approval orchestration service. Whichever path wins, add route compatibility for no-slash list.

Workflow owner: wire approval creation from campaign Gate 1 and Writing Studio Gate 2, then make approval decisions resume the relevant campaign/deliverable state machine.

Frontend owner: render empty live approval state distinctly from demo fallback. Keep demo cards in a separate labeled reference section if useful.

## Cross-Cutting Gaps

### API Slash Compatibility

Several Python routers use `@router.get("/")`, while frontend helpers call the prefix without a trailing slash. This causes direct 404s for signals, approvals, and content assets. Fixing this would immediately expose more live data without changing business logic.

### Contract Drift

Frontend helpers still encode Node-era contracts. Some have TODOs noting Python gaps, but the UI still imports and calls them. The most visible examples are Writing Studio overview/drafts, campaign writing handoff, campaign deliverables query shape, content asset links, content archive, signal create/archive, and approval side effects.

### Demo Fallback Masking

Dashboard, Signals, Campaigns, and Approvals all render static data before or after failed API calls. This is useful during scaffold, but it currently hides broken live paths. For auditability, each metric/card/list should expose whether it is live, demo, empty, or unavailable.

### Data Seeding

The database has minimal Marketing rows. The live candidate/signal/deliverable path exists, and writing profile/rule/example/source data exists, but approvals, rulesets, territory config, scout runs, content assets, and campaign briefs are empty. That limits meaningful smoke coverage.

## Recommended Implementation Order

1. Fix no-slash/trailing-slash compatibility for Marketing list endpoints. This is low-risk and will unblock live Signals, Approvals, and Content Assets reads.
2. Implement `/api/writing-studio/overview` as a read-only aggregator over existing writing and campaign tables. This turns Writing Studio from hard-fail to inspectable.
3. Align Writing Studio draft list/detail/update contracts before Google Docs or autosync. Active draft persistence should be the acceptance test.
4. Port the campaign state machine as a backend service and make `/advance`, Gate 1 approvals, deliverable state, and Gate 2 approvals call it.
5. Decide and implement Marketing-to-Personal handoff semantics: real Jira ticket, durable local stub, or explicit unavailable state.
6. Add demo/live provenance in the UI so broken API paths cannot masquerade as intentional empty data.

## Divvy Summary

Backend:
- Route compatibility aliases for signals, approvals, content assets, and campaign deliverables.
- Writing Studio overview/draft contracts.
- Campaign state machine and approval side effects.
- Ruleset/territory seed path for qualification.

Frontend:
- Update API helpers to match Python or rely on backend aliases consistently.
- Replace silent demo fallback with source-aware states.
- Fix live campaign workspace selection/rendering.
- Gate or hide controls that call missing endpoints.

QA:
- Add browser smoke tests for all five Marketing surfaces.
- Add endpoint contract tests for no-slash and trailing-slash paths.
- Add a Writing Studio persistence test: select active draft, refresh, verify same draft and content.
- Add campaign state-machine tests: approve/reject/monitor/request_changes update decision log, workspace state, gates, and approval creation without deleting history.
