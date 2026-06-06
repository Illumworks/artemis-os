# Roadmap plan — Unify Signals Inbox + Approval Queue into one "Review" page

**Status:** ROADMAP — not yet scheduled. Sequence: part of the Campaign / whole-app UI pass. Captured
2026-06-06 per Jon ("signal inbox and approval queue are sort of the same thing… I question the need for 2
separate pages, people will get confused; both need filter + sort"). Design plan, not a ready-to-fire
worker brief — refine into worker brief(s) when scheduled.

## Problem (grounded in the code)
The two pages overlap for real, not just visually:
- **Approval Queue** (`MARKETING_APPROVALS_VIEW` → `loadMarketingApprovals`/`renderMarketingApprovals`;
  backend `artemis/marketing/routes/approvals.py::list_approvals_route`) is a GENERIC "decisions waiting on
  you" list. Items carry a `kind`: `signal_brief` (Gate-1), `content_draft` (Gate-2), `automation_run`, …
  The route ALREADY supports optional `status` + `kind` filters.
- **Signals Inbox** (`MARKETING_SIGNALS_VIEW` → `loadMarketingSignals`/`renderMarketingSignals`) is a
  SPECIALIZED view of just the `signal_brief` / Gate-1 items, rendered with the rich cluster-selection UI
  (clusters grouped, suggested-strongest highlighted, operator picks which become campaigns).
- ⇒ **The same Gate-1 `signal_brief` items appear on BOTH pages.** That's the redundancy users feel. Neither
  page has real filter/sort today.

The genuine difference is only presentation: Signals Inbox = rich triage UI for one kind; Approval Queue =
flat list of all kinds (the superset).

## Recommendation: collapse to ONE page with segmented filters
A single destination — working name **"Review"** (alt: "Needs you" / keep "Approvals"; naming is a
Creative-Director call for Jon) — with a segmented filter:

> **All · Signals · Campaign briefs · Content drafts · Automations**

- **Signals** segment → the existing rich cluster-selection UI (today's Signals Inbox), unchanged in
  behavior (preserve the Gate-1 operator-selection + suggested-strongest work).
- **Campaign briefs / Content drafts / Automations** → the existing approval cards for those kinds.
- **All** → everything pending, newest first.
- Retire the duplicate page; redirect the old route to the unified one (don't 404 a bookmarked link).

Per-type rendering: the unified page dispatches on `kind` to the right card/UI (signals keep their cluster
card; drafts keep the draft-approval card). One page, distinct rendering — not a mixed bag.

### Filter + sort (both pages need this today; the unified page is where it lands)
- **Filter:** kind/segment (above) · status (pending / approved / rejected / snoozed) · state ·
  district · campaign family.
- **Sort:** newest ↔ oldest · priority / fit score · suggested-strongest first (for signals).
- Lean on `list_approvals_route`'s existing `status`+`kind` filtering; extend with the additional facets.

## Why feasible
The approvals backend is already the superset with `status`+`kind` filtering — much of the plumbing exists.
Work is mostly: one unified front-end page that renders per-kind, wiring the filter/sort controls, folding
the Signals cluster UI in as the Signals segment, and retiring/redirecting the old Signals Inbox route.

## Trade-off / worst case
- The Signals triage UI is richer than a plain approval card; the unified page must keep per-type rendering
  or it degrades into a mixed list. Done well it's strictly cleaner than two pages.
- Effort: moderate — an information-architecture consolidation, not a from-scratch build.
- Worst case if we DON'T: two pages, overlapping `signal_brief` items, both still needing filters — the
  exact confusion predicted for new users (Angela, Josh).

## Constraints (carry into the worker brief)
- Preserve the Gate-1 operator-selection + suggested-strongest-cluster behavior verbatim (it's load-bearing
  + recently built). No regression to the promote-selected-signals path.
- Lossless: no destructive changes to approvals/signals data; status transitions only.
- Don't break existing approval decision flows (approve / reject / snooze / request-changes / the
  signal_brief promotion side effects + memory carryover). Keep both decision paths unified (as Group A did).
- Redirect the retired route; update nav. Org dep rule. Browser-smoke both segments + the filters.

## Open questions (decide when scheduled)
- Final name (Review / Needs you / Approvals).
- Default landing segment (All, or Signals?).
- Keep a deep-link / saved-filter for "just signals" (power users)?
- Do automations belong here at all, or move them under Pipelines?
