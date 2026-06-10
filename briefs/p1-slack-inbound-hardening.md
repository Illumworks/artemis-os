# P1 — Slack Inbound Hardening (echo guard + owner allowlist + identity)

**Status:** BUILT + TESTED + LIVE-VERIFIED (2026-06-10). First real Jon↔Artemis Slack DM
round-tripped; echo loop confirmed fixed (inbound count held at 173, zero bot-self re-ingest).
Required a Slack-side fix too: App Home → Messages Tab → "Allow users to send … messages" was OFF
(showed "Sending messages to this app has been turned off"); Event Subscriptions were already correct.
**Author:** Artemis (Opus Lead). **Chapter 2 / P1** (Slack two-way).

## Why this exists
An audit of the J1/J9 Slack work (built 2026-05-17/18, before state-capture discipline tightened —
which is why `docs/SESSION-STATE.md` wrongly listed P1 as unstarted) found the inbound conversational
plumbing already SHIPPED and registered: Events receiver + HMAC, dedupe, `route_inbound` →
`handle_turn` → reply-in-thread, Slack tools in the FA registry, owner-mode credential UI, triage queue.
A live Slack app is connected (active bot token in DB, `bot_user_id U0AMNKUGXLP`, correct scopes).

But the **database revealed it had never carried a real conversation**: `slack_inbound_messages` held
172 rows, **every one authored by the bot itself** — an echo loop — and `last_verified_at` was null.
Two blocking defects, both at the inbound boundary:

1. **No bot-self filter at ingest.** Artemis's own replies (delivered back as `message.im` /
   `app_mention`) were re-dispatched into the agent loop → infinite echo. The 172 self-rows are the
   fingerprint. (Matches the "relay echo-loop" noted in `docs/artemis-slack-findings-and-routing.md`.)
2. **No identity gating.** `route_inbound` pulled `user_id` but never passed it to `handle_turn`, and
   nothing checked *whether the sender was allowed*. Any workspace member who @mentioned or DM'd the bot
   got a full Artemis agent loop — her memory, Gmail, Calendar, Jira, OKRs — with the reply posted
   publicly. No allowlist/pairing was ever specified in any brief (confirmed across the brief corpus).

## What shipped in this slice
All at the `artemis/routes/integrations_slack_events.py` boundary + supporting config.

1. **Bot-self / non-human filter** — `_is_bot_authored(event, bot_user_id)`: drops events with `bot_id`,
   `subtype == "bot_message"`, or `user == our bot_user_id`. Runs *before* record + dispatch, so no new
   echo rows are written. The bot identity is resolved from the active integration row.
2. **Owner allowlist gate** — `SlackConfig.allowed_user_ids` + `is_user_allowed()` in
   `artemis/integrations/config_resolver.py`. Resolved as `authed_user_id` (owner, always included when
   set) + extras from `integration_configs["slack"]["allowed_user_ids"]` (list) or `SLACK_ALLOWED_USER_IDS`
   env (comma-sep). **Fail-closed**: empty allowlist → nobody is routed. Non-allowed *human* messages are
   still recorded (`upsert_slack_inbound`) for audit/triage, just not dispatched into the agent loop.
3. **Identity threaded through** — `route_inbound` stores `slack_user_id` in the FA session metadata and
   resolves the speaker's display name from the J9b user cache, passing `speaker_name` into `handle_turn`
   → the Slack branch of the system prompt ("You are speaking with <name>"). New `handle_turn` kwarg is
   optional (default None) so the web UI path is unaffected.

## Tests
`tests/test_p1_slack_inbound_gate.py` — 18 tests: bot-self filter (by bot_id / subtype / self-user-id),
allowlist parsing + membership + fail-closed, config resolution (owner + DB extras dedupe, env fallback,
empty), and endpoint integration (bot event dropped before record; non-allowlisted recorded-not-routed;
allowlisted routed; fail-closed; duplicate not re-routed). Existing J1/J9b Slack-events + tools tests stay
green (44 pass combined); full `floating_artemis` suite green (240) after the `handle_turn` signature add.
ruff + ruff format + mypy clean on all changed source.

## To turn on live (remaining — needs Jon)
- Populate the allowlist with Jon's **personal** Slack user ID (not the bot `U0AMNKUGXLP`): set
  `SLACK_AUTHED_USER_ID` (and/or `SLACK_ALLOWED_USER_IDS`) in `.env`, or store under
  `integration_configs["slack"]`. Until then the gate is fail-closed (nothing routes).
- Confirm the Events Request URL (`/api/integrations/slack/events`) is registered in the Slack app and
  reachable through the cloudflared tunnel.
- Live round-trip: Jon DMs Artemis → exactly one reply, no echo loop; a non-allowlisted test user gets
  no session.

## Notes / parked
- The 172 historical self-rows are left in place (audit data; the filter prevents new ones).
- Pre-existing, unrelated: the **test DB is not migrated to head post-move** — `test_j9_slack_triage` /
  `test_j9b_*` fail with `InFailedSQLTransactionError` on clean HEAD (run `alembic upgrade head` against
  `ARTEMIS_TEST_DB_URL`). `.pytest_cache` also still holds old `~/Desktop/...` node paths (cosmetic).
- Mypy has 4 pre-existing errors in `google_docs/client.py` + `pipelines/node_executors/human_gate_executor.py`
  (unrelated to this slice).
