# Build brief — Signals funnel redesign (Phase 1: worklist + visible outcomes + Approvals split)

**Agent:** terminal or Codex (FE-heavy — `public/js/features/marketing-os.js`, `public/js/components/signal-tree.js`,
`public/css/features/marketing-os.css`, `public/js/core/api.js`; small backend additions in
`artemis/marketing/routes/` + `repository.py` if needed). **Branch:** `worker/signals-funnel-redesign` off
**current `main`**. **Own git worktree, cd inside it, own test DB `artemis_test_funnel`.** **Do NOT merge —
report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`.

**VISUAL SPEC (Jon-approved):** `public/mockups/signals-funnel-prototype.html` — match this look/behavior for
the Signals worklist (clickable priority cards, expand-to-signals, "Start a campaign" → green done-state,
collapsed "Browse all signals"). Open it to see the target.

## Context / why
The recent "signals unify" just stacked two views; Jon's feedback: the page must be an ACTIONABLE worklist,
not visual noise, and approving must visibly DO something. Mental model = a funnel: **signals in → decide
which become campaigns (Gate-1, on the Signals page) → campaign work → approve documents to send (Gate-2, on
the Approvals page).** The backend already creates campaigns + queues sends on approval — much of this is
surfacing what already happens and reorganizing the FE. Lossless throughout (no deletes; status transitions
only).

## A. Signals page = the worklist
Replace the read-only "Where to focus" table (`renderMarketingPrioritization`, ~line 4875) with **clickable
priority cards**, one per **cluster** (a group of related signals for one opportunity/account), ranked by the
existing prioritization (velocity + recency). Reuse the prioritization ranking data + the existing
clustering (clusters already form server-side — see the signal-brief approval cluster rendering,
`_renderClustersSection` ~line 2604, and `selected_cluster_keys` promotion). Each card (match the prototype):
- Collapsed: rank, title (district/account), one-line "why ranked" summary, badges (geo, hot, time-sensitive).
- Expand → the underlying signals (source · snippet · age) + an actions row: **primary "Start a campaign"**,
  **Snooze**, **Dismiss**.
- **"Start a campaign"** → call the existing Gate-1 promotion (`approve` / `promote_signal_to_candidate` /
  `promote_selected_signals_for_run`) to create the campaign candidate/workspace → the card flips to the green
  **"→ Campaign started — <name>"** done-state with an **"Open workspace →"** link → on the next load the card
  **clears off the worklist** (signals now in `approved`/converted state, no longer "qualified/awaiting").
- **Light cluster edits (this phase):** remove a signal from a cluster (× → the signal leaves this card, stays
  in Browse-all), and **merge** two cards that are the same opportunity. (Backend: an endpoint to adjust
  cluster membership / merge — additive, lossless.)
- Below the cards, a **collapsed "Browse all signals"** section (reuse the existing signal tree /
  `renderSignalInboxTree` + `_wireSignalActions`: snooze/reject/archive/qualify). It must include a **status
  filter that surfaces "converted to campaign / approved"** signals so converted ones are findable (lossless
  traceability — show which campaign each became). Default view = the priority cards; the full firehose is
  one click away, NOT the default scroll.

## A2. Dismiss / Reject must capture a "why" (for training)
When a signal or cluster is **dismissed/rejected** (on the worklist card OR in Browse-all), prompt a short
**"why" dialogue** before it's recorded — a few quick-pick reasons (e.g. "not a real opportunity", "wrong
audience/segment", "too early", "duplicate", "bad grouping — signals don't belong together") PLUS an optional
free-text note. This must be **stored as a training signal**, not just a status note: persist the reason
(structured reason-code + text) so qualification/clustering can improve over time (mirror the Writing Studio
training-candidate pattern — the reason is a labeled example the model can learn from). The existing
reject/snooze already accept optional notes — upgrade that into a deliberate, structured reason capture that
is queryable for training. Do NOT make it a hard block (don't trap the user), but make the reason the default
next step of dismiss/reject. Lossless: reasons are additive, never overwrite.

## B. Approvals page = documents only (Gate-2)
- **Remove signal-brief (Gate-1) approvals from the Approvals queue** — those decisions now happen on the
  Signals worklist. Approvals shows **only content/document (Gate-2) approvals** (writing-studio drafts).
- Each document approval: the draft + preview + **Approve & send / Request changes / Reject**. Approving must
  show the **visible outcome** ("→ Queued to send" / sent). No more silent dead-end feel.
- (Backend already does the right thing on Gate-2 approve — surface the consequence in the UI.)

## C. Lossless + traceability (must hold)
Nothing deleted. "Start a campaign" → signals to `approved`/converted, linked to the campaign candidate,
findable in Browse-all by status + shown on the campaign workspace. "Archived" (dismissed) stays a SEPARATE
status from "converted." Confirm a converted signal is retrievable after it leaves the worklist.
**The cluster is PRESERVED, not dissolved:** converted signals retain their grouping as the campaign's
source-signal set (the cluster *becomes* the campaign — do NOT scatter them into individuals). The campaign
workspace shows the originating cluster together; in Browse-all each converted signal is labeled with the
campaign it rolled into (`→ <campaign name>`). The `campaign_candidate_signals` link table already binds them
to the candidate — surface that, don't break it.

## Acceptance (verify the EFFECT — browser + screenshots)
- Signals: priority cards render, expand to show real signals; "Start a campaign" creates a real campaign
  (prove a candidate/workspace exists), shows the done-state, and the card is gone on reload. Remove-signal
  and merge-cards work. Browse-all filter surfaces a converted signal.
- Approvals: shows ONLY document approvals (no signal-brief items); approving a document shows "Queued to
  send" and the item leaves the pending list.
- No console errors; `./scripts/check.sh` for touched Python (note PRE-EXISTING failures separately). Match
  the prototype's look. Screenshots of: worklist, an expanded card, the done-state, Browse-all with a
  converted signal, the documents-only Approvals page.

## Out of scope — Phase 2 fast-follow (do NOT build here; will be a separate brief)
**Freeform manual clustering:** multi-select signals in "Browse all signals" → "Group into a cluster → Start
a campaign" (needs a backend path to create a candidate from an arbitrary set of signal IDs). Note it in the
report as the next step; don't start it.

## Constraints
Reuse existing renders (`renderMarketingPrioritization` ranking data, the signal tree, the approval cards) +
existing approve/promote APIs — don't fork. Lossless (status transitions only; converted signals retrievable).
Match `public/mockups/signals-funnel-prototype.html`. Isolated worktree + own test DB. **Do NOT merge** —
report branch + SHA + worktree + screenshots + the "campaign actually created" proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
