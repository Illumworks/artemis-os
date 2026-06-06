# Cost Phase 5 — Monthly forecast + prior-period comparison

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-phase-5-forecast`
**Browser smoke owner:** Lead, post-merge — verify hero shows "on track for $X/mo at current pace" + chart shows projection line.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 (math addition + hero copy + chart projection line).
**Priority:** MEDIUM-LOW — refines Phase 2's hero with predictive framing.
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`
**Depends on:** Phase 2 merged.

---

## Why this exists

Phase 2 answers "what did I spend this month so far." Phase 5 answers "what will I spend by month-end if usage continues."

Useful because cost decisions usually want a forward-looking signal, not just a snapshot. Worth keeping the math simple — trailing-7-day average extrapolated to a full 30-day month. Not a fancy ML projection; just a clean linear pace.

---

## Scope

### Part A — Backend: extend `/api/costs/summary`

Add to the existing endpoint's response:

```json
"forecast": {
  "method": "trailing_7d_extrapolation",
  "trailing_7d_total_usd": 21.40,
  "trailing_7d_avg_daily_usd": 3.06,
  "projected_month_total_usd": 91.80,    // current MTD + (remaining days × avg daily)
  "prior_month_total_usd": 134.20,        // for comparison
  "pace_vs_prior_month": -0.32            // -32% — pacing below last month
}
```

Computation:
- `trailing_7d_total_usd` = SUM(cost_usd) for `created_at` in `[now() - 7d, now()]`.
- `trailing_7d_avg_daily_usd` = `trailing_7d_total_usd / 7`.
- `projected_month_total_usd` = `current MTD spend + (days_remaining_in_month × trailing_7d_avg_daily_usd)`.
- `pace_vs_prior_month` = `(projected - prior_month_total) / prior_month_total`.

The endpoint becomes idempotent + cacheable for the duration of a minute or so; don't bother caching unless smoke shows latency issues.

### Part B — Hero refinement in the Spend tab

Update the hero block in `public/js/features/cost-shell.js`:

```
Cost

This month: $42.10 (MTD)
On track for $91.80 by month-end · last month: $134.20 (pacing -32%)

[daily sparkline of MTD with a dashed projection line continuing to month-end]
```

The sparkline gets a dashed-line projection appended for the remaining days of the month using the trailing-7d average. Match existing chart styling; dashed strokes for the projection portion.

### Part C — Tests

`artemis/routes/tests/test_costs_forecast.py` (new):

1. **`projected_month_total_usd` computed correctly.** Seed 7 days of events with known daily averages. Verify the formula above.
2. **Pace vs prior month math.** Seed prior month with known total + current MTD pacing low. Verify negative pace.
3. **Edge case: first day of month.** MTD = 0 + trailing 7d projection should = trailing_7d_avg × days_in_month.
4. **Edge case: 7-day window has zero data.** Verify projection = 0; no division-by-zero.
5. **Edge case: prior month had zero spend.** Verify `pace_vs_prior_month` returns `null` or special value (don't divide by zero).

---

## Files owned

- EDIT: `artemis/routes/costs.py` (add forecast block to `/summary`)
- EDIT: `public/js/features/cost-shell.js` (hero copy + projection line on sparkline)
- EDIT: `public/css/panels/cost.css` (dashed projection stroke)
- NEW: `artemis/routes/tests/test_costs_forecast.py`

---

## Acceptance criteria

1. **No schema changes.** **Paste.**
2. **Backend tests pass.** **Paste.**
3. `./scripts/check.sh` passes. **Paste.**
4. **Live smoke (Lead does post-merge):**
   - Open Cost page (Spend tab).
   - Verify hero now reads: "This month: $X (MTD). On track for $Y by month-end · last month: $Z (pacing ±N%)".
   - Verify the sparkline shows historical days as solid bars and the remaining days as a dashed projection.
   - Manually compute the projection: `MTD + (days_remaining × (trailing_7d / 7))` and compare to displayed value. **Paste both numbers.**
5. `git diff --stat`. **Paste.**

---

## Hard constraints

- **Trailing 7 days is the projection basis.** Don't add fancier methods (EWMA, seasonality, ML) in this phase. Linear pace from a 7-day window is honest and predictable.
- **Projection updates every page load.** No caching beyond the request lifecycle.
- **Honest about uncertainty.** Hero says "On track for" not "Will be" — projection is a pace estimate, not a guarantee.
- **Pace comparison handles edge cases gracefully.** First day of month, zero prior month, zero current usage — all produce sensible UI states.
- **Local-only git.** Worker on `worker/cost-phase-5-forecast`; Lead merges after smoke.
