# Brief: Surface Marketing Intelligence Phase 1 in the UI

**For:** the TERMINAL Opus Lead (via Sonnet workers) OR Codex — UI build.
**Back to:** app Opus Lead for verification + merge to `main`. Local-only git.
Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**WORKTREE ISOLATION (read first):** Do ALL work in your own dedicated git worktree — never run
git checkout/branch/commit in the main repo at `/Users/artemis/Desktop/Artemis/artemis-os`, and do
not write files into it. Commit to a branch in your worktree and report the branch name; the app
Opus Lead merges. (Two prior rounds leaked branch/file state into the main repo and cost untangling
time.)

## Why

Phase 1 of the Marketing Intelligence Layer is built + merged on the BACKEND but is invisible: the
trend context and the prioritization ranking are computed and returned by the API, but nothing in
the app shows them. This brief surfaces them so a human actually sees the intelligence at the
decision moment. No backend changes — read-only presentation of existing endpoints.

## Piece 1 — Trend context on the Gate-1 / initiation review surface (Decision 1)

The endpoint `GET /api/marketing/campaigns/{id}/initiation-proposal` now returns a `trendContext`
object alongside the existing proposal/enrichment fields:
```
trendContext: {
  resolved: bool, asOf, theme, region,
  momentum: { window_days, bucket_days, buckets:[{bucket_start,bucket_end,count}],
              current_window_count, prior_window_count, delta_ratio },
  comparables: { comparable_count, sample_districts:[...] },
  decisionHistory: { priorApproves, priorRejects, topMatches:[{observationId,category,decision,summary}] }
}
```
Render a compact **trend block** in the initiation / Gate-1 review UI, next to where the existing
ENRICH1 enrichment renders (find it in `public/js/features/marketing-os.js` — the campaign
initiation / proposal detail view). Make the trend legible at a glance:
- **Momentum:** direction + magnitude ("literacy signals up ~3× this quarter" from delta_ratio; a
  tiny inline bar/sparkline from `buckets` is a plus). If `delta_ratio` is null (no prior signals),
  say "new / no prior-period baseline."
- **Comparables:** "{comparable_count} comparable districts with signals in 90d" (+ sample names).
- **Decision history:** "You've approved {priorApproves} / rejected {priorRejects} similar
  campaigns" with the topMatches summaries available on expand.
- If `resolved: false`, show a quiet "no trend data yet" state — don't error.

## Piece 2 — Prioritization view (Decision 2)

`GET /api/marketing/intel/prioritization` (params: window_days≤180 default 30, horizon_days,
limit, optional state) returns:
```
{ as_of, window_days, horizon_days, state_filter,
  velocity_ranking:[...], time_sensitive:[...], combined:[...] }
```
Add a **"Where to focus" / prioritization panel or view** in the marketing surface that calls this
(add the fetch to `public/js/core/api.js`) and renders the **combined** ranked districts with the
why (velocity + urgency + timing). Show the ranking clearly; let the user optionally filter by
state. **Honesty:** D2's time-sensitivity is a proxy from created_at + urgency (no real deadline
column yet) — label it as an estimate, do NOT imply hard deadlines.

## Guardrails

- Read-only presentation; NO backend/endpoint/schema changes (the data already flows).
- Match existing UI patterns in `public/js/features/marketing-os.js` + `public/js/core/api.js` +
  `navigation.js`. Don't invent a new design language.
- If the repo has frontend smoke tests (`tests/unit/frontend/`), add coverage in that style.
- Local-only git; own worktree/branch (`worker/intel-p1-ui-*`); ruff/mypy not relevant for pure JS,
  but keep any Python touched clean. No dependency add/upgrade.

## Verify (the app Opus Lead does this live)

Load the running app: the Gate-1 / initiation screen shows the trend block populated with real data
(e.g. candidate 3 → momentum + comparables + priorApproves=5/priorRejects=1); the prioritization
view shows the ranked districts. Report your branch + a screenshot or the exact view/route to check.

## Handoff

Do NOT merge to main. Report branch + diff + how to view each piece. App Opus Lead verifies in the
running app and merges. Log progress in `../claudeck-artemis/COORDINATION.md`.
