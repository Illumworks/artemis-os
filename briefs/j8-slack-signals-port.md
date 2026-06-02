# J8 — Slack signals backend (Python-native rebuild)

**Owner:** Worker (Sonnet)
**Scope:** ~300 LOC backend. Estimated: half-day.
**Depends on:** J1 Slack integration (DONE — `slack_inbound_messages` table exists, bot/user tokens in `integration_configs`).
**Why Jon wants this:** Focus rail's "Needs Your Reply" card depends on Slack signals. Frontend calls `GET /api/slack/signals` (`public/js/core/api.js`); currently 404. Without it the card renders empty.

## Important architectural divergence from Node

**Do NOT replicate the Node implementation's mechanism.** The Node version at `/Users/artemis/Desktop/Artemis/claudeck-artemis/server/slack-source.js` (307 LOC) is a 2024-era workaround that subprocesses the Codex CLI with a Slack plugin prompt — it pre-dates Artemis having a real Slack integration. Now (post-J1) we have direct Slack OAuth + Events API receiver + `slack_inbound_messages` table. We don't need to shell out to Codex.

**Build it native:** compute signals from our own data + targeted Slack API calls.

Reference Node only to understand the OUTPUT SHAPE the frontend expects; ignore its inputs.

## What you're building

### 1. Module `artemis/integrations/slack/signals.py`

Single async function `get_slack_signals(session, force_refresh=False) -> dict` returning the exact shape the frontend expects (mirror Node's response):

```python
{
    "connected": bool,            # True if integration row exists and token works
    "status": str,                # "connected" | "not_connected" | "unavailable"
    "missedMentions": int | None,
    "unreadDMs": int | None,
    "replyNeededThreads": int | None,
    "checkedWindow": str,         # e.g. "last 48h"
    "checkedAt": str,             # ISO timestamp
    "source": str,                # "artemis" — distinguishes from Node's "codex_cli"
}
```

In-process cache: TTL 60 seconds. `force_refresh=True` bypasses cache. Mirror Node's `SLACK_SIGNAL_CACHE_TTL_MS = 60_000`.

### 2. Signal definitions

**`missedMentions`:** count of rows in `slack_inbound_messages` where:
- `bot_mentioned IS TRUE` (or however the bot-mention flag is stored — read the schema)
- `created_at >= now() - INTERVAL '48 hours'`
- `responded_to IS NOT TRUE` (or no outgoing message correlates back — your call; doc in report)

If `slack_inbound_messages` doesn't have a `bot_mentioned` field, infer: text contains the bot user_id (look up via `auth.test` API once, cache on the integration row).

**`unreadDMs`:** count of DM channels where the bot has unread messages from the user. Two approaches — pick the simpler one and document in the report:
- (a) Query Slack API `conversations.list(types="im")` then for each, `conversations.history(latest=last_acked_ts)`. Returns unread count.
- (b) Use `users.conversations(types="im")` + `conversations.mark` state. Cleaner but requires user-token scope.

If your bot only has bot-token scope (not user-token), `unreadDMs` may need to be `None` with a clear `status` field. Do not lie about it.

**`replyNeededThreads`:** threads where the user wrote something, the bot saw it, and no human (or bot) has responded since. Heuristic: `slack_inbound_messages` rows where `thread_ts IS NOT NULL`, latest message in the thread is from a user, and `now() - latest_ts > 4 hours`. Adjust the threshold if it produces noise.

If the inbound table doesn't track thread state granularly enough, set `replyNeededThreads` to `None` rather than fabricate.

**`checkedWindow`:** describe what you actually queried — e.g. `"slack_inbound_messages over last 48h + API conversations.list"`.

### 3. Route `artemis/routes/integrations_slack.py` (extend if exists, or add)

```python
@router.get("/signals")
async def get_signals_endpoint(
    refresh: int = Query(default=0),
    session: AsyncSession = Depends(db.get_session),
) -> dict:
    return await signals.get_slack_signals(session, force_refresh=bool(refresh))
```

Mount under `/api/slack` (whatever prefix `integrations_slack_*` already uses; read existing slack routes first). Register via existing include if needed.

If the integration is not connected, return:
```python
{
    "connected": False,
    "status": "not_connected",
    "missedMentions": None,
    "unreadDMs": None,
    "replyNeededThreads": None,
    "checkedWindow": "",
    "checkedAt": <now-iso>,
    "source": "artemis",
}
```

NEVER return 4xx for "not connected" — the frontend treats `connected: false` as a valid response and renders an empty state. Returning 404 makes the dashboard show an error.

### 4. Tests `tests/test_j8_slack_signals.py`

- `test_signals_not_connected_returns_zeros_with_connected_false` — no integration row → response shape correct, all counts None
- `test_signals_connected_zero_activity` — integration exists, no inbound rows → counts all 0, status="connected"
- `test_missed_mentions_counts_recent_mentions` — seed 3 mention rows in last 24h, 1 mention in last 72h → returns `missedMentions=3` (assuming 48h window)
- `test_reply_needed_threads_heuristic` — seed a thread where latest message is user-side and >4h old → counts 1
- `test_unread_dms_falls_back_to_null_without_user_scope` — if only bot scope available, `unreadDMs=None` with appropriate `status`
- `test_cache_hits_within_ttl` — call twice within 60s → second call doesn't hit DB/API (use a counter mock)
- `test_force_refresh_bypasses_cache` — `refresh=1` calls underlying logic even with fresh cache
- `test_route_returns_200_when_unavailable` — Slack API errors → returns shape with `status="unavailable"`, NOT a 502

## Quality acceptance — tick before reporting done

- [ ] `./scripts/check.sh` passes (or doc same pre-existing failures already known)
- [ ] Manual smoke against live app on port 8000:
  - `curl http://localhost:8000/api/slack/signals` returns the JSON shape above. Paste output.
  - `curl http://localhost:8000/api/slack/signals?refresh=1` re-fetches (verify by hitting twice quickly — second should match first)
- [ ] If `slack_inbound_messages` is empty, signals should be 0/0/0 with `connected=true, status="connected"` — NOT errors
- [ ] No raw SQL bypass of the repository pattern (look at `artemis/integrations/repository.py` for the style)
- [ ] Diff re-read twice; no stubs / no TODO / no mock data
- [ ] Coverage on `artemis/integrations/slack/signals.py` ≥85%

## Critical guardrails — DO NOT VIOLATE

1. **Lossless memory rule:** never delete inbound messages. Signals are derived counts; don't mutate source rows.
2. **Local-only git.**
3. **No deps < 7 days old.** Use existing `slack-sdk` or whatever's already in `pyproject.toml`.
4. **artemis/__init__.py invariant:** BOTH `load_dotenv` use `override=False`. Don't touch.
5. **Free-by-default:** all Slack API calls use the existing bot token from `integration_configs`. No new API keys.
6. **You are running parallel to J7.** Do NOT touch `artemis/brief/`, `artemis/routes/daily_brief.py`, or any file with `daily_brief` / `brief_snapshots` in the path. Migration head: pick the next free number AFTER J7's reservation if your branches collide at merge.

## Out of scope (separate briefs)

- Outgoing reply queue / "Slack reminder" backend (J6c already has the `/api/slack/reminder` endpoint stub)
- Slack channel listing UI
- User-token OAuth flow if not already wired
- Multi-workspace

## Where to start

1. Read this brief twice
2. Read `artemis/integrations/slack/` end-to-end (client, OAuth, events receiver). Understand what fields `slack_inbound_messages` actually stores.
3. Read `artemis/integrations/repository.py` for the integration-row resolver pattern
4. Module → route → tests → manual smoke
5. Paste signals JSON output verbatim in the report

Be terse but thorough. No emojis. No comments unless WHY is non-obvious.
