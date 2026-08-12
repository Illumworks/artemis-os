# CCA16 — Mine the span a human edited, not the runs Docs stored

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — the output of this goes in
front of Angela as proposed house style, so a wrong pair is worse than no pair.
**Depends on:** CCA15 (merged, `0112`). Mining is currently **disabled in
production** (`ARTEMIS_CRISIS_CONTENT_RULE_MINING_INTERVAL_MINUTES=0`) pending this fix.

## The finding — from the first live pass, not from a test

CCA15 shipped with 15 passing tests. Its first run against Jen's real doc extracted
six pairs, and four of them are not edits any human made:

| count | deleted | inserted |
|---|---|---|
| 1 | `the` | `Amira ` |
| 1 | `It's a` | `students` |
| 1 | `how it's made.` | *(empty)* |
| 1 | `can't` | ` or ` |
| 1 | `topic` | `. ` |
| 1 | `, can't surprise you. Boring, on purpose.` | ` Predictable, on purpose. ` |

Only the last one is a real editorial decision. `It's a` → `students` and
`can't` → ` or ` are fragments of one rewritten sentence, sliced at Google's run
boundaries and then paired with whatever fragment happened to sit next to them.

**The pairing code is not buggy.** `_paragraph_pairs` only pairs a DEL block with an
immediately-following ADD block (`cur.end == nxt.start`), which is locally correct and
well tested. The mismatch is the *unit*: Docs stores a rewrite as interleaved
DEL/ADD/DEL/ADD runs, while the thing a person decided is the whole rewritten span.
Mining at run level produces fragments; mining at span level produces the edit.

This is the CLAUDE.md pattern again — the tests seeded one deletion beside one
insertion, and production sent a sentence rewritten in place.

## What to change

In `artemis/crisis_content/rule_mining.py`, coalesce each **contiguous cluster of
suggestion activity** in a paragraph into exactly one pair:

- Walk the paragraph's runs. A maximal consecutive stretch of `del`/`add` runs,
  uninterrupted by an untouched (`none`) run, is one cluster.
- That cluster yields **one** pair: all its deleted text concatenated in document
  order → all its inserted text concatenated in document order.
- An untouched run (or a non-`textRun` element) ends the cluster, exactly as it ends
  a block today.
- A cluster with only deletions still yields no pair (the existing whole-paragraph
  deletion rule stands, and `how it's made.` → *(empty)* above must stop being
  recorded). A cluster with only insertions likewise yields nothing.

On the live data this turns the four fragments into one span-level pair per rewritten
sentence, and leaves the genuine `, can't surprise you. Boring, on purpose.` →
` Predictable, on purpose. ` pair intact.

### Why this makes the threshold work rather than just quieting it

A true house rule is a *short* substitution — `child` → `student` is a one-word
cluster, so coalescing changes nothing about it and it still reaches 3 across cards.
A sentence rewrite is a long cluster that is unique to its sentence and will sit at 1
forever. The threshold stops needing to be defended against garbage, because
span-level pairs sort themselves.

## Also required: a length guard

Coalescing is necessary but not sufficient — a long span could still recur (boilerplate
pasted into several cards). Refuse to *propose* (keep counting, stay silent) when
either side of a pair exceeds a configurable word count, default **6 words**. Add it as
a setting beside `crisis_content_rule_mining_threshold`, described the way the others
in `config.py` are.

Rationale to put in the docstring: a standing rule is guidance a writer can hold in
their head. "Prefer X over Y" where X is a whole sentence is not a rule, it is one
edit, and Angela's queue is not where one edit belongs.

## Migration + the rows already recorded

The six live rows in `crisis_content_rule_mining_pairs` (and their occurrence rows) were
produced by the run-level extractor and are not evidence of anything at span level.

Add migration **`0114`** (`down_revision = "0113"`) that **deletes** them. State in the
migration's own docstring why this is not a violation of CLAUDE.md rule 3: these are a
derived aggregate computed by a superseded extractor, not observations or drawers — the
suggestions themselves live in Jen's document and are re-readable on the next pass.
Keeping them would double-count every suggestion under two different pairings.

If you believe deletion is wrong here, say so in your report and leave them; do not
half-do it.

## Hard constraints

- **Read-only against the document.** No write, no comment, no accepting or rejecting a
  suggestion. Ever.
- `writing_rules` is still never written. Proposals only.
- Do not modify `writing_examples`, the existing profile, or the pending candidates.
- Do not touch `notify.py`, `poller.py`, `parser.py`, `writeback.py`,
  `tab_resolution.py`, `harvest.py`, `slack_actions.py`, `pyproject.toml`, or `uv.lock`.
  The poller wiring and its four tests are already correct and are not yours to change.
- Keep the existing test-tab and typography/noise exclusions working.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_rule_mining.py -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Both env vars are required; worktrees have no `.env`.

## Tests (all required)

- [ ] **The live case, verbatim.** Build a paragraph whose runs are the interleaved
      rewrite that produced `the`/`It's a`/`can't`/`topic` above, and assert it yields
      ONE span pair — not four — and that none of the four fragments appears as a pair.
- [ ] A single-word substitution (`child` → `student`) is unchanged by coalescing and
      still reaches the threshold across three cards.
- [ ] An untouched run between two suggestion runs still splits clusters.
- [ ] A deletion-only cluster yields nothing, including the live
      `how it's made.` → *(empty)* case.
- [ ] An insertion-only cluster yields nothing.
- [ ] Over the length guard → counted, never proposed. One word under → proposed.
- [ ] Test-tab and typography exclusions still hold (existing tests must still pass
      unchanged, or explain precisely why one had to change).
- [ ] Idempotency and cross-poll accumulation still hold.
- [ ] `0114` removes the six-row shape and leaves `writing_examples`,
      `writing_rules`, and the pending candidates untouched — assert counts.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste the pairs your extractor produces for the live paragraph, so the unit is
      visibly the span and not the fragment.
- [ ] Say plainly whether any pair you now produce would still embarrass us in front of
      a stakeholder, and if so which.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. This slice
      exists because a fully-tested module was wrong about production on its first real
      read; the tests were not the problem, the assumption about the data was.
