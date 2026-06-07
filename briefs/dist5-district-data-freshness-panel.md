# DIST5 — District data provenance + freshness panel (Signals page)

**Paste-into:** Codex OR terminal-Lead sub-worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. Small migration + loader stamp + one endpoint + a display panel that mirrors the existing DIST2 "District Sizing" panel. No novel design.
**Target branch:** `worker/dist5-district-data-freshness`
**Fires:** after DIST1–DIST3 (all merged). Independent of DIST4 (different files) — can run parallel with DIST4 in a separate worktree.
**Browser smoke owner:** Worker (panel renders with real data), Lead re-verifies.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300.
**Priority:** MEDIUM-HIGH — Jon's explicit ask: make district-data freshness visible so we always know we're on current information.

---

## Why this exists

The district layer is now backed by real NCES CCD data (2024-25, 13,403 districts loaded). But that data goes stale — CCD is an annual release, and an operator making approval decisions should be able to SEE, on the Signals page, what district data is backing the system and how old it is. A visible "loaded 2024-25 · N months ago" badge is the platform's "always use up-to-date information" mechanism: staleness becomes something you notice, not something buried in a script.

This panel lives in the **Signal Playbook surface, next to the District Sizing panel** that DIST2 built (terminal-Lead's note: that panel is now the template for Signal Playbook config sections — mirror it).

Context: `docs/campaign-initiation-and-district-design.md` → "District data: source, freshness, and refresh cadence" section. Source pipeline: `scripts/refresh_nces_districts.py` → `artemis/marketing/data/nces_districts.csv` → `artemis.marketing.nces_loader.load_districts_from_csv`.

---

## Scope

### Part A — Migration 0056: `district_data_meta` (singleton)

Run `uv run alembic current` first (will be 0055); down_revision="0055", revision="0056". Paste it.

A one-row metadata table recording the last load:
```
id            bigserial primary key
source        text not null      -- 'NCES CCD via Urban Institute Education Data API'
school_year   text not null      -- '2024-25'
loaded_at     timestamptz not null default now()
row_count     integer not null   -- districts loaded in this batch
updated_at    timestamptz not null default now()
```
Single logical row (upsert on each load). No seed in the migration — it gets stamped on first load (Part B). If empty, the endpoint/UI show "no district data loaded yet."

### Part B — Loader + refresh script stamp the meta

- Extend `load_districts_from_csv(session, csv_path, *, school_year: str | None = None, source: str = "NCES CCD via Urban Institute Education Data API")` to UPSERT the `district_data_meta` singleton at the end of a successful load: school_year (passed in), source, loaded_at=now(), row_count=loaded count.
- Update `scripts/refresh_nces_districts.py` so the load step passes the school year (derive from `--year`: `f"{year}-{str(year+1)[2:]}"`). The script already knows the year. If the script only writes the CSV (and load is a separate step), document that the loader caller must pass school_year — and update the standard load invocation in the design doc's cadence section.
- **Backfill the current row now:** after this lands, the Lead will re-stamp by reloading OR you include a tiny data migration / one-off that sets the existing load's meta to school_year='2024-25', row_count=(SELECT count(*) FROM districts). Prefer: stamp via the loader on next load; for the already-loaded data, set meta in the migration's data step using the current districts count. (Confirm approach in report.)

### Part C — Endpoint

`GET /api/marketing/signal-criteria/district-data-status` →
```json
{
  "source": "NCES CCD via Urban Institute Education Data API",
  "school_year": "2024-25",
  "loaded_at": "2026-05-31T...",
  "total_districts": 13403,
  "supported_count": 1892,
  "unsupported_count": 11511,
  "tier_counts": {"D1": 284, "D2": 620, "D3": 988, "D4": 11511},
  "months_since_loaded": 0,
  "freshness": "current"   // "current" | "aging" | "stale"
}
```
- tier_counts + supported/unsupported computed live from `districts` (COUNT … GROUP BY tier; supported=tier!='D4').
- `months_since_loaded` from loaded_at. `freshness`: current (<12mo), aging (12–18mo), stale (>18mo) — CCD is annual, so >18mo means a newer school year is almost certainly out.
- If `district_data_meta` empty → return a clear "no data loaded" shape (UI shows empty state, not fake numbers).
- Pydantic response model.

### Part D — UI panel (Signal Playbook)

Add a **"District Data" provenance card** to the Signal Playbook surface, adjacent to the DIST2 District Sizing panel (`public/js/features/marketing-os.js` — find where DIST2 added District Sizing; mirror that placement + style). Show:
- Source line: "NCES Common Core of Data (2024-25) · via Urban Institute"
- "13,403 districts · 1,892 supported (D1–D3) · 11,511 unsupported (D4)"
- Tier mini-breakdown (D1/D2/D3/D4 counts) — small, can reuse the District Sizing visual idiom.
- **Freshness badge:** green "Current · loaded 0 mo ago" / amber "Aging · loaded 13 mo ago" / red "Stale · 19 mo old — newer school-year data likely available. Refresh: `scripts/refresh_nces_districts.py`".
- Empty state if no data: "No district data loaded — run scripts/refresh_nces_districts.py".

api.js wrapper for the endpoint.

### Part E (optional / stretch) — Refresh trigger button

ONLY if time allows and it can be done safely. A "Check for newer data" button that POSTs to a backend endpoint which runs the refresh+load+recompute and returns the new status. Must have: loading state, failure handling (network/API errors → show error, don't corrupt existing data — the loader upserts so a partial failure is non-destructive), and it must call recompute_all_tiers after load. If not done, leave a clear TODO comment + note in the report. The display + staleness badge (Parts A–D) are the must-haves; the button is the stretch.

### Part F — Tests

`artemis/marketing/tests/test_dist5_district_data_status.py`:
1. Endpoint with a stamped meta + seeded districts → correct counts, tier_counts, supported/unsupported.
2. Freshness thresholds: loaded_at 0mo → "current"; 14mo → "aging"; 20mo → "stale".
3. Empty meta → "no data loaded" shape (no crash, no fabricated numbers).
4. Loader stamps district_data_meta on load (school_year + row_count correct).

---

## Files owned

- NEW: `alembic/versions/0056_*.py` (district_data_meta + data-stamp for current load)
- EDIT: `artemis/marketing/nces_loader.py` (stamp meta; school_year param)
- EDIT: `scripts/refresh_nces_districts.py` (pass school_year through to load, OR document)
- EDIT: `artemis/marketing/routes/signal_criteria.py` (+status endpoint + Pydantic)
- EDIT: `public/js/features/marketing-os.js` (District Data card)
- EDIT: `public/js/core/api.js` (+wrapper)
- NEW: `artemis/marketing/tests/test_dist5_district_data_status.py`

---

## Acceptance criteria

1. `alembic current` proof + `upgrade head` → 0056. **Paste.**
2. `district_data_meta` stamped for the current load (school_year='2024-25', row_count matches districts count). `psql -c "SELECT * FROM district_data_meta;"`. **Paste.**
3. `pytest .../test_dist5_district_data_status.py -v` — 4 pass (use ARTEMIS_TEST_DB_URL for pytest; ARTEMIS_DB_URL for any alembic step — they differ!). **Paste.**
4. Browser smoke: District Data card renders on the Signals page with real counts + a freshness badge. **Paste console + description.**
5. Empty-meta path shows empty state, not fake numbers. **Confirm.**
6. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **No fabricated freshness/counts.** Empty meta → honest empty state.
- **Live tier counts** from `districts` (don't cache stale numbers in meta beyond row_count).
- **Lossless:** the refresh/load path stays upsert-only; the optional refresh button must not be able to wipe districts on failure.
- **Mirror DIST2's District Sizing panel** placement + style (it's the Signal Playbook template).
- **Env vars:** alembic→ARTEMIS_DB_URL, pytest→ARTEMIS_TEST_DB_URL (do not conflate).
- **Migration 0056**, down_revision 0055. Paste `alembic current`.
- **Local-only git.**

---

## Report-back format

```
DIST5 — district data freshness panel report
1. Commit / branch
2. alembic current + migration number
3. LOC per file
4. district_data_meta row after stamp
5. Test pass count (esp. freshness thresholds #2 + empty-meta #3)
6. Browser smoke (card + badge)
7. Did Part E (refresh button) land or is it a documented TODO?
8. check.sh summary
9. Surprises
```
