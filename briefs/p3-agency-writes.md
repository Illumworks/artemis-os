# Worker Brief — Agency-writes: the propose→confirm gate + phased actions

**Owner:** next available backend agent (Codex when limits reset). **Lead:** Artemis (Opus) verifies live +
merges. **Isolation:** own worktree + own test DB (name contains `artemis_test`); commit before reporting;
do-NOT-merge. **Status:** Foundation + Phase 2a READY; 2b/2c/2d specified for follow-on.

This is where Artemis stops only *reading* and starts *acting*. **Jon's hard rule: she NEVER sends/creates
anything without his explicit approval.** That rule is the whole design — build the gate first, then plug each
action type into it.

## FOUNDATION — the propose→confirm approval gate (build first; shared by all actions)
A generic, persisted, one-shot approval mechanism. NOTHING executes without an explicit human "yes."

- **`proposed_actions` table** (migration; Lead applies on prod): `id`, `action_type`
  (`calendar.create|calendar.update|calendar.respond|slack.send|jira.create|gmail.send`), `payload` (JSONB — the
  fully-drafted action), `preview` (human text), `status`
  (`proposed|approved|rejected|executed|failed|expired`), `requested_by`, `target_user_id`, timestamps,
  `executed_result` (JSONB), `expires_at`.
- **Flow:**
  1. Artemis decides to act → drafts the action → inserts a `proposed_actions` row (`status=proposed`) →
     **DMs Jon a clear preview**: *"I'd like to <action> — <preview>. Reply **yes** to approve, **no** to skip."*
  2. Jon replies in that DM thread → an approve/reject **reply handler** (reuse the commitments done/snooze
     reply-handler pattern) matches the **pending proposal for that user/thread**.
  3. **yes →** execute the action via the right integration, set `status=executed` (+ `executed_result`), confirm
     back to Jon with the result (e.g. event link, message permalink, ticket key). **no →** `status=rejected`,
     acknowledge. **timeout (`expires_at`, default 24h) →** `status=expired`, no action.
- **Safety invariants (enforce + test):**
  - **No execution path exists that isn't gated by `status=approved`.** Auto-fire is impossible by construction.
  - **One-shot:** a proposal executes **at most once** (guard against double-yes / replay).
  - **Preview must match payload** (the thing approved is the thing executed).
  - Full **audit**: every transition logged with actor + timestamp.
  - Match approvals to the **specific** pending proposal (never approve the wrong/stale one).

## PHASE 2a — Calendar writes (build now, proves the gate)
Action types `calendar.create` / `calendar.update` / `calendar.respond`, executed via the existing Google
Calendar write path (the gcal client / MCP `create_event`/`update_event`/`respond_to_event`) using the
**personal** credential. Example: *"Put a 30-min hold with Angela Thu 2pm?" → yes → event created → Artemis
replies with the link.* After-execute, refresh `gcal_events_cache` so it shows immediately.

## PHASE 2b — Slack-send (Jon's daily driver; high value — do right after 2a)
Action type `slack.send`: send a message to a channel/DM/thread after approval. **Design decision to confirm
with Lead/Jon:** send **as the Artemis bot** (existing `chat:write`, clearly attributed) vs **as Jon** (needs the
**user-token `chat:write`** acquired in `briefs/p3-awaiting-reply-radar.md`). Default recommendation: reuse the
radar's user token so it can send *as Jon* when he wants; fall back to bot-attributed otherwise. Pairs naturally
with the radar ("reply to this mention?" → drafts → approve → sends).

## PHASE 2c — Jira create
Action type `jira.create`: create an issue (via the existing Jira integration) after approval — e.g. turn a
commitment/action-item into a ticket on Jon's say-so. Return the issue key/link.

## PHASE 2d — Gmail send (LAST — needs a new scope)
Action type `gmail.send`: compose/send/reply via Gmail. **Requires adding `gmail.send` (and/or
`gmail.compose`) to the personal scope set + a Jon re-consent** (we currently hold `gmail.readonly` only). Build
only after the gate is proven on 2a/2b and Jon re-consents.

## Constraints
- Reuse existing integrations + the commitments reply-handler pattern; don't fork. Tokens encrypted; never
  logged. Test with target = Jon only — **never send to / create against real third parties in tests** (use a
  test channel / Jon's own DM / a sandbox calendar).

## Ship gate (Lead verifies LIVE — foundation + 2a first)
- A proposed calendar event DMs Jon a clear preview; **reply "no" → nothing happens**; reply "yes" → the event
  is really created and Artemis returns the link; the cache reflects it.
- Double-"yes" executes **once** (one-shot holds). An expired proposal never executes.
- No code path can execute an action whose `status` isn't `approved` (assert in tests).
