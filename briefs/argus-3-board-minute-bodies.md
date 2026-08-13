# ARGUS-3 — Read the agenda items, not just their titles

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this adds per-item HTTP
fetches inside a shared timeout budget, so getting the volume control wrong makes
every other Argus source fail alongside it.

## The finding

ARGUS-2 connected districts to their BoardDocs URLs. `board_minutes` went from **0
items every time** to **146 real agenda items** for Dallas. And
`current_vendor`, `decision_makers` and `competitor_commitments` still say
"Insufficient data from available sources."

The reason is one default. `_fetch_board_minutes` (`artemis/argus/research.py`)
calls `fetch_boarddocs(district_cfg, http)` and
`fetch_boarddocs` has `fetch_bodies: bool = False`
(`artemis/scouts/board_minutes/client.py:487`). So Argus receives agenda **titles**
only:

```
Board Meeting Agenda and Notice — Joe Carreón, President, District 8
Board Meeting Agenda and Notice — David W. Carter High School Choir
```

Vendor names, contract values and decision-maker roles live in the item **bodies**.
The synthesis LLM is right to say it cannot tell — it genuinely cannot, from those.

ARGUS-2 correctly declined to flip the flag: `fetch_bodies=True` fetches every
item's detail individually, ~150 extra HTTP calls per district, against
`_TOOL_TIMEOUT_S = 15.0` shared with every other source. That would have made
board minutes time out and taken the working sources down with it.

## The measurement that makes this cheap

Filter first. `artemis/scouts/board_minutes/mapping.py::_is_relevant` already
exists for exactly this judgement. Measured against live Dallas data on 2026-08-13:

| | |
|---|---|
| agenda items returned | **146** |
| titles passing `_is_relevant` | **16** |

And the 16 are the right ones — they read `"Consider and Take Possible Action to
Authorize, Negotiate…"`, i.e. contract and procurement actions. So this is roughly
16 fetches, not 150, and it comfortably fits the existing budget.

## What to build

1. In `_fetch_board_minutes`, fetch the agenda list as now (titles, cheap), filter
   the items with `_is_relevant`, then fetch bodies for **only** the survivors.
   Read `client.py` to find the right seam — it may be a second call, a targeted
   helper, or a small addition to `fetch_boarddocs`. Do not restructure
   `fetch_boarddocs`'s existing contract; `peer_scout` is a live caller.
2. **Bound it explicitly, twice over.** A cap on how many bodies you will fetch
   (a setting, default around 20 — Dallas's 16 should pass untouched) AND a
   sub-budget so body fetching cannot consume the whole `_TOOL_TIMEOUT_S`. If the
   budget runs out, return the bodies you got plus the remaining titles: partial
   real content beats a timeout that yields nothing and starves the other four
   sources.
3. Include the body text in what Argus passes to synthesis, trimmed the way the
   existing code trims (`text[:1500]` today) so one enormous item cannot crowd out
   the rest of the prompt.
4. If `_is_relevant` proves too narrow or too broad on real data, **say so with
   numbers** rather than quietly editing it — it is shared with the board-minutes
   scout and changing it changes that scout's behaviour too.

## Hard constraints

- Do not change what dimensions exist or how findings are stored.
- Do not touch `artemis/crisis_content/*`, `artemis/pipelines/*`,
  `artemis/market_signals/*`, or `artemis/floating_artemis/tools/argus_tools.py`.
- Do not modify `fetch_boarddocs`'s signature defaults in a way that changes
  behaviour for `peer_scout`.
- No new dependencies. No migration expected — say so if you disagree.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_a uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_a uv run pytest artemis/argus/tests -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

`artemis/argus/tests/conftest.py` exists and guards against binding to the LIVE
database — **do not remove it**. Note the known pre-existing mypy error in
`test_argus_core_loop.py` about `Dimension` not being explicitly exported.

## Tests (all required)

- [ ] Only items whose titles pass `_is_relevant` have their bodies fetched —
      assert the count, driven by a stub returning a mixed list.
- [ ] The body cap is honoured when more items than the cap are relevant.
- [ ] When the body sub-budget is exhausted, the already-fetched bodies AND the
      remaining titles are returned, and the call does not raise.
- [ ] A body fetch failing for one item does not lose the others.
- [ ] Body text reaches the synthesis input, trimmed.
- [ ] Zero relevant titles yields the title-only behaviour and does not raise.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] **The deliverable is the before/after on a real district.** Re-research
      Dallas (`11331`) and show `current_vendor`, `decision_makers` and
      `competitor_commitments` moving off "Insufficient data" — or, if they do
      not, say exactly what the bodies contained and why it still is not enough.
      **Do not claim success you cannot show.** This package spent five weeks
      reporting research it never did; an honest "still insufficient, here is
      what came back" is worth far more than an optimistic summary.
      - `research_district` skips dimensions that are present and fresh, so
        Dallas will not re-research without intervention. Supersede the stale
        observations (`superseded_by`, never DELETE — CLAUDE.md rule 3).
      - Known trap found by ARGUS-2: `write_observation` dedupes on content hash,
        so if re-research reproduces byte-identical content you get the
        already-superseded row back and the dimension goes missing entirely.
        Watch for it; report it if it bites.
- [ ] Wall-clock time for one district's board-minutes fetch, before and after.
- [ ] Production `argus_research_requests` row count before and after your run.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than building to it silently.
