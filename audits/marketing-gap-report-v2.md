# Marketing Section Gap Report v2

Generated: 2026-05-20  
Auditor: Codex  
Scope: Python rebuild at `/Users/artemis/Desktop/Artemis/artemis-os`, compared against the frozen Node reference at `/Users/artemis/Desktop/Artemis/claudeck-artemis`.  
Mode: audit/report refresh. No application code was changed.

## Executive Summary

Marketing is healthier than the original audit in one important way: the generic no-slash list-route problem has been fixed for `signal-queue`, `approvals`, and `content-assets`, so Dashboard, Signals, and Approval Queue are no longer doomed by transport-level 404s. Slack signals, Daily Brief, Meetings, and the M1 raw-input provenance work also make the surrounding operating context richer.

The major Marketing blockers remain Writing Studio and campaign workspace parity. The full Node-era Writing Studio frontend still calls `/api/writing-studio/overview`, draft list/detail, Google Docs import/export, autosync, folders, versions, edit history, training candidates, rules/examples/sources, and seed import. Python still exposes only the C4 bridge: create a draft from a campaign candidate, submit it for Gate 2 review, and receive lifecycle events. That is useful for integration plumbing, but it does not load the Writing Studio application.

Campaigns are also still partial. Candidate list/read, brief assembly, and a narrow `/advance` route exist, and the Writing Studio adapter can recompute workspace state from deliverable events. But the frontend still calls mismatched deliverable/content-asset link contracts, promote/reopen/writing-handoff helpers do not map to implemented Python routes, and `/advance` does not yet own a full campaign state machine or append-only decision log.

## Evidence Snapshot

| Endpoint / area | Current state |
|---|---|
| `/api/signal-queue` | Now has no-slash and trailing-slash list compatibility. |
| `/api/approvals` | Now has no-slash and trailing-slash list compatibility. |
| `/api/content-assets` | Now has no-slash and trailing-slash list compatibility. |
| `/api/writing-studio/overview` | Still not implemented; `public/js/core/api.js` still carries a TODO saying it is not ported. |
| `/api/writing-studio/drafts` | `POST` exists for candidate draft creation; `GET` list/detail/update/delete and most draft operations are missing. |
| `/api/campaign-deliverables?campaignId=...` | Frontend still calls query shape; backend supports `GET /api/campaign-deliverables/{candidate_id}` only. |
| `/api/content-assets/links?campaignId=...` | Frontend still calls query shape; backend supports `POST /links` and `DELETE /links/{link_id}` only. |
| `/api/signal-queue` manual create | Frontend `createSignalApi()` still posts to `/api/signal-queue`; backend create is `/api/signal-queue/intake`. |
| `/api/signal-queue/{id}/archive` | Frontend calls `/archive`; backend archive-like route is `/ask`. |
| Slack signals | Native Python `/api/slack/signals` and `/mentions` routes exist; focused tests mostly pass, with one permalink expectation mismatch. |
| Daily Brief | Native Python `/api/daily-brief` snapshot/generate/history exists. |
| M1 raw inputs | Meeting summarizer writes raw input provenance and hash-chain-backed memory foundation exists. |

## 1. Dashboard

### A. Frontend Status

`loadMarketingDashboard()` still renders a static dashboard and then patches live data from `fetchCampaignOpsOverview()`, `listApprovalsApi()`, and `listSignalQueueApi()`.

The earlier transport issue is fixed on the backend side: `listApprovalsApi({ status: 'pending' })` and `listSignalQueueApi({ status: 'in_inbox' })` now target no-slash URLs that Python accepts. The dashboard therefore has a real chance to show live pending-approval and signal counts instead of silently falling back because of 404s.

However, it still swallows failures and can show static/demo values when a deeper contract is missing or data is empty.

### B. Backend Status

The backend has real route coverage for candidate overview (`/api/campaign-ops/candidates`), signal list, approval list, rulesets, scout runs, and content assets. Signals and approvals now include no-slash aliases. Daily Brief and Slack signals add adjacent live context that did not exist in the original audit.

The status endpoint marks `campaign-ops`, `campaign-deliverables`, `content-assets`, `approvals`, `signal-queue`, `signal-criteria`, and `writing-studio` as available. That is directionally true for route slices, but too optimistic for full Writing Studio UI readiness.

### C. End-to-End Smoke

No browser smoke was run for this refresh. The audit used code inspection plus focused tests. The focused test batch covered trailing-slash compatibility, Agents parity, Daily Brief, Slack signals/triage, and token refresh. It produced 104 passed / 2 failed, with failures in Slack test expectations and a missing migration-test cwd.

### D. Compared To Node Reference

Node's dashboard reflected a more unified live Marketing workflow: candidates, approvals, signal queue, scouts/rulesets, and workspace status were all part of a validated MVP path. Python now has more of the read plumbing aligned, but the dashboard can still overstate completeness because Writing Studio and campaign state remain partial.

### E. Gaps And Risks

The dashboard's biggest risk is source ambiguity. A visible count may be live, empty, demo, or failed-and-swallowed. After J10, the old "404 because slash" explanation is mostly gone; any fallback now points to either missing data, missing business behavior, or a deeper frontend/backend contract mismatch.

### F. Suggested Divvy

Frontend owner: add metric-level provenance for campaign count, signal count, and approval count.

Backend owner: keep no-slash parity guarded for all new Marketing list routes.

QA owner: browser smoke Dashboard with known seeded counts and assert each metric's provenance.

## 2. Writing Studio

### A. Frontend Status

The frontend remains the full Node-era Writing Studio application in `public/js/features/writing-studio.js`. Its first critical call is still:

- `GET /api/writing-studio/overview`

Then it expects draft list/detail restoration and a large operational surface: compose/prompt/invoke, Google Docs import/export/unlink, autosync export/import/inspect/reconcile, folders, links, update/delete, submit review, regenerate, edit history, versions, training candidates, rules, examples, sources, and seed import.

`public/js/core/api.js` still explicitly notes that `/writing-studio/overview` is not yet ported.

### B. Backend Status

`artemis/marketing/routes/writing_studio.py` currently exposes only:

- `POST /api/writing-studio/drafts`
- `POST /api/writing-studio/drafts/{draft_id}/submit-review`
- `POST /api/writing-studio/drafts/{draft_id}/events/{event_kind}`

The supporting C4 modules are real and useful:

- `external.py` provides stub and real external Writing Studio clients.
- `invoke.py` creates local campaign deliverables and approval records.
- `adapter.py` listens for draft lifecycle events and updates deliverable/workspace state.
- `events.py` provides in-process pub/sub.

But there is still no overview aggregator, no draft list/detail/update/delete, no Google Docs routes, no autosync routes, no versions/edit history, and no Writing Studio-mounted rules/examples/sources routes.

### C. End-to-End Smoke

The old smoke result, "Could not load Writing Studio," is still the expected browser outcome because the initial overview route is missing. C4 route tests verify the bridge path, not the full Writing Studio UI.

Special focus:

| Focus item | Current result |
|---|---|
| Google Docs import/export | Frontend helpers exist; backend routes missing. |
| Autosync preview | Frontend helpers exist; backend routes missing. |
| Rules/examples/sources migration data | Backend exists under `/api/writing-rules/*`; Writing Studio frontend calls `/api/writing-studio/*`. |
| Active draft persistence | Frontend logic exists; cannot work until overview and draft detail exist. |
| Gate 2 bridge | Python route tests cover create draft, submit review, events, and adapter state transitions. |

### D. Compared To Node Reference

Node's Writing Studio was an application. Python's current slice is an integration bridge. It can create a deliverable-backed draft stub and process lifecycle events, but it cannot render the Writing Studio workspace or manage the draft library.

### E. Gaps And Risks

This remains the highest-risk Marketing gap. The C4 bridge can make parts of campaign handoff testable, but the user-facing Writing Studio is still blocked at first load.

There is also an ID-contract risk: submit-review expects a local integer deliverable ID, while event routes accept an external draft ID string. The adapter updates deliverables using event `deliverable_id`, so any real external Writing Studio integration must preserve that mapping carefully.

### F. Suggested Divvy

Backend owner: implement `GET /api/writing-studio/overview` as a read-only aggregator first. Include drafts, folders, campaigns, rules, examples, sources, profiles, training candidates, and sync config in the shape the frontend already expects.

Backend follow-up: add draft list/detail/update routes before Google Docs/autosync.

Frontend owner: either retarget rules/examples/sources to `/api/writing-rules/*` or keep the `/api/writing-studio/*` facade and make backend aggregate there. Do not split the UI across both without a stable adapter.

Acceptance test: select/create a draft, refresh, and verify the same draft content is active.

## 3. Campaigns

### A. Frontend Status

`loadMarketingCampaigns()` still merges live candidates with static campaign objects. It wires actions for decisions, promote/reopen, Writing Studio bridge, brief assembly, deliverables, content assets, asset links, rulesets, territory config, and scout runs.

Several helpers still call Node-era or mismatched routes:

- `listCampaignDeliverablesApi(campaign.id)` calls `/api/campaign-deliverables?campaignId=...`
- backend expects `/api/campaign-deliverables/{candidate_id}`
- `listCampaignAssetLinksApi(campaign.id)` calls `/api/content-assets/links?campaignId=...`
- backend does not implement link-list-by-campaign
- `deleteCampaignAssetLinkApi(campaignId, assetId)` calls `/links/{campaignId}/{assetId}`
- backend deletes by link row ID
- promote/reopen still route through `/advance` actions Python does not accept
- Writing handoff is only partially represented via deliverable/draft creation, not the full Node contract

### B. Backend Status

Python campaign backend has:

- `GET /api/campaign-ops/candidates`
- `GET /api/campaign-ops/candidates/{id}`
- `POST /api/campaign-ops/candidates/{id}/brief/assemble`
- `POST /api/campaign-ops/candidates/{id}/advance`
- `GET /api/campaign-deliverables/{candidate_id}`
- `POST /api/campaign-deliverables/`
- `POST /api/campaign-deliverables/{id}/submit-review`

Brief assembly is real. The Writing Studio adapter can recompute `workspace_state` from deliverable statuses. But `/advance` still only maps approve/reject/monitor/request_changes to `decision_state`; it does not run a full campaign state machine, append decision events, create approvals, or coordinate deliverable gate state.

### C. End-to-End Smoke

No browser smoke was run in this refresh. Based on current code, live candidates should list more reliably than before, but workspace subpanels that depend on deliverables or asset links can still fail because the query contracts do not match.

Special focus:

| Focus item | Current result |
|---|---|
| State machine invoked when transitioning | Still not implemented in `/advance`; only decision_state changes. |
| Gates render/disable correctly | Static/demo rendering exists; live gate behavior remains unverified and partially unsupported. |
| Marketing ↔ Personal handoff | Still not a complete route-backed flow. |
| Writing Studio handoff | C4 bridge can create a draft from candidate; full handoff UI remains blocked by Writing Studio overview. |

### D. Compared To Node Reference

Node's Marketing MVP had a validated path: scout intake, signal queue, qualifier, approve, candidate, brief assembly, asset link, Writing Studio draft, Gate 2 approval, deliverable state. Python has many pieces of that path, but not the full same contract at the UI boundary.

The key improvement since v1 is not campaign state itself; it is that adjacent route compatibility and C4 deliverable event handling are better.

### E. Gaps And Risks

The live campaign workspace can still become a half-real surface: list data is live, but subpanels and actions may be static, failing, or partial. The biggest correctness risk is state drift: a candidate can be approved while its workspace/gates/deliverables do not move through a single state machine.

### F. Suggested Divvy

Backend owner: port the Node campaign state machine as a pure service, make `/advance` call it, and persist append-only decision events.

Compatibility owner: add deliverable and content-asset link route aliases for the frontend's query shapes, or update `api.js` consistently.

Integration owner: define an explicit Marketing-to-Personal handoff contract: real Jira issue, durable local stub, or visible unavailable state.

## 4. Signals Inbox

### A. Frontend Status

`loadMarketingSignals()` still renders mock signals first, then calls `listSignalQueueApi({ status: 'in_inbox' })`. Because Python now accepts `/api/signal-queue` without a trailing slash, real signals can render when present.

The manual Add Signal form still calls `createSignalApi()`, which posts to `/api/signal-queue`. Python's real creation seam remains `/api/signal-queue/intake`. The frontend also calls `archiveSignalApi()` against `/api/signal-queue/{id}/archive`, while Python names the archive-like route `/ask`.

### B. Backend Status

The Signal Queue backend remains one of the stronger Marketing slices:

- intake with dry-run and duplicate checking
- no-slash/trailing-slash list
- get signal
- qualify
- approve/promote to candidate
- reject
- snooze
- ask/archive

It runs deterministic qualification when active rulesets exist. If no active rulesets are seeded, qualify still returns `no_active_rulesets`.

### C. End-to-End Smoke

No new browser smoke was run. Code inspection says the initial list should no longer fall back solely due to slash mismatch. Add Signal and Archive remain mismatched.

### D. Compared To Node Reference

Node aligned the frontend and backend route names more fully. Python has the core signal mechanics but still leaves two visible frontend actions pointed at the wrong routes.

### E. Gaps And Risks

The most likely new user-visible issue after J10 is that the list works but manual add/archive fails. Also, qualification still depends on seeded active rulesets and territory config; without them, live signals can be present but not scoreable.

### F. Suggested Divvy

Backend owner: add `POST /api/signal-queue` as a compatibility wrapper over `/intake` and `POST /api/signal-queue/{id}/archive` as an alias for `/ask`, or update the frontend helpers to use existing Python route names.

Data owner: seed at least one active ruleset and minimal territory config.

Frontend owner: keep demo signals clearly labeled and do not replace a live-empty state with mock data unless it is explicitly a demo section.

## 5. Approval Queue

### A. Frontend Status

`loadMarketingApprovals()` calls `listApprovalsApi({ status: 'pending' })`. That helper still targets `/api/approvals?...`, but Python now accepts the no-slash list path. When no live approvals exist, the UI still falls back to static demo approval cards.

Approval decisions call `/api/approvals/{id}/decision`, which Python implements.

### B. Backend Status

`artemis/marketing/routes/approvals.py` supports:

- `GET /api/approvals`
- `GET /api/approvals/`
- `GET /api/approvals/{id}`
- `POST /api/approvals/{id}/decision`

The schema is still simpler than Node unified approvals. It returns `targetType`, `approvalKind`, and `payload` as null because those fields are not stored.

Approvals can be created by the Writing Studio C4 submit-review path, but full campaign/automation approval orchestration is not present.

### C. End-to-End Smoke

No browser smoke was run. Route inspection and slash tests indicate list compatibility is fixed. A live approval queue still depends on a producer creating rows.

### D. Compared To Node Reference

Node's approval layer carried target type, approval kind, payload, and downstream side effects. Python is a basic approval record and decision endpoint. It is useful storage, but not yet a full gate orchestration layer.

### E. Gaps And Risks

The Approval Queue can now list live rows, but the rows do not carry enough typed context to drive all Node-style workflows. Decisions do not resume automations, run campaign state transitions, or coordinate Writing Studio beyond the C4-specific bridge.

### F. Suggested Divvy

Backend owner: decide whether to enrich the existing `approvals` table or add an approval orchestration service on top.

Workflow owner: wire approval producers and consumers for campaign Gate 1, Writing Studio Gate 2, and future Automations.

Frontend owner: render live-empty separately from demo approvals.

## 6. Scout Rulesets And Scout Runs

### A. Frontend Status

Marketing includes ruleset/territory/scout-run panels in `marketing-os.js`. They call `listCampaignRulesetsApi()`, ruleset version endpoints, territory endpoints, and scout run/package endpoints.

### B. Backend Status

`artemis/marketing/routes/signal_criteria.py` and `scouts.py` exist. Signal qualification reads active rulesets and territory config. Scout runs are available as a manual harness/read-only debug surface.

### C. End-to-End Smoke

No live scout smoke was run. The original report's DB note said rulesets and territory were empty; this refresh did not mutate or seed data.

### D. Compared To Node Reference

Node had the Marketing MVP smoke path validated with synthetic findings. Python has the route mechanics and deterministic qualifier, but the production scout ecosystem remains a later phase unless seeded and exercised.

### E. Gaps And Risks

Qualification readiness depends on active rulesets. Without seed data, the backend can be correct but the product appears inert.

### F. Suggested Divvy

Data owner: seed canonical rulesets/territory configs.

QA owner: run a synthetic scout intake path: dry-run, commit, qualify, approve, candidate, brief.

## Cross-Cutting Gaps

### API Slash Compatibility

This is mostly resolved for the list endpoints that mattered in the original Marketing audit. Keep `tests/test_trailing_slash_compat.py` updated when new single-segment `/api/<resource>/` list routes are added.

### Contract Drift

The remaining drift is no longer primarily slash-related. It is route-name and shape drift:

- Writing Studio overview/draft library missing.
- Campaign deliverables query shape mismatch.
- Content asset link list/delete mismatch.
- Signal create/archive mismatch.
- Campaign promote/reopen actions unsupported.
- Approval side effects missing.

### Demo Fallback Masking

Dashboard, Signals, Campaigns, and Approvals still use static data as a fallback. After J10, fallback means something more specific than "slash bug": empty live data, missing producer, missing route, or real failure. The UI should make that distinction.

### Data Seeding

Rulesets, territory config, approvals, scout runs, content assets, and campaign briefs need known-good seed/smoke data to make live QA meaningful. The codebase now has more route coverage than the live product may reveal with an empty DB.

## Recommended Implementation Order

1. Implement `GET /api/writing-studio/overview` and draft list/detail. This is still the highest-leverage Marketing unblock.
2. Add route compatibility for campaign deliverables and content asset links, or update frontend helpers to match Python.
3. Add Signal Queue compatibility aliases for manual create and archive.
4. Port the campaign state machine and decision log, then route `/advance` through it.
5. Enrich approval orchestration so decisions resume the appropriate campaign/deliverable/automation flow.
6. Seed rulesets/territory and run the synthetic Marketing smoke path end-to-end.
7. Add provenance labels so demo/live/unavailable states cannot blur together.
