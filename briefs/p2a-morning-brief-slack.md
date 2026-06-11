# Worker Brief — P2a: Scheduled Morning Brief → Artemis's Slack DM

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2a-morning-brief-slack`. **Plan:** `docs/p2-proactivity-build-plan.md` (P2a).
Test DB at head (artemis_test @ 0079) — real DB-backed tests.

## Why
First piece of true proactivity: Artemis sends Jon a morning brief **unprompted**, in his DM. The brief
generator already exists (`brief/generator.py:generate_brief`) — it's HTTP-only today. P2a adds the
**schedule** + the **Slack delivery**, exercising the scheduled→Slack path the rest of P2 depends on.

## Scope
1. **A P2 scheduler** (new `artemis/proactivity/scheduler.py` or similar) following the EXISTING pattern in
   `artemis/automations/scheduler.py` (APScheduler `AsyncIOScheduler`, cron trigger, `misfire_grace_time`
   for catchup). Register a daily job; start/stop it from `artemis/main.py` lifespan alongside the other
   `start_*_scheduler()` calls (98-126).
2. **Schedule config:** cron + timezone from settings/env (e.g. `ARTEMIS_MORNING_BRIEF_CRON` default `0 8 * * *`,
   `ARTEMIS_MORNING_BRIEF_TZ` default America/New_York — confirm Jon's tz). Easy to change.
3. **The job:** call `generate_brief(session)` → format the brief as **Slack-friendly text** (bold labels +
   short bullet lines — **NO markdown tables**, they render as raw pipes in Slack) → run it through
   `writing_rules.lint_agent_text` (no em-dashes/emojis) → deliver to **Artemis's** DM with Jon:
   resolve Artemis's integration token (`provider="slack", agent_id="artemis"`, decrypt access_token),
   `SlackClient(token).post_dm(user_id=<Jon U09F3EPJXSQ>, text=...)`. Resolve Jon's id from the slack config
   `authed_user_id` (don't hardcode if avoidable; constant fallback ok).
4. **Idempotency:** don't double-send the same calendar day's brief (track last delivery — a small state row
   or reuse the brief snapshot's date). A second cron tick / restart in the same window must NOT re-post.

## Constraints
- EXTEND the existing scheduler infra; do not add a new scheduling stack. Reuse `brief/generator.py` as-is.
- This is **informational / Level-0** proactivity → auto-deliver, NO propose→confirm gate (that's for
  action-proactivity in P2c).
- Lossless; no new deps; ruff + mypy strict; `./scripts/check.sh` (pre-existing repo-wide format debt in
  ~9 unrelated files is a known baseline — don't fix those here).
- Don't touch the Slack events receiver / agent loop internals — this is an outbound scheduled job.

## Tests (DB-backed where natural; set ARTEMIS_DB_URL to the test db)
- The job generates + delivers once: mock `generate_brief` + `SlackClient.post_dm`, assert post_dm called
  with the brief text, addressed to Jon's id, via Artemis's token.
- Idempotency: a second run in the same day does NOT re-post.
- Formatting: the delivered text has no markdown table rows and no em-dashes/emojis (post-lint).
- Scheduler registration: the job registers on startup with the configured cron.

## Acceptance
At the configured time, Jon gets a clean morning brief in his Artemis DM (Slack-friendly, lint-clean, once
per day). Lead verifies: trigger the job manually (or set the cron a minute out) and confirm the DM lands,
formatted well, no double-send.

## Note
P2-foundation (trace capture) is a separate parallel slice — see the build plan; not part of P2a.
