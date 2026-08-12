# CCA10 — Slack image upload → Drive → Jen's doc

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — writes to an externally-owned
document and moves binary data between three APIs.

Design doc: `docs/crisis-content-approval-pipeline.md`.
**Depends on:** CCA9 (thread capture) merged — you extend its file-detection.

## What you are building

Someone drops an image into the thread on Callie's card. It ends up in Jen's Google Doc,
attached to the right post, and available as a real file in Drive.

Jon asked for this explicitly after an earlier recommendation against it. Build it.

## Two prerequisites — check both before assuming failure is your bug

**1. `files:read` on Callie's Slack app.** She does not have it today (verified: her scopes
are `app_mentions:read, canvases:read, canvases:write, channels:history, channels:read,
chat:write, chat:write.public, groups:history, groups:read, im:history, im:read, im:write,
reactions:read, reactions:write, users:read, users:read.email`). Without it, fetching a
file's `url_private` returns 403. Jon is adding the scope; if your smoke gets a 403, check
the live scope list before debugging your code.

**2. Google must be able to fetch the image.** `insertInlineImage` takes a `uri` that
Google's servers retrieve. Slack's `url_private` needs a bearer token, so Google cannot use
it directly. That is why this goes through Drive.

## The chain

1. **Detect** — CCA9 already records `has_attachment` on a thread note. Extend it to capture
   the file's `id`, `name`, `mimetype`, `size`, and `url_private`.
2. **Download** — GET `url_private` with Callie's bot token (`Authorization: Bearer`).
   Slack returns the bytes. A 403 here means the scope is missing, not a code bug — say so
   in the error.
3. **Upload to Drive** — multipart upload via `files.create`, using the **personal** Google
   credential (`purpose='personal'`, resolved by purpose, **never** a hardcoded user id;
   `user_id=1` is the `dev@local` shim). Put it in a dedicated folder, name it after the
   card (`{date} {title} {platform} — {original filename}`).
4. **Make it fetchable** — set a link-share permission so Google's image fetcher can read
   it. Use a URI form the Docs API accepts for a shared Drive file.
5. **Insert into the doc** — `insertInlineImage` at the card's status cell, scoped to the
   right `tabId`, reusing CCA7's `locate_card_table` and index logic. Then insert a text
   line with the Drive link beneath it.

## Safety — same rules as CCA7, they still apply

This writes to a vendor-owned document that people are editing.

- **Insert only.** No delete or replace requests.
- **Never write to a card you have not positively identified.** `locate_card_table` raising
  means: log ERROR, alert Jon, write nothing. Doing nothing is always correct.
- **Verify after writing** — re-read and confirm the card count is unchanged.
- **Idempotent per Slack file id.** The same upload must never produce two embeds. Extend
  CCA7's delivery-ledger pattern; key on the Slack file id, not the message ts (an edited
  message keeps its ts).

## Docs API image constraints — validate BEFORE inserting

Reject with a clear in-thread reply rather than a failed insert:

- PNG, JPEG, GIF only. Anything else (HEIC from a phone, PDF, SVG) → tell the uploader what
  is supported.
- Max 50 MB, max 25 megapixels.
- A phone screenshot is commonly HEIC. This will happen; handle it as a normal case with a
  friendly message, not an exception.

## Why Drive as well as the embed

Deliberate, not redundancy. An image embedded in a table cell we do not control will
probably size awkwardly — Jon has been told this and wants it anyway. Uploading to Drive
first means that even if the embed looks poor, the **usable full-resolution file exists**
and the doc carries a working link to it. A bad embed must not be a dead end.

## Feedback in the thread

After a successful push, reply once in-thread: what was attached, that it is now in the doc,
and the Drive link. On failure, say what went wrong in plain language (unsupported format,
too large, card not found) — never a stack trace, never silence.

## Out of scope

Writing Studio harvest, changing the card body or buttons, adding any Slack scope yourself,
and touching `authorization.py`.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest tests/test_crisis_content_image_push.py -q -p no:randomly
uv run ruff check artemis/crisis_content alembic/versions
uv run mypy artemis/crisis_content
```

Both env vars are required and differ deliberately; worktrees have no `.env`. Mock Slack,
Drive, and Docs in unit tests — do **not** hit the live doc from the test suite.

## Tests (all required)

- [ ] PNG attachment → downloaded, uploaded to Drive, embedded, link line inserted.
- [ ] Same Slack file id delivered twice → **one** embed, one link (idempotency).
- [ ] `url_private` returns 403 → error names the missing `files:read` scope explicitly.
- [ ] HEIC / PDF / SVG → rejected before any write, friendly in-thread reply.
- [ ] Over 50 MB → rejected before download completes if size is known.
- [ ] Card not locatable → **nothing written**, ERROR logged, Jon alerted.
- [ ] Drive upload fails → no doc write, no ledger row, retry still possible.
- [ ] Doc insert fails after a successful Drive upload → retry does **not** re-upload to
      Drive.
- [ ] Post-write verification catches a changed card count and alerts.
- [ ] Image goes to the correct tab on a multi-tab doc.
- [ ] Google credential resolved by `purpose='personal'`, not a fixed user id.

## Live smoke — ask first

This writes an image into a real vendor document. **Do not run it without checking in with
the human running you.** When cleared, use the `August XX, 2026 - TBD` placeholder card, and
paste: the file uploaded, the Drive link, the inserted text, and evidence the card count is
unchanged.

If `files:read` is not yet live, say plainly that the smoke is blocked on the scope rather
than implying the path is verified.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Live smoke pasted, or an explicit statement of what is blocked and unproven.
- [ ] No delete/replace request anywhere in the diff.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] State what happens on each failure mode: bad format, too big, card missing, 403.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on this
      pipeline has surfaced a real problem that way; one caught a bug that would have
      silently lost approvals forever, another caught a card-matching instruction of mine
      that could never have worked.
