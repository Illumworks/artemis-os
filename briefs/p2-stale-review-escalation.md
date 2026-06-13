# Brief (QUEUED for P2 kickoff) — Stale-review escalation: Callie DMs the reviewer

**Status:** READY — first concrete P2 (proactivity) job. **Owner:** Codex (backend). **Lead:** Artemis (Opus)
verifies live + merges.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/p2-stale-review-escalation`; **commit your
work on the branch before reporting**, then do-NOT-merge-report (Lead verifies + merges).
**Origin:** Jon, 2026-06-12. This is the "follow up on an open loop that's gone stale" pattern (the
OpenClaw-style commitments idea) applied to draft reviews — a strong, real first use-case for the proactivity
engine.

## The behavior
After a draft is marked **ready for review** (which now posts a channel @mention — see `ws3-1`), if it's
**still not approved after ~1 day**, Callie escalates with a **direct DM** to the reviewer.

- **Timing (Jon's call):** escalate when it has sat **longer than a day** — i.e. **on the second day, toward
  EOD**. Practically: a daily **end-of-day** check (late afternoon, reviewer-local where feasible) that catches
  drafts whose `ready_for_review_at` is more than ~1 day old and which are still unapproved.
- **Once per draft** (dedupe): record an `escalated_at` so Callie doesn't re-DM the same draft every day.
  (Default = single nudge; revisit if Jon wants gentle recurring reminders.)
- **Stops** when the draft gets approved (the Gate-2 approval state clears the open loop).

## Reuses (don't rebuild)
- The **DM mode** of `send_callie_ready_for_review_ping` (added in `ws3-1`).
- The **`ready_for_review_at` timestamp** (recorded in `ws3-1`).
- The **existing scheduler infra** (meetings/pipelines/marketing already run scheduled jobs — extend that,
  per the P2 plan; do NOT stand up a new scheduler stack).
- Gate-2 approval state to determine "still not approved."

## Why this is the right P2 opener
It's small, concrete, Jon-requested, and exercises the exact P2 substrate (a durable scheduled job that
detects a stale commitment and follows up through Callie) — proving the proactivity loop on a real workflow
before generalizing to chat/meeting/email-derived commitments. See `docs/artemis-pa-build-plan.md` (P2).

## Ship gate (Lead verifies LIVE — assert the EFFECT)
A draft in `ready_for_review` with `ready_for_review_at` older than the threshold AND still unapproved → the
EOD check fires and Callie sends **one** direct DM to the reviewer (reuse the `dm` mode of
`send_callie_ready_for_review_ping`). Lead verifies by exercising the check against a stale **test** draft with
**reviewer = Jon** (`jon.fila@amiralearning.com`, so the DM lands on Jon, not Angela), confirming:
(a) the DM is delivered (Slack ok + resolved to the right user), (b) `escalated_at` is stamped, (c) a second
run does NOT re-DM (dedupe), (d) an already-approved draft is skipped, (e) a not-yet-stale draft is skipped.
The threshold/EOD timing must be **configurable** (no brittle hardcoded clock). Reuse the existing scheduler
infra (no new scheduler stack). No hardcoded secrets; named-agent output lint applies to Callie's DM text.
