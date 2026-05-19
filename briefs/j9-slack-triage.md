# J9 — Slack triage: turn the mention counter into a workflow

**Owner:** Worker (Sonnet)
**Scope:** ~250 LOC backend + frontend. Half-day to a day.
**Depends on:** J8 (Slack signals — DONE), Floating Artemis chat (LIVE).
**Blocks:** Personal workspace "fully usable" milestone.

> **Important:** All file paths in this brief are relative to the repo root. The harness controls the worktree. Do NOT treat absolute paths in comments or examples as "the repo." Write to the worktree you were spawned in.

## Why

J8 surfaces Slack signals on the Focus page — currently shown as a single tile: "38 missed mentions are leading the Slack queue." This is informational, not actionable. Jon's feedback: *"it just tells me how much i missed vs like showing me where i was mentioned and with action buttons to send a reply (and have artemis craft a draft) things like that."*

The data exists — `slack_inbound_messages` already gets populated by J8's ingestion path. What's missing is the surface that turns the queue into a workflow: list the mentions, let Jon act on each one (reply via Artemis, open in Slack, mark resolved), and persist resolution state so resolved items disappear from the count.

## Scope

### Backend — list endpoint + resolution state

- [ ] Alembic migration: `ALTER TABLE slack_inbound_messages ADD COLUMN resolved_at TIMESTAMPTZ NULL`.
  - Run `ls alembic/versions/` first to confirm next revision number.
  - **Before committing:** `git diff --staged` to verify content + renames are both staged. (Migration chain corruption pattern bit us once.)
- [ ] Update `SlackInboundMessage` ORM model to expose `resolved_at`.
- [ ] New route `GET /api/slack/signals/mentions`:
  - Query: most recent 20 mentions where `resolved_at IS NULL`, ordered by `ts` DESC
  - Response: `{mentions: [{id, channel_id, channel_name, sender_user_id, sender_name, ts, text, permalink}], total_unresolved: <int>}`
  - Permalink: construct from `team_id` + `channel_id` + `ts` via Slack's URL format (`https://<workspace>.slack.com/archives/<channel>/p<ts_no_dot>`) — find the team subdomain from the active Slack integration's stored config
  - `sender_name` and `channel_name` may need a sub-query against `slack_users` / `slack_channels` if J8 populated those; otherwise return the IDs and let the frontend resolve later
- [ ] New route `POST /api/slack/signals/mentions/{id}/resolve`:
  - Body: optional `{action: "replied" | "ignored"}`
  - Sets `resolved_at = now()` on the row
  - Returns `{ok: true, new_total_unresolved: <int>}`
- [ ] Update existing `GET /api/slack/signals` (J8) so the `missedMentions` count reflects only unresolved rows. Verify the current implementation — if J8 counts all rows in a time window, change it to `WHERE resolved_at IS NULL`.

### Frontend — Focus card becomes a triage queue

The current Focus card lives in `public/js/features/home.js` (or wherever `loadFocusShell` builds the "Needs Your Reply" / "Top Slack Signal" sections — find it via grep for "TOP SLACK SIGNAL" or "Slack mentions").

- [ ] Replace the single "38 missed mentions" tile with a scrollable list. Up to 5 visible by default; "View all" link to expand.
- [ ] Each list item renders:
  - Sender name + channel name + time ago (e.g. "Angela in #brand-design · 12 min ago")
  - Snippet of the message text (first ~120 chars, ellipsis if longer)
  - Three buttons:
    - **Draft reply** — opens Floating Artemis chat seeded with: *"Help me draft a short Slack reply to <sender> in <channel>. They said: '<text>'. Match my voice (concise, direct, lowercase). Don't invent context — if I haven't given you enough info, ask me."* Use the existing Floating Artemis system prompt seeding mechanism (look for `_build_system_prompt` in `artemis/floating_artemis/chat.py`).
    - **Open in Slack** — `target="_blank"` link to the permalink. No backend round-trip.
    - **Mark resolved** — POST to `/api/slack/signals/mentions/{id}/resolve`. On success, optimistically remove the row from the list and update the total count in the section header.
- [ ] If the list is empty (everything resolved), render: *"Slack queue clear. Nicely done."* with no further CTA.
- [ ] The existing "Triage in Chat" button at the top of the section should remain — it should open Floating Artemis chat seeded with: *"Help me triage these Slack mentions. Here are the top 5: <bulleted list with sender / channel / snippet>. Which need a reply now, which can wait, what's the fastest response?"*
- [ ] When Floating Artemis is opened from "Draft reply", the seeded prompt should populate the chat history visibly (so Jon sees what Artemis is working with) — not silently injected. Test by opening the chat and confirming the seed message appears as a system/context message.

### Voice + personality

- [ ] The "Draft reply" prompt MUST use the existing personality profile loader (`artemis/floating_artemis/personality.py` — `PERSONALITY_PROFILE`, `select_voice_samples`). The Floating Artemis system prompt already pulls these; verify that the seeded user message also gets the personality context applied, or wire it through if not.

## Acceptance — what done looks like

- [ ] Focus page Slack section renders a list of 5 recent mentions (not just a count)
- [ ] Sender + channel + snippet visible for each
- [ ] Click "Draft reply" → Floating Artemis chat opens with the seeded context visible in chat history; Jon can see what Artemis is working with
- [ ] Click "Open in Slack" → new tab opens to the actual message in Slack workspace
- [ ] Click "Mark resolved" → row disappears from list; section header count decrements; refreshing the page confirms persistence
- [ ] After resolving everything, the section shows the "queue clear" empty state
- [ ] `GET /api/slack/signals` `missedMentions` count matches the list length
- [ ] Resolution survives app restart
- [ ] Drafted reply via Floating Artemis matches Jon's voice (concise, lowercase, no AI-cheese) — verify by reading one out loud

## Quality acceptance gates

- [ ] Manual smoke output pasted **verbatim**: curl on all three new endpoints + screenshots of the rebuilt section in both populated and empty states
- [ ] Resolution is idempotent — POSTing resolve twice on the same id doesn't error
- [ ] No regression on Focus's other panels (Daily Brief, Calendar, Jira, OKR)
- [ ] `ruff check` + `mypy` clean
- [ ] `git diff --staged` reviewed before each commit
- [ ] Tests: route tests for the two new endpoints (happy + idempotency + 404), migration up/down round-trip, frontend snapshot for the new list rendering

## Out of scope (separate briefs)

- DM ingestion (currently J8 only handles channel mentions) — separate J9b
- Reply-needed thread detection (Slack's RPC for "you have a thread waiting") — separate J9c
- Sending the reply directly from Artemis (vs. drafting in chat) — would need Slack write scopes Jon hasn't granted yet
- Bulk resolve ("mark all older than 7d resolved") — convenience feature, post-MVP

## Where to start

1. Read this brief twice
2. `grep -n "slack_inbound_messages\|missedMentions" artemis/` to map J8's existing code
3. `grep -n "TOP SLACK SIGNAL\|loadFocusShell\|needsReply" public/js/features/` to find the Focus section
4. `ls alembic/versions/` to confirm next revision number
5. Backend first (migration → routes), frontend second
6. Run the app locally and click each button end-to-end before reporting done
