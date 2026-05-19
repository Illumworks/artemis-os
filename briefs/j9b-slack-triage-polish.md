# J9b — Slack triage panel polish: human names, real previews, direct-mention filter

**Owner:** Worker (Sonnet)
**Scope:** ~200 LOC backend + ~100 LOC frontend. Half-day.
**Depends on:** J9 (already merged). Builds on top of `slack_inbound_messages` + the existing triage panel.
**Blocks:** Nothing. Polish.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

The J9 triage panel ships with five UX defects that make it unusable as an actual reply queue:

1. **Sender shows as Slack user ID** (e.g. `U0AMNKUGXLP`) — needs human-name resolution
2. **Channel shows as channel ID** (e.g. `#D0B4EQ175FD`) — needs human-name resolution
3. **Message text preview is missing / garbled** — currently rows show user/channel IDs but not enough of the actual message to know what needs a reply
4. **`@channel` / `@here` group pings are counted as personal mentions** — Jon wants the queue filtered to true `<@USERID>` direct mentions where he was specifically called out, not broadcasts
5. **"Open in Slack" button is vertically off-center** — riding high relative to "Draft reply" and "Mark resolved"

Plus general density / repetition — same user+channel header repeated for every consecutive mention creates visual noise that hides the actual content.

Quote from Jon: *"this panel needs to be cleaned up and optimized for non machine use.... good UX needs to be front of mind."*

## Scope

### Backend — A. User + channel name resolution

The Slack API returns IDs in events; you have to call `users.info` and `conversations.info` to get human names. These should be cached locally so we don't hammer the API.

- [ ] Check if tables `slack_users` and `slack_channels` already exist (they may have been added by an earlier J5/J8 slice — grep). If they exist, use them. If not, add them via alembic migration (next sequential revision; `git diff --staged` before commit):

  ```sql
  CREATE TABLE slack_users (
    id            TEXT PRIMARY KEY,           -- e.g. "U0AMNKUGXLP"
    name          TEXT NOT NULL,              -- "Angela Smith" or display_name
    real_name     TEXT,
    is_bot        BOOLEAN NOT NULL DEFAULT false,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  
  CREATE TABLE slack_channels (
    id            TEXT PRIMARY KEY,           -- e.g. "D0B4EQ175FD"
    name          TEXT NOT NULL,              -- "brand-design"
    is_im         BOOLEAN NOT NULL DEFAULT false,
    is_private    BOOLEAN NOT NULL DEFAULT false,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  ```

- [ ] Add helpers in `artemis/integrations/slack/` (or wherever Slack client lives):
  - `resolve_user(user_id) -> SlackUser` — cache-first; calls `users.info` if missing or stale (>7 days)
  - `resolve_channel(channel_id) -> SlackChannel` — same pattern with `conversations.info`
  - Both are async, both handle 404 (user/channel deleted) gracefully by returning a stub with `name = id` so the UI doesn't break.

- [ ] In the `/api/slack/signals/mentions` response, replace raw IDs with resolved names:

  ```json
  {
    "mentions": [
      {
        "id": 42,
        "sender": {"id": "U0AMNKUGXLP", "name": "Angela Smith"},
        "channel": {"id": "D0B4EQ175FD", "name": "brand-design", "is_im": false},
        "ts": "1715890234.000100",
        "text": "Copy and go. It's formatted well enough for legal as-is. If you want a header added (Amira internal, date, your name as...",
        "permalink": "https://amiralearning.slack.com/archives/D0B4EQ175FD/p1715890234000100"
      }
    ],
    "total_unresolved": 36
  }
  ```

  Frontend gets everything it needs in one call.

### Backend — B. Direct-mention filter

Look at how J9 currently counts `missedMentions`. Find the SQL/ORM query in `artemis/routes/slack/` or wherever signals are aggregated. The current count likely includes any message whose text contains a recognized mention pattern. The bug: `<!channel>` and `<!here>` are Slack's special tokens for `@channel`/`@here`; the current count probably catches messages containing those too.

- [ ] Add a column or filter that distinguishes mention type. Easiest: add a `mention_type TEXT` column to `slack_inbound_messages` with values:
  - `direct` — the message text contained `<@JON_USER_ID>` specifically
  - `channel` — contained `<!channel>` or `<!here>`
  - `group` — contained `<!subteam^GROUPID>` (user group mentions)
  - `keyword` — caught by Slack's keyword-trigger feature
  
  Backfill: for existing rows, parse the message text and tag accordingly. Default new ingestion to compute the field at insert time.

- [ ] Update both `GET /api/slack/signals` (J8's `missedMentions` count) and `GET /api/slack/signals/mentions` (J9's list) to filter `WHERE mention_type = 'direct'` by default. Optionally support `?include=direct,channel,group` query param so the panel can later show non-direct mentions if the user wants the full picture.

- [ ] Jon's Slack user ID is in the Slack integration's stored config — fetch it once on app startup and cache. Document where.

### Frontend — C. Triage panel rebuild

The current panel is at `public/js/features/home.js` (or wherever J9 lives — grep for `Needs Your Reply` / `TOP SLACK SIGNAL`). Three changes:

- [ ] **Use resolved names in the header row.** Replace `U0AMNKUGXLP · #D0B4EQ175FD · 21h ago` with `Angela Smith · #brand-design · 21h ago`. If `channel.is_im === true`, render as `Angela Smith · DM · 21h ago` (no `#` prefix).

- [ ] **Group consecutive mentions from the same sender + channel** into a single card with sub-rows for each message. Reduces the "U0AMN... U0AMN... U0AMN..." visual noise pattern from the screenshot. Example:

  ```
  ┌─ Angela Smith · #brand-design · 21h ago ──────────────┐
  │  • Copy and go. It's formatted well enough for legal… │
  │      [Draft reply]  [Open in Slack]  [Mark resolved]  │
  │  • New message confirmed. Brief is ready in this thr… │
  │      [Draft reply]  [Open in Slack]  [Mark resolved]  │
  │  • Still looping. Quiet until something new arrives.  │
  │      [Draft reply]  [Open in Slack]  [Mark resolved]  │
  └────────────────────────────────────────────────────────┘
  ```

  Sender + channel + relative time appear ONCE per card. Each message gets its own action row.

- [ ] **Fix button vertical alignment.** All three buttons (`Draft reply`, `Open in Slack`, `Mark resolved`) should be vertically centered on the same baseline. The "Open in Slack" link styling in the screenshot is riding high — probably because it's an `<a>` rendered inline with `<button>` elements without matching line-height. Either wrap it in a button-styled span or set `display: inline-flex; align-items: center;` on the action row container.

- [ ] **Message preview should be the first 120 chars of `text`** with ellipsis. Hover or expand to see the full message. Don't truncate at a smaller width — Jon needs enough context to decide if it warrants a reply without opening Slack.

- [ ] **Remove the trailing "36 missed mentions" card** at the bottom of the panel — it duplicates the section header count, and the redundant copy ("Direct mentions are now surfacing here…") is product-marketing voice that doesn't belong in an operational triage UI.

## Acceptance — what done looks like

- [ ] Open Focus → "Needs Your Reply" section shows mentions with human names + real message previews
- [ ] No `U0...` or `#D0...` ID strings visible in the UI
- [ ] `@channel` test: post a message in a test Slack channel with `@channel` mentioning everyone. Verify it does NOT increment the unresolved count.
- [ ] `@jon` test: post a message with `@jon` directly. Verify it DOES appear in the panel within 5 minutes (or whatever the ingestion cadence is).
- [ ] Three consecutive mentions from same sender in same channel render as ONE card with three sub-rows
- [ ] All three action buttons on each row are vertically aligned — pixel-check with a screenshot
- [ ] First-time load resolves all user/channel IDs and caches them; second load is fast (no Slack API roundtrips)
- [ ] Migration up/down round-trip clean

## Quality acceptance gates

- [ ] Manual smoke output pasted **verbatim** including screenshots of: before (current J9 panel from the linked screenshot), after (your rebuild), and the `@channel` filter test
- [ ] `git diff --staged` before every commit
- [ ] `ruff check` + `mypy` clean
- [ ] Tests:
  - Route test for `/mentions` happy path (with name resolution + filter)
  - Mention-type classification unit test (5 cases: direct, channel, here, subteam, keyword)
  - Migration up/down round-trip

## Out of scope (separate briefs)

- Threaded replies (showing parent message context) — separate brief
- Slack search ("show me everything from Angela this week") — separate brief
- Mute / snooze per sender — separate brief
- AI-generated suggested reply text shown inline (vs. opening chat with seeded prompt) — depends on Floating Artemis context handling

## Where to start

1. Read this brief + scan the J9 implementation in `public/js/features/home.js` (search for `Needs Your Reply` and `SLACK MENTIONS`)
2. Check if `slack_users` / `slack_channels` tables already exist; decide migration scope
3. Find Jon's Slack user ID in the active integration's config — grep `JIRA_USER_ID` patterns or similar for precedent
4. Backend first: user/channel resolution + mention_type filter. Test via curl.
5. Frontend: regroup consecutive mentions, swap IDs for names, fix button alignment.
6. Manual smoke in browser before reporting done.
