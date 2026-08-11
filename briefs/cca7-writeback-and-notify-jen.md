# CCA7 — Slice C: write the decision back and tell Jen

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this is the first slice that
**writes to a document owned by someone outside the company.**

Design doc: `docs/crisis-content-approval-pipeline.md`.
**Depends on:** CCA5 (approval loop) merged — you read its `crisis_content_decisions` rows.

## Read this before anything else

Every slice so far has been read-only against Jen's Google Doc. This one writes to it.

The doc is **owned by an external vendor** (`jen@justrightstrategy.com`), holds live
crisis-communications work, and is edited by people while you are writing to it. A bad
index calculation does not throw — it silently mangles someone else's document. Treat
every write as the riskiest line of code in this repo.

Non-negotiables:

- **Never delete or replace existing content.** Insert only.
- **Never write to a tab or table you did not positively identify.** If the card you are
  targeting cannot be located unambiguously, log an ERROR, alert Jon, and write nothing.
  Doing nothing is always the correct fallback.
- **Verify after writing.** Re-read the doc and confirm your text is present and the card
  count is unchanged. If the card count changed, you damaged the document — alert Jon
  immediately and loudly.

## What you are building

When a decision lands (CCA5 writes a `crisis_content_decisions` row), three things happen:

1. A text line is inserted into that post's card in the Google Doc.
2. A Drive comment `@mention`s Jen so she gets a real Google notification.
3. A backup email goes to Jen via Gmail.

### 1. The doc write

Insert a line into the card, after the status block:

```
✅ Approved — Angela, Aug 11 7:14pm
```
```
✏️ Changes requested — Jon, Aug 11 7:14pm: tighten the second paragraph
```

**You cannot set her dropdown chip.** Verified and documented in the design doc: chips are
opaque to the Docs API in both directions. Her `Copy review` chip will still read `Ready`.
Word the inserted line as a record of the decision, and make it clear the chip is hers to
flip — the two sources of truth must not silently disagree.

Mechanics:

- Use the Docs API `batchUpdate` with `insertText`. Requires the `documents` scope, already
  granted.
- The doc is **tabbed**, and more tabs will appear (organised by month). Any index you
  compute must be scoped to the correct tab — `batchUpdate` locations accept a `tabId`.
  Fetch with `includeTabsContent=true`; note that under that flag there is **no** top-level
  `body` and content lives at `tabs[].documentTab.body`, with nesting via `childTabs`.
- Locate the target card by the same signature the parser uses (a table containing both
  `Platform:` and `Copy review`) plus the card's header text and platform. Do **not** use a
  table index — cards get inserted and reordered.
- Beware: `artemis/google_docs/client.py::import_google_document` does **not** pass
  `includeTabsContent` and its markdown converter ignores tables entirely. Do not build on
  those assumptions; there is a separate tracked bug for that path.

### 2. The Drive comment `@mention`

`comments.create` on the file, mentioning Jen. Requires the full `drive` scope — granted
2026-08-11 and verified (`canComment: true`).

**Jen has two addresses on this doc** and it is not obvious which she watches:
`jen@justrightstrategy.com` (the owner) and `jen@digigeeks.com` (a writer). **Mention
both.** It costs nothing and removes the guess.

### 3. The Gmail backup

Send Jen a short email via `gmail.send` (already in scope): what was decided, by whom, on
which post, with a link to the doc. Belt and braces — if the Docs notification is missed or
filtered, the email lands.

Resolve the Google credential by `purpose='personal'`, **not** by a hardcoded user id.
`user_id=1` is the `dev@local` shim; Jon is `user_id=8`. This has now bitten three times —
see `artemis/proactivity/agency_gate.py::_resolve_personal_gmail_client` for the correct
pattern.

## Idempotency

A decision must produce **exactly one** doc line, one comment, and one email, even if the
handler is retried. Record what has already been delivered per decision row, and check
before each of the three actions independently — a failure on the email must not cause the
doc line to be written twice on retry.

## Out of scope

Writing Studio harvest (slice D), the ⭐ reaction, live routing (CCA6), and any change to
Callie's card or the buttons.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest tests/test_crisis_content_writeback.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Both env vars are required and differ on purpose; worktrees have no `.env`.

## Tests (all required)

Mock the Google APIs in unit tests — do **not** hit the live doc from the test suite.

- [ ] Approved decision → correct line text, inserted at the right tab and card.
- [ ] Changes-requested decision → line includes the note.
- [ ] Target card not unambiguously locatable → **nothing written**, ERROR logged, Jon
      alerted.
- [ ] Retry of an already-delivered decision → no second line, no second comment, no
      second email.
- [ ] Email fails but doc write succeeded → retry sends only the email, does not re-insert
      the line.
- [ ] Comment mentions **both** of Jen's addresses.
- [ ] Post-write verification detects a changed card count and alerts.
- [ ] Tabbed-doc index targeting: a card on the second tab is written to the second tab,
      not the first.
- [ ] Gmail credential is resolved by `purpose='personal'`, not a fixed user id.

## Live smoke — coordinate first, do not freelance

The live smoke writes to a real vendor document. **Ask the human running you before doing
it.** When cleared:

1. Use a **scratch card** if one is available, or the most recently decided real card.
2. Paste the exact text inserted, the comment created, and the email sent.
3. Re-read the doc and paste evidence the card count is unchanged and no existing content
   was altered.
4. If anything looks wrong, say so immediately — do not attempt a cleanup write that could
   compound the damage.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Live smoke pasted, or an explicit statement that it was deferred pending approval and
      exactly what remains unproven.
- [ ] Confirm insert-only: no `deleteContentRange` or content-replacing request appears
      anywhere in the diff.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] State plainly what happens if the target card cannot be located.
- [ ] Flag anything in this brief you believe is wrong rather than guessing silently. The
      four previous workers on this pipeline each surfaced a real problem this way; one
      caught a bug that would have silently lost approvals forever.
