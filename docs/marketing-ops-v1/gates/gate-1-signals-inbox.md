# Gate 1 — Signals Inbox

The human review surface where Josh and Angela approve / reject / snooze / ask on every signal before it becomes a campaign.

**Owner:** Inside Artemis OS (we build this).

## Purpose

Bridge automated qualification (Cross-Reference Agent + Brief Composer) and automated content preparation (Content team). Nothing reaches Writing Studio without explicit human approval here.

## What Gate 1 displays

A queue of Signal Brief cards. Each card shows the full output of 2.4 Brief Composer (see `schemas/signal-brief.md`):

- Headline (≤ 80 chars)
- Why flagged (1-2 sentences)
- Evidence (verbatim from source)
- Fit scores (primary + secondaries)
- Suggested campaign
- Related history (max 3 bullets)
- Urgency tier + deadline if applicable
- Source link (click-through to original)

## Four actions on every card

| Action | DB effect |
|---|---|
| **Approve** | Creates `campaign_workspaces` row with status `pending_content`. Triggers Content team. Updates `signal_briefs.status = approved`. |
| **Reject** | Updates `signal_briefs.status = rejected_by_human` with `rejected_reason`. No workspace created. Rejection reason feeds back to Ruleset Manager Agent for hit-rate analysis. |
| **Snooze** | Updates `signal_briefs.status = snoozed`, sets `snooze_until`. A background job re-surfaces the brief when timer expires. |
| **Ask** | Opens a chat thread with originating scout OR with Ruleset Manager Agent for clarification. v1: simple text-based comment field; full chat threading is v2. |

## Rejection reasons (structured dropdown + free text)

When rejecting, the user picks from a structured list. This makes rejection data analyzable:

- `not_a_real_signal` — Brief Composer hallucinated or misread evidence
- `wrong_district` — entity resolution failed
- `geographic_mismatch` — district outside our target territory despite passing filters
- `wrong_campaign_type` — campaign suggestion doesn't match what evidence supports
- `timing_off` — signal is real but too early or too late to act on
- `already_in_pipeline` — district already has an active campaign / open opportunity
- `low_quality_evidence` — source isn't credible or evidence is too thin
- `other` — free text required

The structured list lets Ruleset Manager Agent later analyze "why are we rejecting OBC signals?" — much more useful than free text.

## UI requirements (v1)

The UI itself is out of scope for this build spec but minimum requirements for whoever builds it:

- List view sorted by urgency (hot first), then `created_at` descending
- Filter by: campaign type, state, scout source, urgency tier
- Click on card → detail view with full evidence + source link + related-history expansion
- Approve / Reject / Snooze / Ask buttons always visible without scrolling
- Reject opens reason dropdown modal
- Snooze opens duration picker (1 day, 1 week, 1 month, custom)
- Search by district name
- Per-reviewer assignment (Josh's queue / Angela's queue / shared queue)

`// JUDGMENT CALL:` per-reviewer assignment vs. shared queue — start with shared, add per-reviewer if it becomes useful. Don't over-engineer.

## API endpoints Artemis OS exposes for the UI

```
GET  /api/gate1/briefs                   — list (filterable, sortable)
GET  /api/gate1/briefs/:brief_id         — single brief detail
POST /api/gate1/briefs/:brief_id/approve — approve
POST /api/gate1/briefs/:brief_id/reject  — reject with reason
POST /api/gate1/briefs/:brief_id/snooze  — snooze with duration
POST /api/gate1/briefs/:brief_id/ask     — open question thread
```

## Background jobs

- **Snooze re-surface** — every 15 minutes, query `signal_briefs WHERE status = 'snoozed' AND snooze_until <= NOW()`. Set those back to `pending_human_review`.
- **Stale brief alert** — every 4 hours, query `signal_briefs WHERE status = 'pending_human_review' AND created_at < NOW() - INTERVAL '7 days' AND urgency.tier = 'hot'`. Surface in a dashboard widget or send Slack notification.

## Training signal back to Qualifier team

Rejection reasons feed back to 2.2 Ruleset Manager Agent for hit-rate analysis. Specifically:

- Per-ruleset Gate 1 approval rate (monthly)
- Per-rule contribution to rejected signals
- Per-rubric precision (rubric fired → was Gate 1 approval rate above / below baseline?)

These metrics drive Ruleset Manager Agent's proactive proposals.

## DB tables touched

- `signal_briefs` (read + write — status, rejected_reason, snooze_until)
- `campaign_workspaces` (write on approve)
- `signal_queue` (write status transition: approved → in_content_preparation)

## Out of scope for v1 Gate 1

- Per-reviewer assignment automation (just shared queue)
- Bulk actions (approve multiple at once)
- Comment threading / chat (just simple "Ask" button → text field)
- Mobile interface
- Notifications / digest emails (basic Slack alert via webhook is OK)
