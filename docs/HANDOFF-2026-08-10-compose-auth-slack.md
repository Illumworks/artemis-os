# HANDOFF — Writing Studio compose + Claude auth + Slack agents

**Original: 2026-07-21 session. Corrected + extended: 2026-08-10 (Opus Lead).**

Same machine, same repo (`~/Artemis/artemis-os`), same local memory
(`~/.claude/.../memory/MEMORY.md`).

> **Read the corrections in §0 before acting on anything below.** The original
> handoff's diagnosis of the Slack "outage" was wrong in three specific ways,
> and its recommended next step could not have worked. The fixes for that are
> now shipped.

---

## §0 — CORRECTIONS (2026-08-10)

### 0.1 The commit dates are 2026-07-21, not "this session"

The original doc was written on 2026-08-10 but describes work committed three
weeks earlier. Actual dates:

```
df7f420  2026-07-21 10:01   writing-studio async compose
661dff5  2026-07-21 10:14   claude-code blank error
a549d56  2026-07-21 10:40   slack-tools bot_token
683b2d1  2026-08-06 00:58   watchdog spiral   (the "parallel session" commit)
```

All four are in the running process. The fixes ARE live.

### 0.2 "Artemis/Kai not responding in Slack" was wrong

**Kai was never down.** Kai answered in `#enablement-library` at 10:03 ET on
2026-08-10 — an hour before the handoff was written. 132 messages on that
session.

**Artemis was never down either.** She has delivered the morning brief every
weekday without a miss, including 2026-08-10 at 08:00 ET (`status='sent'`,
delivery #72). **Callie** has been pushing top-tier signal cards to Slack at her
3/day cap, unbroken, through 2026-08-09.

What is *actually* true: **no agent except Kai has had an inbound Slack
conversation in weeks.** Artemis's last conversational turn was 2026-07-21
10:59 — the very exchange that ended in the `list_slack_channels failed:
'access_token'` bug that `a549d56` fixed 19 minutes later. Callie's was
2026-07-06. Ares's was 2026-06-19.

Whether that is a bug or simply nobody having messaged them is **still open** —
see §0.5.

### 0.3 Why the original diagnosis went wrong: six stores, no logging

Agent activity is recorded in **six unrelated places**, and reading any single
one gives a confidently wrong answer:

| Store | Records | Covers |
|---|---|---|
| `floating_artemis_messages` | conversational turns | all agents |
| `morning_brief_deliveries` | scheduled briefs / OKR check-ins | Artemis |
| `memory_observations` (`category='callie_signal_push'`) | autonomous signal cards | Callie |
| `agent_traces` | any provider call | all agents |
| `slack_inbound_messages` | **keyword-mention triage only** — not DMs | channels |
| `pipeline_runs` | pipeline executions | pipelines |

The original session read the first one, found nothing for Artemis since
2026-07-21, and concluded she was down — while stores 2 and 3 showed both agents
working daily.

**Compounding it: the app had no logging configuration at all.**
`settings.log_level` existed and `ARTEMIS_LOG_LEVEL=info` was set in `.env`, but
nothing consumed it, so `artemis.*` loggers fell through to Python's
`lastResort` handler at WARNING. Every `logger.info` and `logger.debug` in the
codebase was discarded in production.

That is why the original "PICK UP HERE" step could not have worked: it says to
`tail -f app.err.log` and expect `route_inbound` → `handle_turn`. Those log at
**debug**. So does the retry-skip line the doc wanted to confirm hypothesis #2.
Absence of those lines was never evidence of anything.

### 0.4 Both problems are now fixed

| Fix | What |
|---|---|
| `artemis/logging_setup.py` | wires `settings.log_level` into logging; called first in `main.lifespan`. Timestamped records to stderr → `app.err.log`. Additive (never strips root handlers) so pytest's `caplog` keeps working in the 27 modules that use it. |
| `artemis/routes/integrations_slack_events.py` | added an INFO arrival marker for every Slack event, and promoted the **five silent-drop decisions** (subtype, bot-echo, duplicate event_id, duplicate message identity, dispatch gate) from debug → INFO. Metadata only, never message text. |
| `artemis/ops/` | `uv run python -m artemis.ops` — one consolidated health report across all six stores, plus derived findings. Read-only; works even when the app is down. Exit code 1 when something is wedged. |

The logging change alone turned a silent boot into a full inventory of every
scheduler and cron. Before: 6 log lines in 22 hours. After: every scheduler
start, every cron expression, Postgres readiness, and each scout subprocess
result.

### 0.5 The Artemis-inbound question — how to actually settle it

The config is **not** the problem. Verified by running the real loader and the
real gate for all four agents: Artemis's `authed_user_id` and `allowed_user_ids`
both contain Jon (`U09F3EPJXSQ`), signing secret and token are set, and
`_should_handle_event(message, im)` returns `True`.

Events *are* reaching her endpoint: since the 2026-08-06 log rotation,
`/events` (Artemis) took 9 POSTs, `/events/kai` 14, `/events/callie` 9 — all
200. Nine Artemis events, zero conversational turns. They may have been
legitimately dropped (bot echo, non-owner sender, wrong channel) or wrongly
swallowed.

**With the new logging, one fresh DM settles it.** Send Artemis a message, then:

```bash
grep "slack event" ~/Library/Logs/artemisos/app.err.log | tail -20
```

- `slack event received … DROPPED: <reason>` → we now know the exact gate. Fix it.
- `slack event received` with no DROPPED line → it reached `handle_turn`; the
  problem is downstream.
- nothing at all → the event isn't arriving; check Slack's event-subscription
  health for her app.

### 0.6 The retry-guard latent bug is still real and still unfixed

`integrations_slack_events.py` (the `X-Slack-Retry-Num` guard) acks-and-skips
**any** retry, assuming retry_num=0 already succeeded. If the original delivery
hit a restarting app, every retry is skipped and the message is lost forever.
Worth fixing properly (only skip when the event_id has a dedup record proving it
was processed). It is a genuine latent bug — it was just never the cause of the
believed outage.

### 0.7 NEW — the marketing funnel's back half has been idle two months

Not in the original handoff, and nobody had flagged it:

- Three pipeline runs have been `awaiting_approval` since 2026-06-06/07 — two on
  `marketing.main`, one on `marketing.campaign_deliverables`. The scheduler
  treats them as in-flight and **skips every scheduled run of those pipelines**.
  Nothing errors; nothing alerts. `marketing.main` (cron `0 */4 * * *`) has not
  run since 2026-06-06.
- **Signal collection is unaffected and healthy** — 296 signals in the last 7
  days, 0 of them via the pipeline. Scouts write directly, which is why Callie
  keeps pushing cards daily.
- But **nothing has been approved past Gate 1 since 2026-06-15**, and all 5
  campaign candidates sit at `human_gate_1`, newest 2026-06-16.

So: the front of the funnel works beautifully; the approval half is dead. Needs
Jon's decision — is that "nobody's been approving" or "the approval path is
broken?" Clearing the three wedged runs is the prerequisite either way.

---

## §1 — What the original session fixed (unchanged, still accurate)

### Writing Studio compose timeout — `df7f420`
- **Symptom:** "Failed to generate Writing Studio draft"; nothing in server logs.
- **Root cause:** compose was ONE long synchronous HTTP request for the whole
  claude-code turn; Cloudflare's edge kills any request >~100s and returns
  non-JSON 5xx → generic toast.
- **Fix:** async job + polling. `artemis/marketing/routes/writing_studio.py`:
  extracted `_perform_compose()`; kept sync `POST /compose` as a thin wrapper;
  added `POST /compose/start` + `GET /compose/jobs/{job_id}` (in-memory job
  store, 1800s TTL). FE: `composeWritingDraftViaJob` in
  `public/js/core/api.js`; switched both call sites — `composer-v5.js`
  `sendChatMessage` and `writing-studio.js`.
- Live-typing (streaming) deferred: `docs/writing-studio-live-typing-streaming.md`.

### Claude adapter blank-error masking — `661dff5`
Both subprocess paths in `artemis/providers/claude_code/adapter.py` raised
`ProviderAPIError` with only stderr. The CLI writes real errors (e.g. auth 401)
to STDOUT as JSON with empty stderr → opaque "Provider API error 1:". Added
`_cli_failure_detail(stdout, stderr)` on both paths.

### Slack tools KeyError — `a549d56`
The 5 Floating-Artemis Slack tools (`artemis/integrations/slack/tools.py`) read
`creds["access_token"]` off `integrations[0]`. Bot rows (ares, kai) store the
token under `bot_token` → `KeyError('access_token')`. Added `_slack_token(creds)`
(access_token OR bot_token). **All Slack tokens were valid the whole time.**

### The Claude auth outage (resolved)
The Claude CLI subscription login (OAuth in macOS Keychain) had expired → 401.
There is NO API key; everything runs on the Max login. Jon re-ran `/login` and
it was verified fixed.

---

## §2 — Current runtime state (2026-08-10 11:28 ET)

- App healthy, `healthz` 200. Restarted twice this session to deploy the logging
  fixes; pid changed each time and verified.
- **The watchdog spiral (`683b2d1`) is genuinely fixed** — watchdog reports
  `creep ok: port-8000 TIME_WAIT=1, mean probes=1.00` on every 2-minute cycle.
- Tunnel up.
- One wart: the boot log shows the first startup attempt dying on
  `CannotConnectNowError: the database system is starting up` before a retry
  succeeds. The boot race survives but is real.
- `radar: gather_radar_items partial failure: This session is provisioning a new
  connection; concurrent operations are not permitted` recurs — a concurrent-use
  bug on one shared session in the radar path. Unfixed, non-fatal.

## §3 — Open items

**Needs Jon's decision:**
1. The three wedged `awaiting_approval` runs (§0.7) — clear them?
2. The idle approval half of the funnel — nobody approving, or path broken?

**Known bugs, unfixed:**
3. Slack retry guard strands messages (§0.6).
4. Radar concurrent-session failure (§2).
5. `channel_not_found` when posting to `C0B9CHVC7KQ` / `C0BB17EJLKC`. Note
   `C0BB17EJLKC` is the channel Kai posts in *successfully* — so this is a
   wrong-token-for-channel bug, not a dead channel. Fits the open chip about
   Slack tools picking an arbitrary `integrations[0]`.
6. `./scripts/check.sh` is currently RED: 51 pre-existing mypy errors across 26
   files, and 8 pre-existing test failures in the Slack/argus area (stale
   `_SlackAgentConfig` constructions missing `always_respond_in_channels`, plus
   `test_c3_no_provider_path` patching a nonexistent `resolve_adapter`). All
   verified pre-existing by stashing. Worth a cleanup pass — a red gate means
   the next real regression hides in the noise.

**Follow-up chips already spawned:**
- Fix stale `_FakeResult` mock in compose tests (`task_984af4f8`).
- Audit Slack token key + agent-scoped row selection (`task_3c69b710`).

## §4 — Key paths / commands

```bash
# One-shot: is the whole system alive, and where is it stuck?
uv run python -m artemis.ops
```

```bash
# Trace a Slack message end to end (works now that logging is wired)
grep "slack event" ~/Library/Logs/artemisos/app.err.log | tail -20
```

```bash
# Restart REQUIRES -k, and verify the pid changed
launchctl kickstart -k gui/$(id -u)/me.artemisos.app
```

- Repo: `~/Artemis/artemis-os` (sessions may open in an empty `~/Desktop/...`
  directory — use the real path).
- Logs: `~/Library/Logs/artemisos/app.err.log` (app logging, now timestamped) +
  `app.out.log` (uvicorn access).
- Health: `curl -s http://127.0.0.1:8000/healthz`.
- Test DB run: `PGPASSWORD=artemis ARTEMIS_DB_URL=…artemis_test ARTEMIS_TEST_DB_URL=…artemis_test PYTHONPATH=$PWD .venv/bin/python -m pytest …`
- Relevant memory: `project-writing-studio-compose-timeout`,
  `project-slack-token-key-schema`, `feedback-app-restart-requires-k`,
  `feedback-verify-actual-call-path`.
