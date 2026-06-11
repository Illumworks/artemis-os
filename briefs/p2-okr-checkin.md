# Worker Brief — P2: Friday 4pm OKR Check-in (propose → approve → word-dump → update)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-checkin`. Builds on P2a (proactivity scheduler + reservation table). Plan:
`docs/p2-proactivity-build-plan.md` (flagship flow). Test DB at head — real tests.

## Why / the flow (Jon's ask)
Fridays 4pm (Jon's tz), Artemis runs an OKR check-in in his DM:
1. She assembles **what she actually knows we accomplished this week** and **proposes** OKR updates —
   each proposal **cites its basis** (an activity entry, a closed Jira ticket, a meeting action item, a
   commitment). **Nothing fabricated or inferred without a source.**
2. Jon **approves / corrects**, then **word-dumps** the week freely.
3. Artemis reconciles the proposal + his word-dump into concrete KR updates and **applies them to OKR Studio
   ONLY on his explicit go** — a conversational confirm.

## CRITICAL safety fix (do this first)
`artemis/floating_artemis/tools/okr.py` registers `update_okr_kr` at **layer 2 (auto-invoke, no confirm)**.
That lets Artemis change OKRs without approval — exactly what Jon forbids. **Bump `update_okr_kr` to layer 3
(propose→confirm).** No OKR write may happen without Jon's explicit confirmation, ever (not just in this flow).
Verify the layer-3 gate (authority.py) actually pauses + waits for confirm for this tool.

## Scope
1. **Layer-3 gate on `update_okr_kr`** (above). Add a test that it requires confirmation.
2. **Scheduled trigger** (reuse `proactivity/scheduler.py` + the reservation table, `delivery_kind='okr_checkin'`,
   once-per-Friday idempotency): `ARTEMIS_OKR_CHECKIN_CRON` default `0 16 * * 5` (Fri 16:00), tz = morning-brief tz.
3. **Proposal generator** — gather this week's basis from sources Artemis has: OKR current state
   (`okr.repository.list_objectives` / `list_key_results`), OKR activity (`list_activity`), closed/updated
   Jira this week, recent meeting `action_items`, commitments. Produce **proposed KR updates each with a cited
   basis**; if a KR can't be grounded, DON'T propose a change for it. Post the proposal to Jon's DM (this push
   is informational — surfacing TO Jon — which is allowed; the WRITES are what's gated).
4. **Conversational apply** — Jon's reply (approve + word-dump) flows through the existing DM agent loop (P1);
   Artemis reconciles + restates concrete updates, and on his explicit go calls the now-gated `update_okr_kr`
   (propose→confirm) + logs an `okr.create_activity` entry noting "updated via Friday check-in, approved by Jon".
   Nothing applied without the confirm; nothing invented.

## Constraints
- **Approval-first / lossless:** OKR writes gated; never fabricate or add a KR/objective without Jon's ok.
  Cite the basis for every proposal. (Operating rules already flag OKR Studio as approval-required.)
- Reuse the P2a scheduler + reservation pattern; don't add a new scheduling stack. No new deps; ruff + mypy
  strict; DB-backed tests.

## Tests
- `update_okr_kr` now requires confirmation (layer 3) — proves it pauses.
- The Friday job reserves once-per-week (idempotent), posts a proposal with cited bases, and does NOT write
  any OKR on its own (write only happens on a confirmed tool call).
- Proposal generator: a KR with no grounding source produces no proposed change.

## Acceptance
Friday 4pm Artemis DMs Jon a sourced OKR-update proposal; he approves + word-dumps; she applies updates only
on his explicit confirm, nothing fabricated. Lead verifies live (fire it manually into Jon's DM, run the
approve→update round-trip, confirm an OKR KR changes only after confirm).
