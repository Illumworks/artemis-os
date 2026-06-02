# DIST4 — Qualifier soft-flags D4 + Gate 1 card shows district context (Stream 1)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. Consumes existing data (district tier from DIST1/DIST3) and adds flag logic + card display; well-scoped but touches both qualifier path and Gate 1 card rendering.
**Target branch:** `worker/dist4-qualifier-soft-flag`
**Fires:** AFTER DIST3 merges (needs `signal_queue.resolved_district_id` + populated districts).
**Browser smoke owner:** Lead post-merge (Gate 1 card shows tier + supported badge).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300.
**Priority:** HIGH — completes Stream 1: tier becomes visible + actionable at the approval gate.

---

## Why this exists

Per `docs/campaign-initiation-and-district-design.md` D-4: D4 (smallest) districts are **soft-flagged** — they reach Gate 1 but are clearly marked "unsupported tier — filtered," so Josh can still eyeball them; nothing is deleted. This brief makes the qualifier annotate the flag and the Gate 1 card surface district context (tier, enrollment, supported badge). This is the payoff of the whole district layer: **the approver finally sees how big the district is.**

DIST1 gave us `districts.supported` (false for D4). DIST3 linked signals → districts. DIST4 consumes both.

---

## Scope

### Part A — Qualifier annotates the tier flag (soft)

In the qualifier / signal-qualification path: when a signal has a `resolved_district_id`, read the district's `tier` + `supported`. Annotate the qualification result with `district_tier` + `district_supported` + a derived `tier_flag` (e.g. `"unsupported_tier"` when `supported=false`, else null).

**Soft only:** do NOT drop, reject, or auto-skip the signal. It still flows to Gate 1. The flag is metadata for the human + for filtering, never a hard gate. (D-4 locked: reopen-to-D4 must be a flag flip, so no destructive filtering anywhere.)

Where to store: prefer the existing `signal_queue.qualification_json` (additive keys) over a new column — no migration needed. If a column is cleaner, run `alembic current` first and coordinate the number.

### Part B — Gate 1 card shows district context

In the Gate 1 Signals Inbox card (marketing UI — locate the card renderer; the audit notes `marketing-os.js` renders signals), add a district context line:
```
Los Angeles Unified · CA · D1 · 49,200 students · supported ✓
```
And for unsupported:
```
Tinytown SD · MT · D4 · 1,100 students · ⚠ unsupported tier (filtered)
```
- When `resolved_district_id` is NULL (unmatched), show a muted "District: unresolved" rather than faking data.
- The unsupported badge should be visually distinct (warning style) but the card stays actionable — Josh can still approve/reject.

### Part C — Optional filter affordance

Add a lightweight "hide unsupported tiers" toggle to the Signals Inbox (default OFF, so D4 is visible by default — matches D-4 "eyeball them"). Persists in the existing UI-state pattern (e.g. localStorage like `MKT_SIGNAL_TREE_STATE`). Keep it simple; this is a convenience, not a hard filter.

### Part D — Tests

`artemis/marketing/tests/test_dist4_tier_flag.py`:
1. Qualification of a signal linked to a D4 district → `district_supported=false`, `tier_flag="unsupported_tier"`, but signal **still reaches Gate 1** (not dropped).
2. Qualification of a D1-district signal → `district_supported=true`, no flag.
3. Signal with NULL `resolved_district_id` → no tier annotation, no crash.
4. **Lossless/soft:** assert the unsupported signal's status path is identical to a supported one (only metadata differs) — no auto-skip.

---

## Files owned

- EDIT: qualifier path (locate — likely `artemis/marketing/` qualification logic or the qualifier tool)
- EDIT: Gate 1 card renderer (marketing UI — `public/js/features/marketing-os.js` signal card)
- POSSIBLE EDIT: `public/js/core/api.js` (if the signal payload needs the district fields surfaced — check the signal_queue serializer includes tier/supported/enrollment)
- POSSIBLE EDIT: signal_queue serializer/route to include district context in the API response
- NEW: `artemis/marketing/tests/test_dist4_tier_flag.py`

**Likely no migration** (use `qualification_json`). If you add a column, paste `alembic current`.

---

## Acceptance criteria

1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_dist4_tier_flag.py -v` — 4 pass. **Paste.**
2. **Soft-flag proven:** test #1 + #4 show D4 signals reach Gate 1 unchanged except metadata. **Paste.**
3. Browser smoke: Gate 1 card shows district line for a resolved signal (tier + enrollment + badge) and "unresolved" for an unmatched one. **Paste console + description.**
4. API response includes district context fields. **Paste a sample signal payload.**
5. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **Soft flag ONLY.** No drop/reject/auto-skip of unsupported-tier signals anywhere. Reopening to D4 must be a one-field flip.
- **Unmatched districts show honest "unresolved"** — never fabricate tier/enrollment for display.
- **Prefer `qualification_json` (no migration).** If a column is needed, coordinate the number via `alembic current`.
- **Fires after DIST3** (needs resolved_district_id + populated districts).
- **Local-only git.**

---

## Report-back format

```
DIST4 — qualifier soft-flag report
1. Commit / branch
2. LOC per file
3. Test pass count (esp. soft-flag #1 + #4)
4. Browser smoke (Gate 1 card district line, supported + unsupported + unresolved)
5. Sample API payload with district context
6. Where the flag is stored (qualification_json vs column)
7. check.sh summary
8. Surprises
```
