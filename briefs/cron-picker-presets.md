# Cron Picker Presets — Human-Friendly Scheduling

**Owner:** Sonnet Worker (UX design + interaction work)
**Branch:** `worker/cron-picker-presets`
**LOC budget:** ~400 (honest overrun OK to ~520)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE3 + PIPE3 patch merged (trigger_scheduled form exists with raw cron input + reactive preview).
**Grounded in:** Jon's 2026-05-21 walkthrough — "to be honest [cron] is not human friendly and most people including myself don't understand that. it should be very simple, like be able to say weekly, daily, hourly, minute and be able to adjust them easily."

## Why this brief exists

Cron strings (`0 */4 * * *`) are a power-user input. Most operators want to say "Every 4 hours" or "Every weekday at 9am" without learning cron syntax. PIPE3 ships raw cron input; PIPE3 patch adds live preview. This brief replaces the raw cron input with a **preset-based picker** that compiles down to cron under the hood. Raw cron remains as an escape hatch for power users.

After this brief: configuring a scheduled trigger feels like setting a calendar reminder, not writing a regex.

## Scope

### In scope — preset modes

Replace the single cron input with a **mode selector** + per-mode inputs:

#### Mode 1: Every N minutes / hours / days

- Number input + unit dropdown: `Every [5] [minutes / hours / days]`
- Compiles to:
  - Minutes (5): `*/5 * * * *`
  - Hours (4): `0 */4 * * *`
  - Days (1): `0 0 */1 * *`
- Bounds: minutes 1-59, hours 1-23, days 1-31
- Limitation: this preset doesn't support time-of-day for hourly+; use Mode 2 for that

#### Mode 2: Daily at specific time

- Time picker: `Every day at [HH:MM]`
- Compiles to: `<MM> <HH> * * *`
- Timezone selector (carry over from current form)

#### Mode 3: Weekly on specific days

- Day-of-week multi-select: `[Mon] [Tue] [Wed] ...` (Mon = 1, Sun = 0)
- Time picker: `at [HH:MM]`
- Compiles to: `<MM> <HH> * * <days,csv>`
- e.g., Weekdays at 9am = `0 9 * * 1-5` (the form should detect contiguous Mon-Fri and prefer `1-5` over `1,2,3,4,5`)

#### Mode 4: Monthly on specific date

- Date-of-month input: `Day [15]` (1-31, validation: 29-31 may not occur in all months — warn but allow)
- Time picker: `at [HH:MM]`
- Compiles to: `<MM> <HH> <day> * *`

#### Mode 5: Custom (raw cron)

- The existing PIPE3 raw cron input + live preview
- Use this for any pattern that doesn't fit modes 1-4
- Mode 5 is also the fallback when the saved cron string can't be parsed back into any preset (so existing pipelines with complex cron load into Custom mode)

### Round-trip behavior

When opening an existing trigger_scheduled node:
1. Parse the saved cron string
2. Match against preset patterns in priority order (Every N → Daily → Weekly → Monthly → Custom)
3. Open in the matching preset's mode with fields populated
4. If no preset matches: open in Custom mode with raw cron

When saving:
1. Each preset has a `compileCron()` function that produces the cron string
2. Custom mode just passes through the raw cron
3. Save persists the cron string only — backend doesn't know about presets

### Preview

The human-readable preview persists across modes — even in Mode 1-4 (where the user isn't typing cron), the preview reflects the current preset state ("Every 4 hours", "Weekdays at 9:00 AM CST", "Day 15 at 10:30 AM").

Next-run preview also persists across all modes.

### Tests

For each preset mode:
1. Default state renders cleanly
2. Modifying fields updates cron string + preview
3. Save persists; reload opens in same mode with same values
4. Cross-mode: save in Mode 1, manually edit cron via JSON view to a pattern that matches Mode 3, reload form — opens in Mode 3 (round-trip via parsing)

Plus:
5. Custom mode is the fallback for complex patterns (`0 9-17/2 * * 1-5` → Custom)
6. The 5 mode buttons render correctly in the mode selector

### Out of scope

- Specific date-of-year (annual) presets. Defer.
- "Last day of month" patterns. Defer.
- Timezone-relative "every weekday at 9am LOCAL time across timezones" — keep single timezone for v1
- Recurrence end-date / count limits. The cron string is open-ended; if needed, add as a separate field.
- Visual calendar view of upcoming runs (next 7 days). Defer.
- Multi-locale day names. English only for v1.

## Invariants

1. **Cron string is the canonical persisted format.** Presets are UI sugar that compile to cron. Backend never sees preset mode.
2. **Round-trip is lossless.** Saving in Mode 1, reopening, and saving again produces an identical cron string.
3. **Custom mode is always available.** Power users can bypass presets entirely.
4. **No new dependencies.** Cron parsing + compilation is small enough to do inline (~100 LOC). Don't add a cron library.
5. **The trigger_scheduled JSON view (Form/JSON toggle)** still works as a fallback; raw cron edits in JSON view round-trip into the picker.

## Files expected

| File | LOC |
|---|---|
| `public/js/components/node-config-forms/trigger-scheduled-form.js` | ~200 delta (mode selector + 5 preset renderers + cron compile/parse logic) |
| `public/js/components/cron-utils.js` (new — reusable parsing + humanizing) | ~150 |
| `public/css/features/pipelines.css` | ~50 delta (mode selector styling, day-of-week multi-select, time picker) |
| `tests/unit/frontend/test_cron_picker_presets.js` (new or appended) | ~100 |

**Total: ~500 LOC.** Cap 520. If you find yourself heading past 520 (e.g., preset parsing edge cases multiplying), STOP and ping Lead.

## Test plan

1. **Mode 1 — Every 4 hours:** select Mode 1, set "Every [4] [hours]", verify cron is `0 */4 * * *` and preview is "Every 4 hours."
2. **Mode 2 — Daily at 9:00 AM:** select Mode 2, set time to 9:00, verify cron is `0 9 * * *`.
3. **Mode 3 — Weekdays at 9:00 AM:** select Mode 3, check Mon-Fri, time 9:00, verify cron is `0 9 * * 1-5`.
4. **Mode 4 — Monthly on the 15th at 10:30 AM:** verify cron is `30 10 15 * *`.
5. **Mode 5 — Custom:** type any cron string, verify Custom mode shows raw input + preview.
6. **Round-trip:** save in Mode 3 (Weekdays 9am) → reload → Mode 3 selected, Mon-Fri checked, time 9:00.
7. **Cross-mode round-trip:** save in Mode 1 (Every 4h), edit JSON to `0 9 * * 1-5`, save, reload → opens in Mode 3.
8. **Custom fallback:** save unparseable cron `30 9-17/2 * * 1-5` → reload → opens in Mode 5 with raw cron visible.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (Python tests only)
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors; trigger_scheduled save persistence unchanged

## What "done" looks like

1. trigger_scheduled form has a Mode selector with 5 options.
2. Each mode has appropriate inputs (number+unit, time, day-of-week, day-of-month, raw cron).
3. Live human-readable preview updates as fields change.
4. Next-run preview computed correctly per mode.
5. Round-trip works for all preset patterns.
6. Custom mode is the fallback for unparseable cron strings.
7. JSON view still shows the canonical cron string.
8. Tests pass.
9. `check.sh` passes within exempt set.

## Report Worker submits

1. `git diff --stat` output.
2. Screenshots: each of the 5 modes selected with sample values.
3. Cron compilation examples (one per mode, paste).
4. Parse-and-match algorithm summary (how does the form decide which mode to open in?).
5. Test pass count.
6. Branch + worktree path.

---

**Lead notes (not for Worker):**
- This brief is small UX win that materially changes the perceived sophistication of the platform. Non-engineer operators can finally schedule pipelines without learning cron.
- The parse-and-match algorithm is the trickiest part — making `0 9 * * 1-5` open in Mode 3 (Weekdays) requires recognizing that `1-5` means Mon-Fri. Worker should write this as a small set of pattern-match rules, not a full cron parser.
- After this lands, Pipelines configuration is genuinely accessible to anyone, not just engineers.
