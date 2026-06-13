# Worker Brief — "Awaiting your reply" radar (Slack mentions + Gmail unanswered)

**Owner:** next available backend agent (Codex when limits reset, or a terminal sub-agent).
**Lead:** Artemis (Opus) verifies live + merges. **Isolation:** own worktree + own test DB (name contains
`artemis_test`); commit before reporting; do-NOT-merge. **Jon performs a Slack re-auth at verify time.**
**Status:** READY (Slack half gated on the re-auth below).

## Goal
Artemis proactively surfaces **things Jon owes a response to** — Slack @mentions/DMs he hasn't replied to (his
daily driver) and Gmail threads awaiting his reply — as a gentle nudge ("3 people are waiting on you: …").
**Read + surface only. No auto-reply** (that's the agency-writes phase).

## Part 1 — Slack half (needs a USER token)
The existing Slack integration is a **bot token** that can't search and only sees channels Artemis is in. To find
*Jon's* mentions workspace-wide we need a **Slack user token with `search:read`** (and `users:read`).
- **Add a Slack user-token OAuth flow** (or extend the integration to store a second, user token) — request
  `search:read` + `users:read` now, and **also `chat:write`** so the same token serves the later Slack-send
  agency-write (`briefs/p3-agency-writes.md`). Store encrypted, alongside the bot token; don't clobber it.
- Resolve **Jon's Slack user id** via `lookup_user_by_email("jon.fila@amiralearning.com")`.
- **Find open mentions:** `search.messages` for messages mentioning Jon (and DMs to him) within a recent window
  (default last 14 days, capped). For each hit, decide **"awaiting reply"** = Jon has NOT posted a later message
  in that thread/conversation (check `conversations.replies` / history for a message from Jon's user id after the
  mention ts). Skip channels/threads where Jon already replied.
- Return a compact list: who/where/when/permalink/snippet.

## Part 2 — Gmail half (buildable now)
Use the existing Gmail read client. Find threads where **Jon is a recipient**, the **latest message is NOT from
Jon**, and the thread is recent (default last 14 days). That's "awaiting Jon's reply." Return sender/subject/
threadId/snippet. (Heuristic is fine; favor precision over recall — don't surface newsletters/no-reply senders.)

## Part 3 — Surface it (proactivity, not a new stack)
- Feed Artemis's **existing proactivity layer** (the scheduler that runs the morning brief / nudges). Add an
  "awaiting your reply" section/nudge: counts + the top few items with links. Do NOT build a new scheduler.
- **Noise control + dedup:** don't re-nag the same item every run — track what's been surfaced; only re-surface
  on a sensible cadence. Let Jon **dismiss** an item (reuse the dismiss pattern from
  `briefs/p3-dismiss-action-items.md` — a "drop it" that stops it nagging).
- Cap counts; window-limit; never include message bodies in logs.

## Constraints
- Tokens encrypted; never logged. Read-only — no sending. Per-user identity via the existing CF Access path.
- Don't break the existing bot-token Slack flows (signals, Callie pings, commitment DMs).

## Ship gate (Lead verifies LIVE; Jon does the Slack re-auth)
- Jon re-auths Slack (user token, `search:read`/`users:read`/`chat:write`) → token stored encrypted.
- Slack: a real unanswered @mention to Jon shows up; one he already replied to does NOT.
- Gmail: a real thread awaiting his reply shows up; a thread he last replied to does NOT.
- The nudge fires through the existing proactivity path; dismissing an item stops it re-nagging.
