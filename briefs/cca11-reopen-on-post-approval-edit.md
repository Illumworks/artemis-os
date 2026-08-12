# CCA11 — Reopen a post whose copy changed after approval

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4-mini` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`. Everything through CCA10 is merged
and the pipeline is LIVE in production.

## Why

`approved` is currently terminal for a route: once someone approves, nothing reopens it.
That was right when all editing happened before approval.

It is now wrong. The vendor's team asked to work **inside the document** — putting
suggested edits directly in the doc rather than describing them in Slack (Steffie Cruz,
DigiGeeks, 2026-08-12). So copy changing *after* an approval is no longer an edge case; it
is the expected shape of the workflow.

The failure that creates: Angela approves specific words, someone then edits those words,
and the approval on record now refers to text that no longer exists. Nothing flags it. For
crisis communications, where the exact wording is the thing being approved, that is a real
integrity problem, not a nicety.

**Jon's decision (2026-08-12): reopen for approval.**

## What you are building

Extend the existing reopen logic in `artemis/crisis_content/transitions.py`. Today
`_reopened_after_changes_requested` fires only when the latest decision is
`changes_requested`. Add the approved case:

Emit a fresh transition for a route when **all** of these hold:

- current status for the route is `Ready`, and
- the latest decision for `(card, route)` is `approved`, and
- a `crisis_content_copy_versions` row exists with `first_seen_at` **after** that
  decision's `decided_at` — i.e. the copy genuinely changed since sign-off.

The ledger's unique key already includes `copy_hash` (migration 0109), so the re-fire is
not swallowed as a duplicate. **No migration should be needed** — verify that claim rather
than assuming it, and say so in your report.

Rename the helper to reflect that it now covers both cases; a function called
`_reopened_after_changes_requested` that also handles approvals is a trap for the next
reader.

## The card must say why it is back

A re-fired card that looks identical to a first-time card is worse than no card — the
approver has no idea they are re-reviewing something. Mark it:

```
⚠️ Previously approved by Angela on Aug 11, and the copy has changed since.
```

Distinguish the two reopen reasons. A card returning after `changes_requested` is the
expected loop and needs no warning banner; a card returning after `approved` is an
exception and needs one. Do not collapse them into one message.

## Do not reopen on noise

The re-fire must key on a genuine new `crisis_content_copy_versions` row, never on a raw
document re-read. Google's exported hrefs carry `ust`/`usg` tracking params that change on
**every** fetch, which is why `copy_hash` is computed from normalized text — do not
introduce any comparison that reintroduces that instability. Get this wrong and Callie
re-posts every approved card every two minutes into a channel with the vendor in it.

Also: a change to the **asset** status must not reopen the **copy** route, and vice versa.
Routes reopen independently.

## Out of scope

Detecting Google Docs *suggestions* (a separate slice, pending verification that the API
exposes them), the Writing Studio harvest, and any change to the card body beyond the
banner above.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_transitions.py tests/test_crisis_content_lifecycle.py tests/test_crisis_content_voice.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Both env vars are required and differ deliberately; worktrees have no `.env`.

Note: `artemis_test_a` has never been migrated this far and `artemis_test_c` was
schema-drifted earlier today. Use `artemis_test_b`, and if you hit a missing column,
suspect the database before your code.

## Tests (all required)

- [ ] `approved` → new copy version → **one** re-fired transition.
- [ ] `approved` → **no** new copy version → **no** re-fire. Run this twice in a row to
      prove it does not fire repeatedly; this is the guard against Callie spamming the
      channel every two minutes.
- [ ] `changes_requested` → new copy version → still re-fires (existing behaviour intact).
- [ ] Re-fire after `approved` carries the "previously approved" banner, naming the
      original approver and date.
- [ ] Re-fire after `changes_requested` does **not** carry that banner.
- [ ] A copy change reopens only the `copy` route, not `asset`.
- [ ] Two re-fires for the same card require two distinct copy versions — one revision
      produces exactly one re-fire, even across several polls.
- [ ] Existing decision rows survive; the reopen writes no decision of its own.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] State explicitly whether a migration was needed, with the reasoning.
- [ ] The renamed helper has no stale references anywhere.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] Nothing written to any Google Doc by this slice.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on
      this pipeline has surfaced a real problem that way: one caught a bug that would have
      silently lost approvals forever, one caught a card-matching instruction of mine that
      could never have worked, and one caught a contradiction between a brief heading and
      its own mechanism.
