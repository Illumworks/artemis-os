# CCA15 — Mine repeated suggestions into candidate style rules

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this proposes standing guidance for
how the company writes, so a wrong rule is worse than no rule.

Design doc: `docs/crisis-content-approval-pipeline.md`.
**Depends on:** CCA13 (tab resolution, so test cards are excluded). Independent of CCA14.

## The finding this exists for

Angela and Hannah went through every one of the vendor's posts in Google Docs Suggesting
mode. Extracted live from the API on 2026-08-12:

```
'kids'      ->  'students'
'child'     ->  'student'
'child'     ->  'student'
'child'     ->  'student'
'About'     ->  'With about'
"'s"        ->  's’'          (straight -> smart apostrophe)
```

`child -> student` appears **three times**, `kids -> students` once. That is not four
examples. **That is one house style rule revealing itself**: Amira says *students*, never
*kids* or *child*.

So the vendor engagement is not only producing content — it is producing this team's style
guide, by observation. A stored example teaches a model implicitly and slowly; an explicit
rule ("prefer *students* over *child*/*kids*") is worth far more, and `writing_rules` already
exists with a human review gate in front of it.

## Verified mechanics — build on these, do not re-derive

Fetch with:

```
GET https://docs.googleapis.com/v1/documents/{id}
    ?includeTabsContent=true&suggestionsViewMode=SUGGESTIONS_INLINE
```

Suggestion markers live on the **`textRun`**, not on the paragraph element:

```
tabs[].documentTab.body.content[].table.tableRows[].tableCells[]
    .content[].paragraph.elements[].textRun
        -> suggestedInsertionIds / suggestedDeletionIds / suggestedTextStyleChanges
```

An earlier probe checked one level too high and found zero — read the run, not the element.
The live doc currently has 144 such nodes across 21 paragraphs.

A replacement is an adjacent **deletion run** and **insertion run** inside the same paragraph.
Walk each paragraph's runs in order, pairing a `DEL` with the neighbouring `ADD`. A run with
neither marker is untouched text and is the context.

**A whole-paragraph deletion with no adjacent insertion is not a replacement** — several exist
in this doc. Those are cuts, not word swaps; do not synthesise a rule from them.

## What to build

1. **Extract** every `(deleted, inserted)` pair from suggestion runs, per card, with the tab it
   came from. Skip cards on a test tab.
2. **Normalise** for aggregation: trim, casefold for comparison but **keep the original casing
   for display**. Ignore pairs that differ only by whitespace or a smart-quote/apostrophe
   substitution — the `'s -> 's’` pair above is a typography artifact of Docs autocorrect, not
   an editorial decision, and proposing it as a house rule would be noise.
3. **Aggregate** identical normalised pairs across the whole doc, and across polls over time —
   a rule that shows up twice today and twice next week is stronger evidence, not two separate
   findings. Persist observations so counts accumulate.
4. **Propose** a rule when a pair's count reaches a configurable threshold (default **3**).
   Write a `writing_training_candidates` row: `candidate_type='rule'`, `status='proposed'`,
   `proposed_text` in the shape of the existing rules, and a `rationale` citing the actual
   count and example cards.

## Never auto-apply

**Nothing here may write to `writing_rules` directly.** These are proposals for the existing
human gate — Angela already has 38 pending, and this feeds the same queue she is used to.

A wrong standing rule silently degrades every future draft, and it would be attributed to the
team rather than to us. Propose, cite the evidence, let a person decide.

Also: **do not propose one-off pairs.** Under the threshold, keep counting and stay silent. A
single edit is a judgement about one sentence, not a rule.

## Attribution

Docs suggestion runs carry ids, not names — resolve who suggested what only if the API gives
it to you cleanly. If it does not, record the pair without an author rather than guessing, and
say so in your report. `author_email` on thread notes is a separate, unrelated path.

## Hard constraints

- **Read-only against the document.** No write, no comment, no accepting or rejecting a
  suggestion. Ever.
- Skip test-tab cards.
- Idempotent: re-running must not re-propose an already-proposed rule, nor double-count the
  same suggestion.
- **Do not modify** `writing_rules`, `writing_examples`, the existing profile, or the 38
  pending candidates.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_rule_mining.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Use `artemis_test_b`; both env vars are required. Build fixtures from the real JSON shape
above — do **not** hit the live doc from the test suite.

## Tests (all required)

- [ ] Three `child -> student` pairs at threshold 3 → **one** proposed candidate, rationale
      citing three occurrences.
- [ ] Two occurrences at threshold 3 → **no** candidate, count persisted for later.
- [ ] Counts accumulate across two separate runs (2 then 1 → proposes on the second run).
- [ ] A whole-paragraph deletion with no adjacent insertion → **no** pair, no rule.
- [ ] `'s -> 's’` (apostrophe only) → filtered out, no candidate.
- [ ] A whitespace-only difference → filtered out.
- [ ] Case is preserved for display but not for aggregation (`Child` and `child` aggregate).
- [ ] Test-tab cards contribute **nothing**.
- [ ] Re-running proposes no duplicate candidate.
- [ ] `writing_rules` is never written — assert row count unchanged.
- [ ] The 38 pending candidates and all `writing_examples` rows are untouched.
- [ ] Suggestion markers are read from the `textRun`, not the paragraph element — a fixture
      with markers only on the element yields nothing (this is the mistake that already
      happened once).

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste the proposed candidate row for the `child -> student` case exactly as it would land,
      so Jon can judge whether the wording is something he'd put in front of Angela.
- [ ] Confirm nothing writes to the document and nothing writes to `writing_rules`.
- [ ] Report whether suggestion authorship was resolvable, and what you did about it.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently — especially if the
      threshold or the filtering rules look wrong to you once you see real data. Every worker
      on this pipeline has surfaced a real problem this way.
