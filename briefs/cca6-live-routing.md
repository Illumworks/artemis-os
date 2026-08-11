# CCA6 — Live routing: channel + real approvers

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4-mini` / medium
**Depends on:** CCA5 (approval loop) merged — this changes who receives the card CCA5 made
interactive.

Design doc: `docs/crisis-content-approval-pipeline.md` "Routing".

## What you are building

Today every card DMs Jon alone, with a `⚠️ Testing` footer. The destination is a setting
(`crisis_content_notify_destination = "dm_jon"`) but the other paths were never
implemented — CCA4's worker was explicit that flipping needs code, not just config. This
slice implements them.

## The routing table

| Route fires | Goes to |
|---|---|
| `asset` → `Ready` | Jon, as a DM |
| `copy` → `Ready` | Channel `C0BM9TL63TL`, mentioning Angela, Hannah and Jaclyn |

Copy goes to the channel rather than three DMs because approval is **any one of them** —
they need to see that a colleague already handled it. Three separate DMs would produce
duplicated work and no shared visibility. Asset stays a DM because Jon is the only
approver and a channel post would be noise for everyone else.

Emails are already in config from CCA5's allowlist: `angela.miata@`, `hannah.slater@`,
`jaclyn.wright@`, all `@amiralearning.com`. Resolve to Slack ids with
`lookup_user_by_email` — **never** by listing users and filtering, which paginates and
silently misses people past the first page. Cache the resolutions; do not look up three
users on every tick.

## Settings

Replace the single `crisis_content_notify_destination` string with something that can
express the table above, and keep `dm_jon` working as an override that sends **everything**
to Jon. That override is the rollback: if the channel posts go wrong at 9pm on a Friday,
one setting change restores today's known-good behaviour without a deploy.

Default the setting to the live routing — Jon has asked for full functionality — but make
the override obvious in the settings docstring.

## The footer

The `⚠️ Testing — routed to you only` line must **disappear** when routing is live. Leaving
it on a card that really went to three colleagues would be actively misleading.

On a live copy-route card, replace it with a line naming who can approve, e.g.
`Any one of Angela, Hannah or Jaclyn can approve.` On a live asset card, no footer is
needed — Jon knows he owns visuals.

Keep both testing footers in the code (`TESTING_LINE`, `TESTING_LINE_ASSET`) and use them
whenever the `dm_jon` override is active, so the rollback path stays honest about what it
is.

## Failure handling

- If a mention cannot be resolved for one approver, still post — with the ones that
  resolved — and log a WARNING naming the unresolved email. Do not drop the whole
  notification because one lookup failed. A post that reaches two of three approvers is
  vastly better than silence.
- If the channel post fails entirely, do **not** call `mark_notified` (the next tick
  retries), and alert Jon. That contract is already established in
  `artemis/crisis_content/poller.py` — follow it, do not re-invent it.
- If Callie is not a member of `C0BM9TL63TL`, `chat.postMessage` fails. She was invited,
  but detect and report this specific failure clearly rather than as a generic Slack error.

## Out of scope

Doc write-back, `@mention`ing Jen, Gmail, Writing Studio harvest, the ⭐ reaction, and any
change to the card body or the buttons.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_poller.py tests/test_crisis_content_routing.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Both env vars are required and differ on purpose; worktrees have no `.env`.

## Tests (all required)

- [ ] `copy` route → channel `C0BM9TL63TL`, with all three approvers mentioned.
- [ ] `asset` route → DM to Jon, **not** the channel.
- [ ] `dm_jon` override → everything DMs Jon, and the `⚠️ Testing` footer returns.
- [ ] Live routing → the `⚠️ Testing` footer is **absent**.
- [ ] One approver email unresolvable → still posts, mentions the other two, WARNING logged.
- [ ] Channel post failure → no `mark_notified` row, so the next tick retries.
- [ ] Approver Slack ids are resolved via `lookup_user_by_email`, and resolution is cached
      rather than repeated per tick.

## Live smoke — required

Unit tests are not shipping. With live routing on:

1. Trigger one real copy-route notification and confirm it lands in `C0BM9TL63TL` with the
   three mentions rendering as real Slack mentions (not literal `@angela` text).
2. Confirm the `⚠️ Testing` footer is gone.
3. Paste what was actually posted.

**Ask before firing the live smoke if it would post to the channel where colleagues will
see it.** Prefer replaying an already-notified card into a scratch destination, or coordinate
with the human running you. Do not spam three colleagues to satisfy a checklist.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Live smoke pasted, or an explicit statement of why it was deferred and what remains
      unproven.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] Nothing written to any Google Doc.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] State plainly whether the `⚠️ Testing` footer is present under each setting.
- [ ] Flag anything in this brief you believe is wrong rather than guessing silently.
