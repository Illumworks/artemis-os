# Worker Brief — WS3.1: Ready-for-review notifies via CHANNEL @mention (not DM)

**Owner:** Codex. **Lead:** Artemis (Opus) verifies live + merges.
**Branch:** `worker/ws3-1-channel-mention` — **use an ISOLATED WORKTREE this time** (ws3 was built in the
main tree by mistake; do-NOT-merge-report from the worktree).
**Status:** READY. Builds on ws3 (`6c91937`, already on main).

## Why (Jon's escalation design, 2026-06-12)
Gentle/public nudge first, direct/personal only if ignored. So the **initial** ready-for-review notification
should be a **channel @mention**, NOT a DM. (The DM is reserved for the *overdue* escalation — that's a
separate P2 job, `briefs/p2-stale-review-escalation.md`.)

## Scope

### Backend (`review_notifications.py` + the route)
1. **Initial notification = channel @mention.** When a draft is marked ready-for-review, Callie posts in the
   **Marketing Campaigns channel** (`settings.marketing_campaigns_slack_channel`) **@mentioning the reviewer**
   (`<@{slack_user_id}>`) — NOT an immediate DM. Resolve the reviewer's Slack ID via `users.lookupByEmail`
   (as today) to build the mention. If the reviewer can't be resolved, post to the channel without a personal
   mention (name in plain text) — stay deterministic. Message stays a fixed template (no emoji/em-dash;
   named-agent output lint applies): `<@U…> — "{title}" by {author} is ready for review. <link|Open draft>`.
2. **Keep the DM path available, don't delete it.** Refactor `send_callie_ready_for_review_ping` to take a
   `mode` (e.g. `"channel_mention"` default vs `"dm"`), so the P2 escalation job can reuse the **dm** mode
   later. The `/ready-for-review` endpoint uses `channel_mention`.
3. **Record a timestamp on the flag.** Ensure the `ready_for_review` flag stores **when** it was requested
   (e.g. `ready_for_review_at`) — the P2 escalation needs it to compute ">1 day". If ws3 already stamps one,
   reuse it; otherwise add it.

### FE (tiny)
- Update the confirmation toast wording to match the new behavior, e.g. "Posted in Marketing Campaigns for
  {reviewer}." No other FE change (the existing "Ready for review" action + reviewer picker stay).

## Constraints
- Isolated worktree; reuse Callie's token/posting path; deterministic template (no emoji/em-dash).
- Do NOT touch the selection-toolbar logic in composer-v5.js.
- Lossless: state/timestamp only.

## Acceptance (Lead verifies LIVE — assert the EFFECT)
Triggering ready-for-review posts a **channel @mention** of the reviewer in Marketing Campaigns (Lead will
verify with a clearly-marked test, reviewer = Jon, so the @mention lands on Jon, not Angela), the
`ready_for_review_at` timestamp is recorded, and **no initial DM** is sent. The DM `mode` still works when
called explicitly (the P2 escalation will use it).
