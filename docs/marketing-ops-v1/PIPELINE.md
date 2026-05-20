# Artemis OS — Pipeline & Handoff Contracts

This document describes the end-to-end flow of Artemis OS v1. Every handoff between teams has a defined contract (schema). Build to the contract, not to the upstream agent.

## End-to-end flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SCOUT TEAM — DETECT                                │
│                                                                             │
│  Territory Config (shared) ──► All 9 scouts read from it                    │
│                                                                             │
│  1.1 Starbridge Researcher      1.4 Legislative Scout                       │
│  1.2 Regional News Scout        1.5 Federal Funding Scout                   │
│  1.3 LinkedIn Observer (Mode B) 1.6 State DoE Scout                         │
│                                 1.7 Procurement Scout                       │
│                                 1.8 Board Minutes Scout                     │
│                                 1.9 Leadership Transition Scout             │
│                                                                             │
│  All 9 emit ──► signal_queue (PostgreSQL, append-only)                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                          Contract: Signal Schema (schemas/signal.md)
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       QUALIFIER TEAM — QUALIFY                              │
│                                                                             │
│  2.1 Cross-Reference Agent                                                  │
│    Phase 1: hard filters (deterministic, DB queries only)                   │
│    Phase 2: score against ALL rulesets (LLM per qualitative rubric)         │
│    Phase 3: route to top campaign type(s) (deterministic)                   │
│                                                                             │
│  2.2 Ruleset Manager Agent (operates via Josh's chat panel)                 │
│  2.3 Ruleset Compiler (deterministic, converts YAML → runtime objects)      │
│  2.4 Brief Composer Agent (signal → Josh-readable inbox card)               │
│                                                                             │
│  Output ──► signal_briefs table                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                          Contract: Signal Brief (schemas/signal-brief.md)
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       GATE 1 — SIGNALS INBOX                                │
│                                                                             │
│  Human surface. Josh / Angela review briefs and choose:                     │
│    Approve  ──► create campaign_workspace, trigger Content team             │
│    Reject   ──► reason routes back as training signal                       │
│    Snooze   ──► re-surface in N days                                        │
│    Ask      ──► opens chat thread with originating scout / ruleset manager  │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                          Contract: Campaign Workspace (schemas/campaign-workspace.md)
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CONTENT TEAM — USE ASSETS                              │
│                                                                             │
│  5.1 Campaign Brief Assembler (deterministic, builds immutable brief)       │
│  5.2 Asset Selector Agent (LLM, picks ONE asset bundle for whole campaign)  │
│  5.3 Writing Studio Adapter (deterministic, POST /drafts per deliverable)   │
│                                                                             │
│  Output ──► Writing Studio API (POST /drafts)                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                          Contract: Writing Studio Draft Payload
                                  (schemas/writing-studio-draft.md)
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│             EXTERNAL — WRITING STUDIO (NOT BUILT BY ARTEMIS)                │
│                                                                             │
│  Writing Studio drafts the deliverable (email / social / long-form /        │
│  landing page) and routes to Approval Drawer for human review.              │
│                                                                             │
│  Owned by: Angela / Julia / Olivia                                          │
│  Artemis OS responsibility ends here.                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Handoff contracts (the seams Codex needs to honor)

### Scout → Qualifier

- **Artifact:** Signal object (see `schemas/signal.md`)
- **Storage:** `signal_queue` table, status = `pending_qualification`
- **Trigger:** Scout writes to queue; Qualifier polls every 5 minutes for `pending_qualification` rows
- **Failure mode:** Scout writes invalid signal → DB constraint rejection → scout logs error and continues

### Qualifier Phase 1 → Phase 2

- **Internal to Cross-Reference Agent (2.1).** Not a cross-team handoff.
- Phase 1 outputs `passed_hard_filters: bool`. If False, signal is marked `rejected_hard_filter` in queue and no LLM cost is incurred.

### Qualifier Phase 2 → Phase 3

- **Internal to Cross-Reference Agent (2.1).** Not a cross-team handoff.
- Phase 2 outputs `ruleset_scores: list[{ruleset_id, fit_score, evidence}]`. Phase 3 reads this list.

### Qualifier → Brief Composer

- **Artifact:** Cross-Reference output (transient, not stored as own row — included in `signal_briefs` row)
- **Trigger:** Phase 3 completes → 2.4 Brief Composer runs immediately on the result

### Brief Composer → Signals Inbox

- **Artifact:** Signal Brief (see `schemas/signal-brief.md`)
- **Storage:** `signal_briefs` table, status = `pending_human_review`
- **Trigger:** Brief Composer writes row; Signals Inbox UI reads pending rows

### Signals Inbox → Content team

- **Artifact:** Campaign Workspace (see `schemas/campaign-workspace.md`)
- **Storage:** `campaign_workspaces` table, status = `pending_content`
- **Trigger:** Josh / Angela clicks "Approve" → workspace row created → 5.1 Campaign Brief Assembler picks it up

### Content team → Writing Studio

- **Artifact:** Writing Studio Draft Payload (see `schemas/writing-studio-draft.md`)
- **Transport:** HTTPS POST to Writing Studio API (URL in `.env`)
- **Trigger:** 5.3 Writing Studio Adapter runs once per deliverable. Returns `draft_id` from Writing Studio.
- **Failure mode:** Writing Studio unreachable → exponential backoff, max 5 retries, alert on final failure

### Writing Studio → ??? (out of scope for v1)

- Writing Studio has its own approval workflow (the Approval Drawer in the canvas).
- For v1, Artemis OS has **no read-back** from Writing Studio. We send drafts and stop.
- Future v2: webhook from Writing Studio back to Artemis on approval / rejection / send. Out of scope now.

## Status lifecycle for a signal (end-to-end)

```
pending_qualification           Scout has written; Qualifier hasn't picked up yet
   ↓
rejected_hard_filter            Phase 1 failed (e.g., not in priority state)
   OR
in_qualification                Phase 2 running (LLM calls in flight)
   ↓
rejected_low_fit                No ruleset scored above 0.4
   OR
qualified                       At least one ruleset scored above 0.7
   ↓
brief_composed                  2.4 wrote a signal_brief row
   ↓
pending_human_review            Signal is in Signals Inbox
   ↓
rejected_by_human               Josh / Angela rejected with reason
   OR
snoozed                         Re-surface at snooze_until timestamp
   OR
approved                        Campaign workspace created
   ↓
in_content_preparation          5.1 / 5.2 / 5.3 running
   ↓
sent_to_writing_studio          5.3 successfully POSTed all drafts
   OR
content_preparation_failed      5.1 or 5.2 or 5.3 errored; alert raised
```

Every signal terminates in one of: `rejected_hard_filter`, `rejected_low_fit`, `rejected_by_human`, `snoozed` (transient), `sent_to_writing_studio`, `content_preparation_failed`.

## Shared infrastructure (used by all teams)

These services are not part of any team; every team uses them. See `services/`:

- **Signal Queue** (`services/signal-queue.md`) — PostgreSQL-backed queue.
- **Memory Layer** (`services/memory-layer.md`) — dedupe memory; embedding hash + timestamp per `(district, reason_code)`.
- **Ruleset Storage** (`services/ruleset-storage.md`) — append-only versioned storage for rulesets.
- **Contact DB Stub** (`services/contact-db-stub.md`) — placeholder, returns True for priority districts.
- **Territory Config** (`services/territory-config.md`) — priority states, watch keywords, deprioritized lists.
- **PDF Extractor** (`services/pdf-extractor.md`) — shared by scouts 1.2, 1.6, 1.7, 1.8.

## Cadence summary (when each agent runs)

| Agent | Cadence | Notes |
|---|---|---|
| 1.1 Starbridge Researcher | Every 4h | Throttled credit usage during bench-test |
| 1.2 Regional News Scout | Daily | High-priority sources event-driven |
| 1.3 LinkedIn Observer (Mode B) | Event-driven (post-detection) | Mode A disabled in v1 |
| 1.4 Legislative Scout | Daily (in session), weekly (off-session) | Chamber votes trigger immediate |
| 1.5 Federal Funding Scout | Daily | Federal sources predictable |
| 1.6 State DoE Scout | Daily | Per priority state |
| 1.7 Procurement Scout | Twice-daily (statewide), daily (watch list) | |
| 1.8 Board Minutes Scout | Weekly per watch-list district | Daily for transition-active |
| 1.9 Leadership Transition Scout | Weekly | Daily for transition-active |
| 2.1 Cross-Reference Agent | Polls every 5 min for pending signals | |
| 2.2 Ruleset Manager Agent | On-demand (Josh's chat sessions) | |
| 2.3 Ruleset Compiler | On ruleset write (event-driven) | |
| 2.4 Brief Composer Agent | Runs immediately on Phase 3 completion | |
| 5.1 Campaign Brief Assembler | On workspace creation (event-driven) | |
| 5.2 Asset Selector Agent | After 5.1 completes | |
| 5.3 Writing Studio Adapter | After 5.2 completes | One call per deliverable |
