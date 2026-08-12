# CCA14 — Harvest approved copy into Writing Studio

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high

Design doc: `docs/crisis-content-approval-pipeline.md`, "Slice D — harvest approved copy into
Writing Studio". Read that section in full; it records several decisions you must not
re-open.

**Depends on:** CCA13 (tab resolution) merged, because you must not harvest test cards.

## What you are building

When copy is approved, the post becomes a permanent example in Writing Studio, so future
drafting learns from what this team actually ships.

This is the "don't waste the information" slice. Jon's framing: the vendor engagement is
producing 50–150 posts over a couple of months, and reconstructing that corpus afterwards
means re-reading and re-classifying everything.

## Verified state of the target tables — do not build a parallel store

Read 2026-08-11:

| Table | Rows | Contents |
|---|---|---|
| `writing_examples` | 7 | **All** `example_type` `reference`/`template` — glossary, proof pack, claims register. **`channel` is NULL on every row.** |
| `writing_profiles` | 1 | `Amira Marketing Voice` |
| `writing_rules` | 3 | standing guidance |
| `writing_training_candidates` | 41 | 38 `rule`/`proposed` (Angela's queue) |

The gap is precise: **Writing Studio has reference material and rules but zero examples of
finished, approved content.** `writing_examples` already carries `example_type`, `asset_type`,
`channel`, `body` — it is shaped for this and `channel` has simply never been used. Target it.

## Decisions already made — implement, do not relitigate

**A separate `Amira Social` profile.** Create it if absent. The existing
`Amira Marketing Voice` profile is built from whitepapers and enablement docs; `writing_rules`
are profile-scoped, so sharing one profile would leak social conventions ("Link in bio",
hashtags, character limits) into document drafting. Leaves Angela's 38 pending proposals
untouched.

**Every approval is harvested, silently.** No second button, and nothing on the card mentions
storage. Jon overruled an earlier "Approve + save as example" design: the copy clears a
professional vendor plus three reviewers, the whole engagement is only 50–150 posts (filtering
could leave ~20, too thin to teach anything), and curating later with performance data beats
guessing in the moment. Store a `quality` field defaulting to unrated so retroactive curation
needs no migration.

**Capture the final text.** Re-read the copy at harvest time from the card's current state,
not the text captured when the notification fired — it can change in between, and the corpus
must hold what was actually approved.

## Channel fan-out

`Platform` carries multi-platform combos as single opaque values: `FB/IG`, `All`,
`Facebook, IG, X`, `FB, LI, & X`, `Facebook/LinkedIn`, plus `TBD`. Writing the raw string into
`writing_examples.channel` would make a search for "LinkedIn examples" miss a post that was
approved for LinkedIn.

Map to canonical channels and **write one row per channel**:

| Vendor value | Canonical |
|---|---|
| `Facebook`, `FB` | `facebook` |
| `Instagram`, `IG` | `instagram` |
| `LinkedIn`, `LI` | `linkedin` |
| `X` (also `Twitter`, `TWITTER(X)`) | `x` |
| `FB/IG` | `facebook`, `instagram` |
| `Facebook/LinkedIn` | `facebook`, `linkedin` |
| `FB, LI, & X` | `facebook`, `linkedin`, `x` |
| `All` | every canonical channel — define the list explicitly, do not infer |
| `TBD` | none — do not harvest |

Dedup on `(copy_hash, channel)`, not `copy_hash` alone.

**An unrecognized platform value must alert, never be stored as a literal channel and never
be silently dropped.** The dropdown is user-editable; without an alert the corpus rots
quietly as the vendor adds options.

## Hard constraints

- **Never harvest a test card.** CCA13 exposes `is_test` on the `Transition`; the decision
  path must honour it. A test post in the corpus is training data from a fake post.
- **Only `approved` decisions harvest.** Not `changes_requested`, not a reopen.
- **Idempotent.** Re-running for the same decision must not duplicate rows.
- **Do not modify** `writing_rules`, the existing profile, or Angela's candidate queue.
- **Write nothing to the Google Doc** in this slice.

## Open question to answer, not assume

Whether Writing Studio retrieval over `writing_examples` is semantic (needs an embedding on
insert) or a profile-scoped fetch-all. With 7 rows it may well be the latter. **Read the
retrieval code and report what you find**; add an embedding only if retrieval actually uses
one.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_harvest.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Use `artemis_test_b`; both env vars are required.

## Tests (all required)

- [ ] `approved` on a single-platform card → one `writing_examples` row, correct `channel`,
      `example_type`, body, and the `Amira Social` profile.
- [ ] `FB, LI, & X` → **three** rows, one per canonical channel, same body.
- [ ] `TBD` → **no** rows, no crash.
- [ ] Unrecognized platform (`Threads`) → alert raised, **no** row with a literal `Threads`
      channel.
- [ ] `changes_requested` → no rows.
- [ ] **Test card (`is_test`) → no rows.** Assert explicitly.
- [ ] Re-harvesting the same decision → no duplicate rows.
- [ ] The `Amira Social` profile is created once and reused, never duplicated.
- [ ] `Amira Marketing Voice`, `writing_rules`, and `writing_training_candidates` are
      untouched — assert row counts before and after.
- [ ] `quality` defaults to unrated.
- [ ] The harvested body matches the card's copy **at harvest time**, not an earlier snapshot.

## Quality acceptance

- [ ] All commands pass; paste verbatim output; include the migration round-trip if you add one.
- [ ] Paste one harvested row as it lands in the DB.
- [ ] Report what you found about retrieval and whether an embedding was needed.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on this
      pipeline has surfaced a real problem that way, and two bugs nobody caught reached
      production — a flag is worth more here than a clean report.
