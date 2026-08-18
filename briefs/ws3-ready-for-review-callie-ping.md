# Worker Brief — WS Backlog #3: "Ready for review" → Callie pings the reviewer

**Owner:** Codex (full vertical slice — backend + FE button). **Lead:** Artemis (Opus) drives the live
browser/Slack verification + merges (Codex builds; Lead proves the effect since Codex has no browser).
**Branch:** `worker/ws3-ready-for-review`. **Status:** READY.
**Do-NOT-merge-report** in an isolated worktree; Lead merges after a live smoke.

## Why
Authors (Angela et al.) need to flag a draft **"Ready for review"** so the reviewer gets pinged — and the
cool version (Jon): the ping is posted **by Callie** (Angela loves Callie), reusing her bot. This is also the
P4 orchestration bridge (a Named sub-agent acting on a workflow event).

## Scope

### Backend (Codex)
1. **Draft state.** Add or reuse a `ready_for_review` flag/state on the writing draft. **Reconcile with the
   EXISTING review flow — do NOT build a parallel one:** there's already `submit_draft_for_review`, the Gate-2
   human-gate cards, and the marketing `state_machine.py` / `campaign_deliverables.py` review surfaces, plus
   `content_agent_tools.py` ("push a draft into the Writing Studio for Angela/Julie/Olivia review"). Extend
   that, don't duplicate. If a suitable state already exists, reuse it.
2. **Endpoint.** `POST /api/writing-studio/drafts/{draft_id}/ready-for-review` with body `{ reviewer_email }`
   (default the campaign's approver, else `angela@amiralearning.com`). Sets the state + fires the ping. The
   state change is a status update (lossless/recoverable — no hard delete).
3. **The Callie ping.** Post **as Callie** (reuse her bot token + posting path — the C2/C3 multi-bot routing
   in `routes/integrations_slack_events.py` / `docs/callie-build-plan.md`). Resolve the reviewer's Slack ID
   via **`users.lookupByEmail`** (NOT `users.list` — see the Slack-lookup memory note) and DM them, or post in
   the marketing channel, per Callie's role. Message = **deterministic template** (NOT a free LLM generation):
   draft title + author + a link to open it in the Writing Studio. Keep it short, no emoji/em-dash (named-agent
   output lint already enforces this).
4. The click is user-initiated, so the ping is a direct consequence of the user's action — no separate
   propose→confirm gate needed. But the message must be templated/deterministic so nothing surprising is sent.

### FE (Codex)
- A **"Ready for review"** action in the composer (header/actions menu), with an optional reviewer picker
  (default Angela / the campaign approver). On click → `POST .../ready-for-review` → reflect the new state +
  a confirmation toast ("Sent to Angela for review").
- **Hands-off:** do NOT touch the selection-toolbar logic in composer-v5.js (`updateSelectionState`,
  `positionNearSelection`, `showSelToolbar`, `hideSelToolbar`, `handleOutsidePointerDown`) — see SESSION-STATE.

## Constraints
- Reuse Callie's existing posting path + token; do NOT mint a new Slack integration.
- Deterministic message template; named-agent output lint applies (no emoji/em-dash).
- Lossless: state changes only, no destructive deletes.
- Do ws3 and ws4 as **separate branches, one at a time** (both touch composer-v5.js) — finish/report ws3
  before starting ws4 so there's no self-collision in the same file.

## Acceptance (Lead verifies LIVE — assert the EFFECT, not HTTP 200)
Click "Ready for review" on a real draft → (a) draft state flips to ready_for_review in the DB, and (b) a
message **actually posts as Callie** to the reviewer (confirm the delivered Slack message, reviewer resolved
by email). Re-open shows the state. Verified in a real browser + Slack.
