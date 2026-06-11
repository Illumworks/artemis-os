# P2 — Proactivity / Commitments Engine — Build Plan

**Status:** SCOPE (2026-06-11). Grounds P2 from `docs/artemis-pa-build-plan.md` against the code. Done so far:
P1 (Slack two-way) + P4 (Callie). P2 is the next major phase: agents that **follow up unprompted**.

## Goal
Agents infer open-loops / promises / deadlines (from meetings, chat, email), and **surface them in their due
window** — Artemis in Jon's DM (personal/ops), Callie in her marketing channels — through the propose→confirm
gate (informational = auto; actions = confirm). Start with a scheduled morning brief, grow into true
follow-ups. **Extend the existing schedulers, do NOT build a new stack.**

## What exists (reuse)
- **Schedulers (APScheduler):** `automations/scheduler.py`, `pipelines/scheduler.py`, `meetings/scheduler.py`,
  `memory/scheduler.py`, `integrations/token_refresh/scheduler.py` — all started in `main.py` lifespan
  (98-126); clear register/deregister + `misfire_grace_time` catchup pattern. Add a P2 scheduler the same way.
- **Daily brief generator:** `brief/generator.py:generate_brief()` already pulls Jira/Calendar/Slack/OKR/memory
  into a validated brief (Haiku). HTTP-only today — NOT scheduled, NOT Slack-delivered.
- **Commitment source (already extracted!):** `meetings/summary_schemas.py:ActionItem` (text/owner/due),
  stored in `meetings/models.py` JSONB; surfaced to Artemis's prompt via `get_recent_summaries_with_provenance`
  (chat.py) — currently flagged "don't autonomously act without approval."
- **Memory keystone:** `memory/` observations support `category` + `valid_until` + supersession + provenance.
- **Slack delivery:** `integrations/slack/client.py` `post_message`/`post_dm`; per-agent tokens resolvable
  from the integrations registry (Artemis agent_id=artemis, Callie=callie).
- **Propose→confirm:** `floating_artemis/authority.py` layers 3/4 + `confirmation_store` — but it's REACTIVE
  (session/WS-keyed). Proactive (unprompted) delivery is net-new.

## What's net-new
- A scheduled→Slack delivery path (nothing posts proactively today).
- A **commitments** store + lifecycle (active / snoozed / done) + dedupe.
- Commitment **extraction** (start from meeting action_items; later chat/email) + **owner resolution**
  (name→Slack user) + **sensitivity classification**.
- A **proactive** propose→confirm path (the existing one is reactive/session-bound).
- **Trace capture** (P6 foundation) — record agent turns/tool-calls/outcomes for later self-evolution.

## Decisions (lock before building)
- **Commitments = a dedicated lightweight table** (`commitments`: source_type/source_id, text, owner_user_id,
  due, sensitivity, status active|snoozed|done, snoozed_until), NOT pure memory observations — they have a
  lifecycle (snooze/done) that's awkward as keystone supersession. **Also mirror** a memory observation
  (`category='commitment'`) so agents can recall them in conversation. (Best of both.)
- **Autonomy by sensitivity (matches persona Autonomy Levels):** informational proactivity (morning brief,
  "you have X due tomorrow") = **auto-deliver, no confirm** (Level 0). Proactivity that ACTS (draft+send a
  reply, schedule, edit) = **propose→confirm** (Level 2+). Sensitive topics = humour off / escalate.
- **Delivery routing:** personal/ops commitments → Artemis DM; marketing → Callie's channel. Reuse per-agent
  tokens. Dedupe so the same commitment isn't re-pinged (track last_notified_at).
- **Reuse the automations *scheduler + dispatch*, NOT its routes** (HTTP routes are deprecated/410). Build a
  P2 scheduler module following the existing pattern.

## Phases
### P2a — Scheduled morning brief → Slack (FIRST; smallest real proactivity)
Wire `generate_brief()` to a daily cron (Jon's tz) + **deliver to Artemis's Slack DM** (reuse her token +
post_dm). This is the first unprompted message and exercises the scheduled→Slack-delivery path everything
else needs. Low risk, generator already exists. Brief: `briefs/p2a-morning-brief-slack.md`.

### P2-foundation — Trace capture (seed for P6, parallel/early)
Lightweight hook in `handle_turn` (+ tool calls) → record (agent_id, session, prompt digest, tools used,
outcome, tokens) to a `agent_traces` table. Lossless, cheap. Gives P6 real history to learn from later.

### P2b — Commitments store + extraction (meetings first)
The `commitments` table + repo; ingest meeting `action_items` as commitments (owner/due/source=granola_id);
owner→Slack-user resolution; sensitivity classification (cheap LLM/heuristic); dedupe; mirror to memory.
Snooze/done lifecycle (reuse the marketing snooze pattern: `snoozed_until` + filter).

### P2c — Proactive follow-up delivery (the differentiator)
A P2 scheduler job: find commitments due-soon / un-followed-up, route to the right agent surface, deliver
through the autonomy gate (informational auto; action → propose→confirm via a proactive-action queue +
a lightweight confirm endpoint, distinct from the reactive session-bound confirmation_store). Dedupe + snooze.

Later: chat/email extraction (P3 agency-writes overlaps for send), digests, escalation Callie→Artemis→Jon.

## Open unknowns (flag during build)
- `automations.approval_policy` JSONB is undocumented — reverse-engineer before wiring confirms.
- Owner resolution for free-string `ActionItem.owner` ("Sarah" → Slack id) — needs a name→user lookup
  (reuse J9b `slack_users` cache + fuzzy match).
- The proactive confirm path: the existing `confirmation_store` is WS/session-bound; proactive needs its own
  queue + delivery+confirm surface.

## Recommended start
**P2a (morning brief → Slack)** — small, real, exercises the delivery path — plus seed **trace-capture** in
parallel. Then P2b (commitments) → P2c (follow-ups). Lead scopes/briefs each; Codex/terminal build;
Lead verifies + merges.
