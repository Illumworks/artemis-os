# CCA13 — Tab resolution: the test lane and the deep link

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`. Everything through CCA12 is merged
and LIVE. Read `artemis/crisis_content/poller.py`, `notify.py`, `transitions.py`.

## What this unlocks

Two things blocked on the same missing fact — which tab a card lives on:

1. **A test lane.** Jon created a tab `Content To Review (TESTING)` and duplicated one of the
   vendor's cards into it. He wants to exercise the whole loop — approve, edit, attach an
   image, watch the reopen fire — without the channel or the vendor seeing anything.
2. **The `?tab=` deep link.** CCA12 shipped `Transition.tab_id` typed, tested, and
   deliberately unpopulated, so `Edit in doc` currently lands on the document root instead of
   the review tab. Its author was right not to guess; this is the follow-up they recommended.

## Verified facts to build on

`documents.get` with `includeTabsContent=true` returns clean tab ids and titles. Confirmed
live on the target doc:

```
t.0               'Strategy Plan'
t.5b63cccie8xp    'Content Plan Draft'
t.jfvhnt5wun8g    'Repeatable Framework'
t.cv99t981gtu6    'Content To Review'
t.cv9uq0oh5hzc    'Content To Review (TESTING)'
```

`artemis/crisis_content/writeback.py::_find_all_card_tables` **already** walks tabs
(recursing `childTabs`) and tags each signature-matching table with its owning tab. Reuse
that rather than writing a second walker.

## Resolve once per tick, not per card

The poller currently fetches only the HTML export. Add **one** `documents.get` call per poll
tick and build a map from card identity → `(tab_id, tab_title)`, then thread it through
`record_observation` into each `Transition`.

One call every two minutes, not one per card. Do not put this call on the render path — the
CCA12 author specifically avoided that because it would add a network dependency and a new
failure mode to the hot notify path.

Match export-derived cards to API-derived tables by the same key the rest of the package
uses: header text plus copy-body hash (**not** platform — platform is a chip and chips are
invisible to `documents.get`; this exact mistake was already made once in the CCA7 brief).

## The test lane

A card is a test card iff its tab title contains a configurable marker, default `TESTING`
(case-insensitive). Add `crisis_content_test_tab_marker` to settings.

| | Test card | Real card |
|---|---|---|
| Destination | DM to Jon | channel + the three approvers |
| `⚠️ Testing` footer | **yes** | no |
| Doc line on decision | **yes**, into the test card | yes |
| Jen `@mention` + Gmail | **NO** | yes |
| Writing Studio harvest | **NO** (later slices must honour this) | yes |

The doc line still writes on purpose: index math against the live document is the single
riskiest unproven thing in this pipeline, and a duplicated card is exactly the right place
to prove it. Suppressing only the vendor-facing notifications means Jen never sees a test.

Expose the test-ness on the `Transition` (e.g. `is_test: bool`) so later slices can honour
it without re-deriving it.

## Failure handling — this is the dangerous part

**If tab resolution fails, do NOT notify anything this tick.** Log an ERROR, alert Jon, and
return.

Reasoning, and do not "improve" it into a fallback: without tab titles we cannot tell a test
card from a real one. Treating unknown as real would post a test card to the channel and
`@mention` the vendor about a fake post — the exact outcome the test lane exists to prevent.
Nothing is lost by skipping: `mark_notified` only records after a successful post, so the
next tick retries. A two-minute delay is strictly better than a wrong-audience post.

Alert on the transition into failure and on recovery, not every tick — the existing debounce
in `poller.py` already does this; reuse it.

## Out of scope

The Writing Studio harvest (CCA14/CCA15), suggestion reading, and any change to the card
body or buttons beyond the `url` gaining `?tab=`.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/ -q -p no:randomly -k "crisis_content or slack_interactivity"
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Use `artemis_test_b`; both env vars are required since worktrees have no `.env`.

## Tests (all required)

- [ ] A card on a `TESTING`-titled tab → `is_test` true; a card on `Content To Review` →
      false.
- [ ] Test card routes to Jon's DM, **never** the channel.
- [ ] Test card keeps the `⚠️ Testing` footer; a real card under live routing does not.
- [ ] Test card's decision **does** write a doc line, and does **NOT** schedule the Jen
      `@mention`/email. Assert the suppression with a spy, not by absence of observable
      effects.
- [ ] `Edit in doc`'s `url` carries `?tab=<the card's own tabId>`.
- [ ] Tab resolution failure → **zero** notifications posted, alert sent, no ledger rows, and
      the next tick retries successfully.
- [ ] Matching is by header + copy hash; a card whose platform chip differs still matches
      (proving platform is not used).
- [ ] Exactly one `documents.get` per tick regardless of card count — assert the call count.
- [ ] A new monthly tab with no marker behaves as a real tab (no configuration needed).

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Confirm the call count is per-tick, not per-card.
- [ ] State plainly what happens when tab resolution fails.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on this
      pipeline has surfaced a real problem that way — including one who refused to fake this
      exact feature rather than ship something that looked done.
