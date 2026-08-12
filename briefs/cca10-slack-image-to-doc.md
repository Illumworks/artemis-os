# CCA10 — Link a thread-attached image into Jen's doc

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4-mini` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`.
**Depends on:** CCA9 (thread capture) merged — you extend its `has_attachment` handling.

## What you are building

Someone drops an image into the thread on Callie's card. Callie inserts a link to that
Slack message into the post's card in Jen's Google Doc, so the doc points at the visual.

That is the whole slice. **No image bytes move anywhere.**

## Why this shape — an earlier draft of this brief was much bigger

The first version uploaded the file to Drive and embedded it in the doc with
`insertInlineImage`. Jon chose the link instead, and it is the better trade:

- **No new Slack scope.** Callie has no `files:read`, so she cannot download an attachment
  at all. `chat.getPermalink` works with her **existing** scopes — verified live against a
  real message in `C0BM9TL63TL`.
- **No Drive upload, no Google image fetch.** `insertInlineImage` needs a URI Google's
  servers can retrieve; Slack's `url_private` needs a bearer token, which is what forced the
  Drive detour.
- **A text insert with a hyperlink is the same low-risk operation CCA7 already performs
  safely.** No image sizing inside a table cell whose layout we do not control.
- Clicking through shows the image **in context**, with whatever discussion surrounds it.

**Known limitation, do not try to solve it here:** a Slack permalink only opens for someone
with access to that channel. Jen has it via Slack Connect and the team has it, but the doc
is link-editable by anyone with the URL, and an outside viewer would hit a dead link. If
that ever matters, the Drive path returns — the reasoning is in this file's git history.

## The chain

1. CCA9 already records a thread note with `has_attachment` and `message_ts`. Trigger on a
   note whose `has_attachment` is true.
2. `chat.getPermalink(channel, message_ts)` → the permalink. Verified working with Callie's
   current token.
3. Insert a line into that post's card in the doc, reusing CCA7's `locate_card_table` and
   index logic:

   ```
   🖼 Asset in Slack — posted by Angela, Aug 11 8:52pm: <permalink>
   ```

4. Reply once in-thread confirming it is now linked in the doc.

Use the attachment's own message ts for the permalink, **not** the parent card's — the link
must land on the reply that carries the image.

## Safety — CCA7's rules still apply

- **Insert only.** No delete or replace requests.
- **Never write to a card you have not positively identified.** `locate_card_table` raising
  means log ERROR, alert Jon, write nothing. Doing nothing is always correct.
- **Verify after writing** — re-read and confirm the card count is unchanged.
- **Idempotent per thread note.** The same attachment must never produce two lines. Extend
  CCA7's delivery-ledger pattern, keyed on the thread note id.

## Multiple images

If several images are attached across several replies, each gets its own line. Do not try
to collapse them — the doc reading as a chronological list of what was posted is correct
and matches the append-only spirit of everything else here.

If one message carries several files, that is still **one** permalink (the link is to the
message, not the file), so insert one line and say "3 images" in it.

## Out of scope

Downloading any file, Drive uploads, `insertInlineImage`, adding any Slack scope, Writing
Studio harvest, and changing the card body or buttons.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest tests/test_crisis_content_image_link.py -q -p no:randomly
uv run ruff check artemis/crisis_content alembic/versions
uv run mypy artemis/crisis_content
```

Both env vars are required and differ deliberately; worktrees have no `.env`. Mock Slack and
Docs in unit tests — do **not** hit the live doc from the test suite.

## Tests (all required)

- [ ] Thread note with `has_attachment` → permalink fetched, one line inserted, one
      in-thread confirmation.
- [ ] Same note processed twice → **one** line (idempotency).
- [ ] Thread note **without** an attachment → nothing inserted.
- [ ] Two attachments in two replies → two lines, in order.
- [ ] One reply with three files → **one** line, wording reflects the count.
- [ ] `chat.getPermalink` fails → no doc write, no ledger row, retry still possible.
- [ ] Card not locatable → nothing written, ERROR logged, Jon alerted.
- [ ] Post-write verification catches a changed card count and alerts.
- [ ] The permalink targets the reply's ts, not the parent card's.
- [ ] Correct tab on a multi-tab doc.

## Live smoke — ask first

This writes into a real vendor document. **Check in with the human running you before
running it.** When cleared: attach an image to a thread on the `August XX, 2026 - TBD`
placeholder card, and paste the permalink, the inserted line, and evidence the card count is
unchanged.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Live smoke pasted, or an explicit statement of what is unproven.
- [ ] No delete/replace request anywhere in the diff; no file download; no Drive call.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on this
      pipeline has surfaced a real problem that way; one caught a bug that would have
      silently lost approvals forever, another caught a card-matching instruction of mine
      that could never have worked.
