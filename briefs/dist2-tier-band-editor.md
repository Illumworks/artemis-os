# DIST2 — Tier-band editor in Signal Playbook + recompute (Stream 1)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. New endpoint + UI section wired to existing patterns (TerritoryConfig editor is the template); some inference about the Signal Playbook UI structure, but no novel design.
**Target branch:** `worker/dist2-tier-band-editor`
**Fires:** AFTER DIST1 merges (needs `district_tier_bands` table + `recompute_all_tiers` repository fn).
**Browser smoke owner:** Worker (edit a band → save → recompute → verify), Lead re-verifies.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300.
**Priority:** HIGH — gives Josh the editable knob (design decision D-3).

---

## Why this exists

Per `docs/campaign-initiation-and-district-design.md` D-3: tier bands (D1–D4 enrollment cutoffs) are **global, DB-backed, and edited in the Signal Playbook UI** — so Josh retunes them without code. DIST1 created the `district_tier_bands` table + `recompute_all_tiers()`. DIST2 exposes them: a CRUD-ish endpoint + a "District Sizing" section in the Signal Playbook UI + a recompute-all button.

The existing **TerritoryConfig editor** (`PUT /api/.../territory/{family}` in `artemis/marketing/routes/signal_criteria.py`, edited in the Signal Playbook UI) is the exact pattern to mirror.

---

## Scope

### Part A — Backend endpoints

In `artemis/marketing/routes/signal_criteria.py` (the Signal Playbook route module), add:
- `GET /api/marketing/signal-criteria/tier-bands` → returns the 4 bands ordered by display_order (reuse `get_tier_bands` from DIST1).
- `PUT /api/marketing/signal-criteria/tier-bands` → accepts the full set of 4 bands (min/max per tier), validates them, upserts. **Validation (self-teaching, H1 style):** bands must tile the range with no gaps/overlaps — D4 floor null, D1 ceiling null, each tier's max+1 == next tier's min. Reject with a clear message naming the gap/overlap if invalid.
- `POST /api/marketing/signal-criteria/tier-bands/recompute` → calls `recompute_all_tiers` (DIST1), returns `{updated: N}`.

Pydantic request/response models (H-discipline). No raw dict bodies.

### Part B — Signal Playbook UI

Add a **"District Sizing"** section to the Signal Playbook UI (find the file that renders the territory/criteria editor — likely `public/js/features/` Signal Playbook module; locate via the territory editor). It shows the 4 bands as editable min/max number inputs, a Save button (PUT), and a "Recompute all districts" button (POST recompute) that shows the updated count.

Match the existing editor's visual + save-feedback pattern. Add api.js wrappers for the 3 endpoints.

### Part C — Tests

`artemis/marketing/tests/test_dist2_tier_bands.py`:
1. GET returns 4 seeded bands ordered.
2. PUT with valid tiling updates bands; re-GET reflects change.
3. PUT with a **gap** (D3 max 9999, D2 min 11000) → 4xx with self-teaching message naming the gap.
4. PUT with an **overlap** → 4xx self-teaching.
5. POST recompute after a band change returns updated count > 0 (seed a couple districts first).

---

## Files owned

- EDIT: `artemis/marketing/routes/signal_criteria.py` (+3 endpoints + Pydantic models)
- EDIT: Signal Playbook UI module (+District Sizing section) — locate via the TerritoryConfig editor
- EDIT: `public/js/core/api.js` (+3 wrappers)
- NEW: `artemis/marketing/tests/test_dist2_tier_bands.py`

**No migration** (DIST1 created the table).

---

## Acceptance criteria

1. 3 endpoints live; `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_dist2_tier_bands.py -v` all pass. **Paste.**
2. Browser smoke: edit D3 max → Save → Recompute → see updated count. **Paste console + screenshot/description.**
3. Tiling validation rejects gaps + overlaps with self-teaching messages. **Paste one rejection body.**
4. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
5. `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **Mirror the TerritoryConfig editor pattern** — don't invent a new UI idiom.
- **Tiling invariant enforced server-side** (gaps/overlaps rejected) — the classifier depends on total, non-overlapping bands.
- **Recompute is the only path to re-tier existing districts** after a band edit — wire the button to it.
- **down_revision N/A** — no migration.
- **Local-only git.**

---

## Report-back format

```
DIST2 — tier-band editor report
1. Commit / branch
2. LOC per file
3. Endpoints + test pass count
4. Browser smoke (edit → save → recompute)
5. Tiling-validation rejection example
6. check.sh summary
7. Surprises — esp. Signal Playbook UI module structure
```
