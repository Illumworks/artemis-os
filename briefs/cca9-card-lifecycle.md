# CCA9 — Card lifecycle: threads, Jen's ping, and the re-approval fix

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high

Design doc: `docs/crisis-content-approval-pipeline.md`. Everything through CCA8 is merged
and the pipeline is LIVE: cards post to `C0BM9TL63TL` with working buttons, write-back to
Jen's doc is enabled.

## Why this slice exists — one of these is a real bug

**The re-approval hole.** Trace the current code: copy hits `Ready` → card posts → an
approver requests changes → buttons vanish → Jen rewrites the copy → **nothing happens
ever again**. Her chip still says `Ready`, so `_evaluate_route` sees no status change and
emits nothing; and even if it did, `crisis_content_notifications` already holds
`(card, 'copy', 'Ready')` so `mark_notified`'s unique constraint would dedupe it. The post
is unapprovable. The only escape is Jen toggling the chip to `Draft` and back, which
nothing tells her to do.

**Thread replies are silently dropped.** Callie's `allowed_channel_ids` is empty, so
`_is_authorized_inbound` returns `False` for anything in `C0BM9TL63TL`. If Angela replies
"looks good!" in thread, Callie says nothing, nothing is recorded, and the post sits
forever while everyone assumes it was handled.

## Migration 0109

Head is `0108`.

**Extend `crisis_content_notifications`** — it is the record of a post, so the posted
message belongs on it:

| Column | Notes |
|---|---|
| `channel_id` | text, nullable (existing rows have none) |
| `message_ts` | text, nullable — the posted Slack message |
| `copy_hash` | text, nullable |

Then **replace** the unique constraint `(card_id, route, status_value)` with
`(card_id, route, status_value, copy_hash)`. Backfill `copy_hash` on existing rows from
`crisis_content_cards.copy_hash` before adding the new constraint, or the migration will
fail on the two live rows. Index `message_ts` — thread lookups hit it on every reply.

**New `crisis_content_thread_notes`** — append-only, never UPDATE/DELETE
(`CLAUDE.md` rule 3):

| Column | Notes |
|---|---|
| `id` | PK BigInteger |
| `card_id` | FK → `crisis_content_cards.id`, not null |
| `route` | text, nullable |
| `slack_user_id` | text, not null — from the verified event |
| `author_email` | text, nullable |
| `text` | text, not null |
| `has_attachment` | boolean, not null default false |
| `message_ts` | text, not null |
| `created_at` | timestamptz, not null |

Unique on `(card_id, message_ts)` so a Slack retry cannot duplicate a note.

Verify the round-trip (`upgrade head` → `downgrade -1` → `upgrade head`), and confirm no
other file claims `0109`. This repo has shipped a broken chain before.

## 1. Drop the redundant footer

Jon's call: the live copy card names the three approvers twice now — once in CCA8's opener,
once in the older `Any one of … can approve.` footer. **Remove the footer** on live copy
cards. The opener carries both who and the any-one-of rule.

Keep the `⚠️ Testing` footers under the `dm_jon` override untouched.

## 2. Record where each card was posted

`post_transition_card` must persist `channel_id` + `message_ts` onto the notification row
it creates. Everything below depends on being able to map a Slack thread back to a card.

## 3. Thread replies: capture, then nudge

When someone replies in a thread whose parent is a known card:

1. Insert a `crisis_content_thread_notes` row (text, author, whether files were attached).
2. Reply once in-thread, warm and brief, pointing at the button — e.g.
   `Got it, noted. Tap Approve above when you're happy and I'll record it.`
3. **Do not infer approval from prose.** "Looks good except the last line" is not an
   approval. The button is the only thing that records a decision.

Only nudge once per thread — if a note already exists for that card, capture silently
without re-nudging. Three replies must not produce three nudges.

**Attachments.** Callie has **no `files:read` scope**, so she cannot download an attached
file. She can see `files[]` in the event. Set `has_attachment` and acknowledge it in the
nudge (`Thanks — I can see the image in the thread`), but do **not** attempt to fetch
`url_private`; it will 403. Do not add the scope in this slice.

**Routing note (Lead handles this, not you):** Callie's `allowed_channel_ids` lives in her
encrypted `integrations` row, not in settings, and must include `C0BM9TL63TL` for these
events to arrive at all. Lead is applying that separately. Build assuming events arrive;
say in your report that you could not verify inbound end-to-end without it.

Keep `listen_channel_messages` **False**. `_should_handle_event` already returns True for
`is_reply_to_agent`, so replies to Callie's own posts flow through while general channel
chatter stays ignored. Do not flip that flag.

Crisis-content thread handling must run **before** the generic conversational agent loop
for these threads — a card reply should get the nudge, not an LLM improvisation.

## 4. @-mention Jen on change requests only

CCA8 added `jen_mention()` and the `crisis_content_jen_slack_user_id` setting
(`U016P00LP08`; `users.lookupByEmail` returns `None` for her because she is an external
Slack Connect user on another team — do not "fix" this into a lookup).

On a `changes_requested` decision, post in the card's thread mentioning Jen, with the note:

```
<@U016P00LP08> — Angela asked for a change on this one:
"tighten the second paragraph"
```

Ready-for-review cards must still contain the plain word `Jen` and **never** her id — CCA8
has a test for that; keep it passing.

If the setting is empty, post the message without a mention rather than a broken `<@>`.

## 5. The re-approval fix

Emit a fresh `copy` transition when **all** of these hold:

- current `copy_status` is `Ready`, and
- the latest decision for `(card, 'copy')` is `changes_requested`, and
- a `crisis_content_copy_versions` row exists with `first_seen_at` **after** that
  decision's `decided_at` — i.e. Jen has actually revised the copy since.

No new column is needed for the detection: the version log and decision timestamps already
carry it. The new `copy_hash` on the notification ledger is what stops the dedupe from
swallowing the re-fire.

A revised post therefore comes back as a new card with fresh buttons. The same logic
applies to the `asset` route — mirror it rather than special-casing copy.

**Do not re-fire on an approved card.** `approved` stays terminal; only
`changes_requested` reopens.

## Out of scope

Drive image fetch/upload (later slice), Writing Studio harvest, adding `files:read`,
changing the card body, and touching `authorization.py`.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest tests/test_crisis_content_lifecycle.py tests/test_crisis_content_decisions.py tests/test_crisis_content_transitions.py tests/test_crisis_content_voice.py -q -p no:randomly
uv run ruff check artemis/crisis_content alembic/versions
uv run mypy artemis/crisis_content
```

Both env vars are required and differ deliberately; worktrees have no `.env`. Ruff reports
~18 pre-existing errors in old `alembic/versions/` files (`0034`–`0088`) — not yours.

## Tests (all required)

- [ ] Live copy card no longer contains `Any one of` — and still names the approvers once,
      in the opener.
- [ ] `dm_jon` override still renders both `⚠️ Testing` footers.
- [ ] Posting a card persists `channel_id`, `message_ts`, and `copy_hash`.
- [ ] Migration backfills `copy_hash` on pre-existing notification rows and the new unique
      constraint applies.
- [ ] Thread reply on a known card → one note row, one nudge.
- [ ] Second and third replies → notes recorded, **no** further nudge.
- [ ] Reply in a thread that is not a card → ignored, no note, no crash.
- [ ] Reply with a file attachment → `has_attachment` true; **no** attempt to fetch
      `url_private`.
- [ ] A reply saying `approved` records **no** decision.
- [ ] `changes_requested` → Jen is mentioned in-thread with the note text.
- [ ] Empty `crisis_content_jen_slack_user_id` → message posts without a broken mention.
- [ ] Re-approval: `Ready` → changes_requested → new copy version → **one** new transition.
- [ ] No new copy version after the change request → **no** re-fire (this is the guard
      against re-pinging every 2 minutes forever — get it wrong and Callie spams the
      channel).
- [ ] `approved` → later copy revision → **no** re-fire (approved stays terminal).
- [ ] Asset route re-fires under the same rule.

## Quality acceptance

- [ ] All commands pass; paste verbatim output plus the migration round-trip.
- [ ] Migration `revision="0109"`, `down_revision="0108"`, no other file claims 0109.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] Nothing written to any Google Doc by this slice.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] State plainly that inbound thread events could not be verified end-to-end without
      Lead's channel-allowlist change, rather than implying they were.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on
      this pipeline has surfaced a real problem that way; one caught a bug that would have
      silently lost approvals forever, and another caught a card-matching instruction of
      mine that could never have worked.
