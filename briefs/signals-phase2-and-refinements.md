# Build brief — Signals Phase 2 (freeform clustering) + worklist refinements

**Agent:** Codex (has the funnel context from `worker/signals-funnel-redesign`, now merged) or terminal.
**Branch:** `worker/signals-phase2` off **current `main`** (the funnel redesign is merged — pull first).
**Own git worktree, cd inside it, own test DB `artemis_test_phase2`.** **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md`, the now-live Signals worklist (`marketing-os.js` +
`public/css/features/marketing-os.css` + `signal-tree.js`), the funnel endpoints in
`artemis/marketing/routes/signal_queue.py` + `repository.py`, and `public/mockups/signals-funnel-prototype.html`.
Three items, all on the Signals worklist. Lossless throughout.

## 1. Freeform manual clustering (the Phase-2 we deferred)
Keep the AI auto-grouping as-is. ADD the ability to hand-build a cluster and start a campaign from it:
- In **"Browse all signals"**, add a **multi-select** affordance (checkboxes on signal rows) → a
  **"Group into a cluster → Start a campaign"** action on the selection. The user picks the signals they
  believe belong together, groups them, and promotes that group into a campaign.
- **Backend:** add a path to create a campaign candidate from an **arbitrary set of signal IDs** (reuse the
  funnel's promote/candidate code — `promote_selected_signals_for_run` / `promote_signal_to_candidate` and
  the `campaign_candidate_signals` link table — rather than forking). The hand-picked signals transition to
  `approved`/converted exactly like an auto-cluster promotion, bound together as that campaign's source set.
- Editing existing cards (remove a signal, merge two cards) already shipped in Phase 1 — this adds the
  build-from-scratch case. Keep it discoverable in Browse-all so the clean prioritized worklist on top stays
  uncluttered.

## 2. Hot signals get the orange highlight (make them pop)
Right now "hot" clusters only carry a small HOT badge. Jon wants the **orange highlight treatment we had
before** so hot ones are obvious at a glance.
- **First, grep for the prior treatment** — there was an orange highlight on hot signals in the earlier
  signals UI (search `marketing-os.css` / `signal-tree.js` for hot/urgent/highlight classes). **Reuse it if
  it exists.**
- If not, apply the app's amber accent (`--cv5-accent` / `--amber`, `#c8892f`): a clear **orange left-border
  rail on the card + a subtle warm background tint**, so a hot priority card stands out from the rest of the
  worklist. Keep it tasteful, on-brand (match the prototype's warm palette) — accent, not alarm.
- Tie "hot" to the same signal the ranking already uses (the existing hot/time-sensitive flag / top velocity
  tier) — don't invent a new score.

## 3. Filter the signals INSIDE a cluster
A cluster can hold many signals (Fort Worth had 24) — the expanded card becomes a long scroll. Add a way to
navigate within a group:
- Inside an expanded cluster card, add a small **filter/search box** (filter the cluster's signals by
  source type and/or free-text match on the signal text).
- For large clusters, **collapse to the top N (e.g. 5) by default with a "Show all N" expander**, so opening
  a big cluster isn't an instant wall. The filter + show-all work together.
- The remove-signal (×) and the campaign-start actions must keep working on the filtered/collapsed view.

## Acceptance (verify the EFFECT — browser + screenshots)
- Freeform: multi-select 2–3 signals in Browse-all → group → start a campaign → prove a real
  `campaign_candidates` row links exactly those signal IDs; those signals go converted; they appear together
  on the campaign. No fork of the promote path.
- Hot highlight: a hot cluster is visually distinct (orange) from a non-hot one in the worklist — screenshot
  both.
- In-cluster filter: open a large cluster, filter by source + text, confirm it narrows; "Show all" reveals
  the rest; remove/start still work on the filtered view.
- No console errors; `./scripts/check.sh` for touched Python (note PRE-EXISTING failures separately). Match
  the prototype's look.

## Constraints
Reuse the funnel's promote/candidate code + the prior hot-highlight styling if present + existing renders —
don't fork. Lossless (freeform grouping is additive; converted signals retrievable). Match
`public/mockups/signals-funnel-prototype.html`. Isolated worktree + own test DB. **Do NOT merge** — report
branch + SHA + worktree + screenshots (freeform group→campaign, hot vs non-hot, in-cluster filter) + the
"campaign links exactly the picked signals" proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
