# ARGUS-2 — Connect districts to the sources that already know them

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — a wrong mapping puts one
district's board minutes in another district's dossier, which is worse than an
empty dossier because nobody would catch it.

## The finding

Argus's three weakest dimensions are **current_vendor**, **decision_makers** and
**competitor_commitments**. All three are fed by `board_minutes`, which returns 0 items
for essentially every district. Measured across ten real runs on 2026-08-12:

| dimension | produced content |
|---|---|
| current_vendor | **1 of 9** |
| procurement_timing | **1 of 9** |
| competitor_commitments | **1 of 10** |
| recommended_angle | **0 of 10** (synthesised from the others, so it fails with them) |

`_fetch_board_minutes` (`artemis/argus/research.py`) needs a `boarddocs_url` and reads it
only from the signal (`signal["provenance"]["boarddocs_url"]` or `signal["boarddocs_url"]`).
Signals do not carry one. So it logs "no boarddocs_url -- skipping" and returns `[]`, every
time.

**The URLs already exist.** `artemis/scouts/board_minutes/peer_scout.py` hardcodes **27** of
them. They are keyed by a hand-written id (`TX_dallas`, `IN_msd_pike`, `MD_prince_georges`)
which is a **different key space** from anything Argus or `signal_queue` uses — the
`districts` table has 13,466 rows keyed by numeric `id`, plus `nces_id`, `name`, `state`.
Nothing joins the two.

**Six of the ten districts researched on 2026-08-12 already have a URL in that list** and got
nothing anyway:

| researched district | drawer key | existing peer_scout id |
|---|---|---|
| Dallas ISD | `11331` | `TX_dallas` |
| MSD Pike Township | `3399` | `IN_msd_pike` |
| Prince George's County | `4612` | `MD_prince_georges` |
| St. Louis Public Schools | `St. Louis Public Schools` | `MO_st_louis` |
| Kansas City | `6165` | `MO_kansas_city` |
| Elgin Area U-46 | `IL-U46` | `IL_elgin_u46` |

And the source is live — verified by hand the same day:
`fetch_boarddocs({'district_id':'TX_dallas','boarddocs_url':'https://go.boarddocs.com/tx/disd/Board.nsf/Public'})`
returned **146 agenda items**. (Ignore older `blocked egress: cannot resolve host
go.boarddocs.com` lines in `app.err.log`; DNS resolves fine now and those are historical.)

So this is a **mapping problem, not a data-acquisition problem.** One join is standing between
Argus and the commercially sharpest half of every dossier.

## What to build

1. **Persist the mapping.** Add `boarddocs_url` (and see item 4) to the `districts` table —
   migration **`0117`**, `down_revision = "0116"`. Back-fill the 27 known URLs by matching
   `peer_scout`'s entries to `districts` rows.
   - **Match on `nces_id` or an exact `name` where you can, and on nothing fuzzy.** A
     `state` + partial-name match will happily attach Dallas ISD's board to Dallas County
     Schools. Where a `peer_scout` entry cannot be matched with confidence, **leave it
     unmapped and list it in your report** — an unmapped district produces the same empty
     dimension it does today, which is safe. A mis-mapped one publishes another district's
     procurement plans into a dossier Josh acts on.
   - `peer_scout.py` should keep working exactly as it does; it is a live scout. Read its
     list, do not restructure it.
2. **Make Argus use it.** `_fetch_board_minutes` should fall back to the district's stored
   `boarddocs_url` when the signal does not carry one. Resolve it the same way
   `_resolve_search_term` resolves the name (same seam, same key), so both lookups agree
   about which district a key means.
3. **USASpending.** `_fetch_usaspending` returns 0 items even with a state, because it needs a
   recipient identity rather than a search string. Investigate what it actually needs
   (`artemis/tools/usaspending.py`, `_build_recipient_locations`, the CFDA filters) and either
   wire the district's real recipient name / NCES id into the query, or **report that it needs
   something we do not have and leave it alone**. Do not leave it half-wired.
4. **Say what you added and why** in the migration docstring, including that
   `districts.boarddocs_url` is nullable and that NULL is a normal, permanent state for most
   of 13,466 rows.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_a uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_a uv run pytest artemis/argus/tests -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Use `artemis_test_a`. Both env vars are required; worktrees have no `.env`.

## Tests (all required)

- [ ] The back-fill maps Dallas ISD to `go.boarddocs.com/tx/disd/...` and **not** to any other
      Texas district. Assert the specific row.
- [ ] A district with no stored URL still yields `[]` from `_fetch_board_minutes` and does not
      raise.
- [ ] A signal-supplied `boarddocs_url` still wins over the stored one.
- [ ] The stored URL is used when the signal has none — the case that is broken today.
- [ ] `_fetch_board_minutes` and `_resolve_search_term` resolve the same district key to the
      same district row.
- [ ] Ambiguous `peer_scout` entries are left unmapped rather than guessed.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] **Live proof, and this is the deliverable:** re-run Argus for Dallas (`11331`) through
      the real claimer and show `current_vendor` / `decision_makers` /
      `competitor_commitments` producing content where they said "Insufficient data" before.
      Paste the before and after findings.
      - Note: `research_district` skips dimensions that are "present and fresh", so Dallas
        will not re-research without intervention. Supersede the stale observations
        (`superseded_by` — CLAUDE.md rule 3 forbids DELETE) rather than deleting them, and
        say what you did.
- [ ] How many of the 27 mapped, how many you left unmapped, and why for each.
- [ ] Whether USASpending is now wired or deliberately untouched, with reasoning.
- [ ] Production `argus_research_requests` row count before and after your run.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than building to it silently. Argus spent
      five weeks reporting work it never did; this package earns trust by being checkable.
