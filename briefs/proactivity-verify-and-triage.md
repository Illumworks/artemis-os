# Worker Brief — Proactivity engine: verify-live + triage (NOT a build)

**Owner:** terminal. **Lead:** Opus verifies the report + merges any branch terminal recommends.
**Isolation:** isolated worktree + own test DB (name contains `artemis_test`); do-NOT-merge — report to Lead.
**Status:** READY. **Important:** the proactivity engine is ALREADY BUILT, merged, and wired — this is a
verification + cleanup task, NOT a rebuild. Do not re-implement anything that exists.

## Context (what already exists on main — do not rebuild)
- `artemis/proactivity/commitments.py:send_commitment_followups` — real impl: reactivates expired snoozes,
  finds due-soon / un-followed-up commitments, renders follow-up text, routes by sensitivity (marketing →
  Callie's channel via her token; else → Artemis DM via her token), posts via `SlackClient`.
- `artemis/proactivity/scheduler.py` registers `_register_commitments_followup_job` (cron
  `settings.commitments_followup_cron`) alongside morning-brief, OKR check-in, stale-review escalation.
- `start_proactivity_scheduler()` is called at app boot (`artemis/main.py:126`), so the jobs run in prod.
- Migrations `0083_commitments_engine`, `0086_radar_surfaced_items` are applied. `radar.py` (awaiting-reply
  radar) + `p3-agency-writes` are merged.

## Task 1 — Prove the follow-up FIRES end-to-end (assert the EFFECT, not "code exists")
This is the one thing unconfirmed: that a real commitment actually produces a real Slack follow-up.
- In a controlled way (test DB / a safe test recipient or a dry-run capture of the Slack `post_dm`/
  `post_message` call), seed a commitment that is due-soon and not yet followed up, run
  `send_commitment_followups(session, now=...)`, and CONFIRM: (a) it selects the commitment, (b) it routes to
  the correct surface (personal/ops → Artemis DM, marketing → Callie channel), (c) the Slack send is actually
  invoked with the rendered text, (d) the commitment is marked followed-up / not re-notified next run.
- Do a personal-sensitivity case AND a marketing-sensitivity case (Artemis vs Callie routing).
- Report the EFFECT you observed (the actual outbound payload + the DB state change), not just "tests pass".
- If you must avoid posting to Jon's real Slack, capture/mocked the SlackClient call and assert on it; note
  clearly that you did so and that a true live post still needs Jon's go.

## Task 2 — Triage the two unmerged branches
For `worker/p2-proactivity-voice` and `worker/p3-tool-implementations`:
- `git diff main..<branch>` — is the content already on main (superseded), or does it carry unmerged value?
- Does it apply cleanly / pass its tests on top of current main? Any conflicts?
- Recommend per branch: **MERGE** (with a one-line what-it-adds), or **DELETE as stale** (with why). Do NOT
  merge — Lead merges after reading your recommendation.

## Report back
1. Task 1: the observed effect (outbound Slack payload + DB state) for both routing cases; whether a true live
   post was made or mocked; any bug found.
2. Task 2: per-branch recommendation (merge/delete) + rationale + conflict status.
3. Anything that looks built-but-not-actually-working (the lesson from the memory phase: wired ≠ firing).
Keep it lean — this is verification + triage, not construction.
