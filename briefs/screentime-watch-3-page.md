# Brief — Screen-Time Watch #3: the dedicated page (heat map + search + scrub)

**Owner:** app-seat Lead (me) → Sonnet worker. **Read first:**
`docs/screentime-watch-plan.md` + Brief 1. **Depends on:** Brief 1 (data + state stance).
**Coordination:** `navigation.js` collides with Forge's "Dev Projects→Forge" rename —
**sequence with Forge (see COORDINATION.md) before editing it.**

**Goal:** a dedicated internal **"Screen-Time Watch"** page — a 50-state heat map
(hero) over a searchable signal repository, with the scrub control. This is the
go-to dashboard for Angela's team + leadership on the screen-time landscape.

## Scope
1. **Backend route** `artemis/routes/screentime.py` (new), owner/marketing-gated as
   appropriate (internal-only — confirm the gate with the marketing surface auth;
   not customer-facing). Endpoints:
   - `GET /api/screentime/state-stance` — the 50-state rollup for the map.
   - `GET /api/screentime/signals?state=&level=&status=&stance=&since=&q=` — the
     searchable, filterable repository (paginated).
   - `POST /api/screentime/purge` (owner-only) — calls `purge_screentime_data()` from Brief 1.
   - Register in `main.py` (additive; flag in COORDINATION.md).
2. **Frontend page** `public/js/features/screentime-watch.js` (new) + a nav entry in
   `public/js/core/navigation.js` ("Screen-Time Watch"). **Rebase the nav edit on top
   of Forge's rename — coordinate timing.**
   - **Hero: 50-state US heat map**, each state colored by stance
     (🟢 favorable / 🔴 unfavorable / ⚪ neutral-or-no-info). Hover = state + rationale +
     signal count; click = filter the list to that state. Render with inline SVG / a
     dependency already in the repo — **do NOT add a new mapping library** (org dep rule;
     check what's already available before choosing).
   - **Searchable repository** below: filter by state, level (state/district), status,
     stance, date; free-text search; each row links the actual source. Click-through
     from the map filters here.
   - **Scrub control** (owner-only): a "Purge screen-time data" action → `POST /purge`
     with a clear confirm. Optionally surface the retention-window setting.
3. Internal-only; no customer exposure.

## Constraints / coordination
- ORG RULE: **no new frontend dependency** (no new map/chart lib) — use inline SVG or
  an existing lib. Confirm before choosing.
- `navigation.js` + `main.py` are shared-risk — additive, sequenced with Forge.
- Match the existing marketing/operations page style + auth pattern.

## Verification (observe the EFFECT)
- Map renders all 50 states colored from real `screentime_state_stance`; a state with
  no signals shows ⚪ no-info (honest gray), not a gap.
- Filters + search return correct subsets; map click filters the list; source links open.
- Purge (owner-only) empties the screentime_* tables and the page reflects it; a
  non-owner cannot purge.
- Page loads under the intended (internal) gate; non-authorized users can't reach it.

**Deliverable:** committed to a worktree branch; report the route gate chosen, the map
rendering approach (which existing lib / inline SVG — no new dep), the nav-rebase
coordination with Forge, and screenshots/among the live render of the map + a filtered search.
