# CCA12 — Replace the modal with an "Edit in doc" button

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`, "Bridging the two teams' workflows".
Everything through CCA11 is merged and LIVE.

## Why

`Request changes` opens a modal asking the approver to type what needs changing. The
vendor's team then told us they want the opposite (Steffie Cruz, DigiGeeks, 2026-08-12):

> Our preference is the team providing suggested edits directly in the document so we can
> move to approval faster.

Confirmed by real use the same morning: Angela hit the card, could not tell where she was
meant to edit, pasted a full rewrite into the Slack thread instead, and eventually went and
made the edits in the doc by hand. The modal is friction pointing the wrong way.

Jon's call: the second button should **take them to the doc**, not ask them to describe a
change.

## The mechanism — verified, not assumed

A Block Kit button with **both** `url` and `action_id` does both things. From Slack's own
docs for the button element's `url` field:

> "If you're using `url`, you'll still receive an interaction payload and will need to send
> an acknowledgement response."

So one tap opens the doc in their browser **and** delivers us an interaction. We keep the
decision record without asking anyone to type prose.

## What to build

Replace `Request changes` with:

| Text | `action_id` | Extras |
|---|---|---|
| `Edit in doc` | `crisis_content_edit_in_doc` | `url` = the doc, deep-linked to the review tab |

- **Deep-link the tab.** Docs has no per-row anchor (established early and unchanged), but
  `?tab=<tabId>` lands them on the review tab instead of the top of a four-tab document.
  Take the tab id from the card's own location rather than hardcoding it.
- Keep `Approve` exactly as it is, including its `action_id`.
- **Delete the modal entirely** — `views.open`, the `view_submission` handler, the
  `private_metadata` plumbing, and `CRISIS_CONTENT_VIEW_CALLBACK_ID`. Remove the tests that
  covered it rather than leaving them asserting dead behaviour.

## On click

1. Record a `changes_requested` decision with `note = NULL`, attributed to the verified
   payload's `user.id` as always. Same authorization check, same route rules.
2. Repaint the card to `✏️ <@U…> is editing in the doc · <time>`, buttons gone. The card
   returns automatically when the copy actually changes — CCA9/CCA11's reopen already does
   that, and this is why removing the buttons is safe.
3. Reply once in the card's thread inviting what a suggestion cannot express, e.g.
   `Opened for edits. If something isn't a specific wording change — a question, or the
   whole angle — drop it here.` Thread capture (CCA9) already records replies.

## Do NOT notify Jen on this click

Today a `changes_requested` decision schedules the write-back: a doc line, a Drive
`@mention`, and an email. If `Edit in doc` inherits that, **Jen is pinged the instant
someone taps the button — before a single edit exists.** She would open the doc to find
nothing changed.

So this decision must **not** schedule the write-back. In the doc-editing workflow the
document itself is the message: Jen sees the edits where she is already working, and the
eventual approval still notifies her normally.

That is a deliberate reduction, not an oversight — record it in the code so nobody
"restores" it. If Jen later proves to need an explicit nudge, that is the suggestion-
detection slice, batched and suppressed when a human already pinged her.

## Out of scope

Suggestion detection, the Writing Studio harvest, changing `Approve`'s behaviour, and any
change to the card body.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_decisions.py tests/test_crisis_content_voice.py tests/test_crisis_content_lifecycle.py tests/test_slack_interactivity.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Use `artemis_test_b`. Both env vars are required; worktrees have no `.env`.

## Tests (all required)

- [ ] The card renders `Edit in doc` with **both** a `url` and an `action_id`.
- [ ] The `url` carries the review tab's `?tab=` parameter, taken from the card, not
      hardcoded.
- [ ] Clicking it records exactly one `changes_requested` decision with `note is None`.
- [ ] It does **NOT** schedule the write-back — assert the scheduler was not called. This
      is the one that stops Jen being pinged before any edit exists.
- [ ] The card repaints to the "is editing in the doc" state with no buttons.
- [ ] One thread reply inviting context; a second click does not produce a second.
- [ ] Authorization is unchanged: an unlisted user is refused, and the refusal does **not**
      modify the card (`replace_original: false` — this regressed in production once).
- [ ] Route rules unchanged: Jon refused on `asset`… (he is allowed on both today; assert
      Angela is refused on `asset`).
- [ ] No `views.open` call and no `view_submission` handling remains anywhere.
- [ ] `Approve` still behaves exactly as before — assert against the existing expectations.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste the rendered card so Jon can read it before it ships.
- [ ] Confirm the write-back is not scheduled on this action, and say how you proved it.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] Nothing written to any Google Doc by this slice.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Four workers on this
      pipeline have each surfaced a real problem that way, and two of the bugs they did not
      catch reached production — so a flag is worth more here than a clean report.
