# DIST1 — District entity + NCES tier classification (Stream 1, foundational)

**Paste-into:** Codex OR terminal-Lead worker — well-specified backend substrate, no novel reasoning.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `low`. Fully-specified mechanical work (schema + pure function + exact tests); no design judgment left open. Bump to `medium` only if the migration chain or test wiring needs a second pass.
**Target branch:** `worker/dist1-district-entity`
**Browser smoke owner:** Lead, post-merge (verify recompute endpoint + classifier function over fixture data).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~400 (migration + 2 models + pure classifier + repository + loader + tests).
**Priority:** HIGH — foundational. Layer 1 of `docs/campaign-initiation-and-district-design.md`. Everything in the campaign-initiation stream consumes this. Read that design doc first.

---

## Why this exists

The marketing workflow qualifies and approves signals with **no idea how big the district is** — yet district size is a hard business filter (Amira does not currently serve D4 / smallest districts). District is not modeled as an entity at all: `signal_queue.district_id` / `.state` are 100% NULL, and there is no districts table. This brief makes **District a first-class, classified entity** so qualification and the Gate 1 card can consume tier.

**Hallucination-free by construction (the stated bar):** size is NEVER guessed by an LLM. Enrollment comes from the authoritative NCES dataset (loaded as reference data), and tier is a **pure deterministic function** of enrollment against editable bands. This brief builds that machinery + the loader contract + a fixture. (Sourcing the real full NCES CSV is a separate data step the Lead owns — see "Out of scope.")

---

## Locked decisions this brief implements

(Full context in `docs/campaign-initiation-and-district-design.md`.)

- **D-1:** District is a first-class entity (`districts` table). Lossless — never deleted; reused across signals.
- **D-2:** Classification = enrollment lookup + pure-function tier bands. No LLM in the sizing path.
- **D-3:** Tier bands are **global, DB-backed config**, seeded here, edited later (DIST2) in the Signal Playbook.
- **D-4:** D4 = **soft flag** — `supported=false`, but the row is kept and reaches downstream; never deleted.
- **D-5 (locked, editable):** Bands **D1 ≥ 25,000 · D2 10,000–24,999 · D3 5,000–9,999 · D4 < 5,000**.

**IMPORTANT reconciliation:** the old master-plan locked decision **D4 — `district_marketing_flags` table** (task #77) is **subsumed by this `districts` table.** Its "per-district flag" intent is covered by `districts.supported` + `districts.on_skip_list`. **Do NOT create a separate `district_marketing_flags` table.** If you find references expecting one, route them to `districts`.

---

## Scope

### Part A — Migration 0054 (down_revision = "0053")

Two tables.

**`districts`:**
```
id                    bigserial primary key
nces_id               text unique            -- authoritative NCES LEA id; null until resolved
name                  text not null          -- canonical district name
state                 text                   -- 2-letter postal code
enrollment            integer                -- student count from NCES; null if unresolved
tier                  text                   -- 'D1'|'D2'|'D3'|'D4'; null if enrollment null
                                              --   CHECK (tier IS NULL OR tier IN ('D1','D2','D3','D4'))
supported             boolean not null default true   -- false for currently-unsupported tiers (D4)
on_skip_list          boolean not null default false  -- spec §4.1 hard skip
classification_source text not null default 'unresolved'  -- 'nces'|'manual'|'unresolved'
                                              --   CHECK (classification_source IN ('nces','manual','unresolved'))
classified_at         timestamptz
created_at            timestamptz not null default now()
updated_at            timestamptz not null default now()
```
Indexes: `idx_districts_state` (state), `idx_districts_tier` (tier), `idx_districts_supported` (supported). The `nces_id` unique constraint is the upsert key.

**`district_tier_bands`** (global config — single logical row, but model it as a small table for editability + history-friendliness):
```
id            bigserial primary key
tier          text not null unique   -- 'D1'|'D2'|'D3'|'D4'  CHECK in those 4
min_enrollment integer               -- inclusive lower bound; null = no lower bound (D4 floor)
max_enrollment integer               -- inclusive upper bound; null = no upper bound (D1 ceiling)
display_order  integer not null
updated_at     timestamptz not null default now()
```

**Seed the 4 band rows in the migration** with the D-5 locked values:
| tier | min_enrollment | max_enrollment | display_order |
|---|---|---|---|
| D1 | 25000 | null | 1 |
| D2 | 10000 | 24999 | 2 |
| D3 | 5000 | 9999 | 3 |
| D4 | null | 4999 | 4 |

### Part B — SQLAlchemy models

Add `District` and `DistrictTierBand` to `artemis/marketing/models.py`, mirroring the existing `TerritoryConfig` model style (around line 402). Module docstring inventory line updated.

### Part C — Pure tier classifier (NO LLM, NO I/O beyond reading bands)

`artemis/marketing/district_classifier.py`:

```python
def classify_tier(enrollment: int | None, bands: Sequence[TierBand]) -> str | None:
    """Pure function. enrollment None -> None. Otherwise return the tier whose
    [min_enrollment, max_enrollment] range contains enrollment. Deterministic,
    total over the seeded bands (they tile 0..inf with no gaps/overlaps)."""
```
- `bands` is the band config (pass the 4 rows in; do not query inside the pure function — keep it testable).
- `supported` derivation: a separate tiny helper `is_supported(tier: str | None) -> bool` returning `tier != "D4"` (D4 is the only unsupported tier today; centralize so reopening D4 is a one-line change). Document the D-4 soft-flag intent.

### Part D — Districts repository

`artemis/marketing/repository.py` add:
- `async def upsert_district(session, *, nces_id, name, state, enrollment, on_skip_list=False, source) -> District` — upsert by `nces_id` (or by `name`+`state` when `nces_id` is null/unresolved). Computes `tier` via `classify_tier` using the current bands, sets `supported` via `is_supported`, stamps `classified_at` + `classification_source`. Lossless: updates in place, never deletes.
- `async def get_district(session, district_id) -> District | None`
- `async def get_tier_bands(session) -> list[DistrictTierBand]` (ordered by display_order)
- `async def recompute_all_tiers(session) -> int` — re-run `classify_tier` + `is_supported` over every district using current bands; return count updated. (This is what DIST2's "recompute" button will call after a band edit.)

### Part E — NCES loader contract + fixture (machinery only)

`artemis/marketing/nces_loader.py`:
```python
async def load_districts_from_csv(session, csv_path: Path) -> dict[str, int]:
    """Bulk-ingest NCES district reference data. Expected columns (header row):
       nces_id, name, state, enrollment
    Upserts each row via upsert_district(source='nces'). Skips rows with blank/
    non-integer enrollment (logs a warning + counts them). Returns
    {'loaded': N, 'skipped': M}. Idempotent: re-running updates in place."""
```
- Use a streaming CSV read (`csv.DictReader`); do not load the whole file into memory as objects.
- Validate the header columns up front; raise a clear ValueError listing expected vs found columns if mismatched (self-teaching, H1 style).
- **Provide a small fixture** `artemis/marketing/tests/fixtures/nces_sample.csv` (~8 rows spanning all 4 tiers + one blank-enrollment row) for the tests. Do NOT vendor the real multi-MB NCES file — that's the Lead's data step.

### Part F — Tests

`artemis/marketing/tests/test_dist1_district_entity.py`:
1. `classify_tier` boundaries: 24999→D2, 25000→D1, 9999→D3, 10000→D2, 4999→D4, 5000→D3, 0→D4, None→None. (Exhaustive boundary coverage — this is keystone-class; the band edges are load-bearing.)
2. `is_supported`: D1/D2/D3 True, D4 False, None False.
3. `upsert_district` inserts then updates-in-place on same `nces_id` (no duplicate rows; row count stable on re-upsert).
4. `upsert_district` with enrollment=3000 → tier D4, supported=false, but row persists (lossless soft-flag).
5. `recompute_all_tiers` after mutating a band row reclassifies correctly (insert district at 9000 → D3; widen D3 max to 12000 won't matter, but narrow… — concretely: set enrollment 9000 (D3), change bands so 9000 falls in D2, recompute, assert tier flips to D2 and supported recomputed).
6. `load_districts_from_csv` over the fixture: returns {'loaded': 7, 'skipped': 1} (the blank-enrollment row skipped); all 4 tiers represented; idempotent on re-run (counts identical, no dup rows).
7. `load_districts_from_csv` with a bad-header CSV raises ValueError naming the missing column.

---

## Files owned

- NEW: `alembic/versions/0054_*.py`
- EDIT: `artemis/marketing/models.py` (+District, +DistrictTierBand)
- NEW: `artemis/marketing/district_classifier.py`
- NEW: `artemis/marketing/nces_loader.py`
- EDIT: `artemis/marketing/repository.py` (+5 functions)
- NEW: `artemis/marketing/tests/test_dist1_district_entity.py`
- NEW: `artemis/marketing/tests/fixtures/nces_sample.csv`

---

## Acceptance criteria

1. `uv run alembic upgrade head` → 0054 head. **Paste `alembic current`.**
2. `psql -d artemis_os -c "SELECT tier,min_enrollment,max_enrollment FROM district_tier_bands ORDER BY display_order;"` shows the 4 seeded bands. **Paste.**
3. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_dist1_district_entity.py -v` — all 7 pass. **Paste.**
4. `./scripts/check.sh` passes modulo known-exempt (j5b Jira + b3_consolidation flakes). **Paste summary.**
5. **Lossless invariant:** no DELETE anywhere; `recompute_all_tiers` updates in place. **Confirm in report.**
6. **No `district_marketing_flags` table created** (subsumed by `districts`). **Confirm.**
7. `git diff --stat` + `git log --oneline -1` on the branch. **Paste.**

---

## Hard constraints

- **No LLM in the sizing path.** `classify_tier` is a pure function; the only place a model ever touches districts is name-resolution in DIST3 (a later brief), never enrollment.
- **Pure function stays pure** — `classify_tier` takes bands as an argument; it does NOT open a session.
- **Lossless** — soft-flag via `supported`, never delete; D4 rows persist.
- **Self-teaching errors (H1 pattern)** on the CSV header mismatch.
- **down_revision = "0053"** exactly. No parallel migration in flight (CC12 had none).
- **Local-only git.** Commit on `worker/dist1-district-entity`; Lead merges.

---

## Out of scope (do NOT do here)

- **Sourcing the real NCES CCD file** — Lead owns acquiring + vendoring the trimmed `nces_districts.csv` and running `load_districts_from_csv` in prod. This brief builds the loader + a fixture only. (Reason: avoid hallucinating NCES URLs/column schemas — the real file's shape is verified by the Lead, not guessed by a worker.)
- **Signal → district linkage** — DIST3.
- **Qualifier consuming tier + Gate 1 card** — DIST4.
- **Band-editing UI + recompute button** — DIST2 (this brief exposes `recompute_all_tiers` as the repository function DIST2's endpoint will wrap).
- **Campaign initiation** — Stream 2.

---

## Report-back format

```
DIST1 — District entity report
1. Commit / branch
2. LOC diff per file
3. alembic current (0054) + seeded bands query output
4. Tests added + pass count (esp. #1 boundary coverage + #6 loader idempotency)
5. check.sh summary
6. Lossless + no-district_marketing_flags confirmations
7. Anything surprising — esp. around TerritoryConfig model style, or NCES column assumptions
```
