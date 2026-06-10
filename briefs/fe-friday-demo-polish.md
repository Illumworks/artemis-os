# Worker Brief — Friday Demo Polish Pass (Frontend)

**Owner:** terminal (frontend). **Lead:** Artemis (Opus) verifies in a real browser + merges.
**Status:** READY. **Branch:** `worker/fe-friday-demo-polish`.
**Independent of the Callie backend work** — do NOT touch `artemis/floating_artemis/*`,
`artemis/routes/integrations_slack_events.py`, or `artemis/integrations/*`. This is FE-only (`public/`).

## Why
Friday presentation. The demo will show a REAL campaign created from a hot signal (Jon is removing the
mockup campaigns). Polish the surfaces that will be on screen so nothing reads as unfinished.

## Scope (each is self-contained)

1. **Remove mock/hardcoded campaign fallback.** Per the marketing audit, `marketing-os.js` may still render
   hardcoded demo campaigns when the real API list is small. Rip out any mock/fallback campaign data so the
   Campaigns surface shows ONLY real API data (empty state if none). 
   - File: `public/js/features/marketing-os.js` (~`loadMarketingCampaigns()` / `renderMarketingCampaigns()`,
     ~lines 2963-3016). Remove any hardcoded `CAMPAIGNS` array / merge-with-mock fallback.
   - Add a clean empty state ("No campaigns yet") so a zero-campaign view looks intentional, not broken.

2. **Composer placeholder buttons.** The Writing Studio composer header has disabled placeholder buttons
   (Variants / Rules, `class="is-placeholder" disabled aria-disabled="true"`). Hide them (don't ship disabled
   stubs in the demo).
   - File: `public/js/features/composer-v5.js` (~lines 3145-3146).

3. **Agent Monitor empty-state copy.** Replace the long, apologetic "not yet durable history / not an audit
   trail…" placeholder with a short, confident empty state.
   - File: `public/js/features/agent-monitor.js`.

4. **Light a11y on demo surfaces (time-permitting).** Add `aria-label`/alt text to the key marketing-os +
   composer controls and the floating panel icons. Don't boil the ocean; hit the visible demo path.

## Constraints
- FE-only. None of the Callie backend files (above). No backend API changes.
- **Verify in a real browser** before calling it done (per house discipline: synthetic checks give false
  confidence). Screenshot the Campaigns empty state, the composer header, and the agent monitor.
- Keep the existing composer selection-toolbar logic untouched (it's hard-won; see CLAUDE.md / SESSION-STATE).
- Match surrounding code style.

## Acceptance
Campaigns shows only real data with a clean empty state (no mock rows); no disabled placeholder buttons in
the composer header; agent-monitor empty state is short and confident; demo path has reasonable labels.
Verified in a browser with screenshots.

## Note (separate, not this brief)
Removing the mock campaign ROWS from the DB and initiating a fresh real campaign from a hot signal is a
data/product step Lead is handling separately — this brief only removes the FE mock fallback so the real
campaign renders cleanly.
