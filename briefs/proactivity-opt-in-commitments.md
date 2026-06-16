# Brief: Opt-in commitments — stop meeting action-items from auto-firing (proactivity lane)

**Owner:** Opus Lead (proactivity lane; NO overlap with terminal's R3 / scout). Greenlit by Jon 2026-06-15.

## Problem
`ingest_meeting_commitments` (`artemis/proactivity/commitments.py:~170`) turns **every** meeting action-item
(up to 15/meeting) into an **active** `Commitment`, and `send_commitment_followups` then auto-DMs reminders
for active+due commitments. Control is opt-OUT (a `meeting_action_item_dismissals` row suppresses an item
after the fact). Result: one meeting buried Jon in reminders he never wanted. **Precision > recall for a PA:
one flood of wrong reminders and the user mutes the whole feature.** The item is ALSO mirrored to memory
(`write_observation`, commitments.py:232) — so gating the *commitment* loses nothing (lossless holds).

## Design — flip to opt-in (propose → Jon confirms → track)
**Phase 1 (this build):**
1. **`proposed` commitment state.** Add `proposed` to the `commitments` status CheckConstraint
   (`proposed, active, snoozed, done, dismissed`). Migration.
2. **Create as `proposed`, gated at the source.** In `ingest_meeting_commitments`, only create a (proposed)
   commitment when the item is **owned by the owner (Jon)** AND has a **deadline** (`owner_user_id ==` the
   resolved owner id AND `due_value is not None`). Items owned by others / no deadline → memory observation
   only (still written, lossless), NO commitment. `upsert_commitment` lands `status='proposed'`.
3. **Follow-ups fire only on `active`.** Verify `send_commitment_followups` /
   `list_commitment_followup_candidates` exclude `proposed` (so nothing auto-nags). With (2) landing proposed,
   meeting commitments can't fire until promoted — satisfies "auto-fire off in the meantime."
4. **Promote / dismiss + capture the learning signal.** `approve(commitment)` → `active` (+ record decision);
   `dismiss` → `dismissed` (+ record decision). New **append-only `commitment_decisions`** table
   (commitment_id, decision `approve|dismiss`, features JSONB {owner_is_jon, had_due, sensitivity,
   meeting_title, source_type, ...}, decided_at). This is the labeled data future learning needs — lossless.
5. **Surface for approval (Slack-first):** a digest DM to Jon listing proposed commitments
   ("Caught these from your meetings — reply `track 1,3` / `track none` / `track all`"), deterministic reply
   handler (reuse the keyword/confirm pattern from the OKR flow, not an LLM classifier) → promote/dismiss +
   record decisions. (Build as a 2nd worker on top of the core.)

**Phase 2-3 (later, NOT now):** use `commitment_decisions` to shrink the proposed list (learn "drop
other-owned", "rank Jon-owned+deadline top"); then self-improvement proposals ("you always dismiss Y — stop
surfacing it?"), human-gated. **Guardrail (firm):** learning may only make her QUIETER / pre-select a tighter
list (auto-confirm only patterns Jon has repeatedly approved, with one-tap undo) — it must NEVER auto-fire a
follow-up without the gate. Precision over recall is the invariant.

## Key code
`artemis/proactivity/commitments.py` (`ingest_meeting_commitments` ~170-248, `send_commitment_followups`
~321, `_resolve_owner_user_id`, `upsert_commitment` in repo), `artemis/proactivity/models.py` (`Commitment`
status constraint ~158; add `commitment_decisions` model), `meeting_action_item_dismissals` (existing
dismissal capture — extend/complement, don't break). Owner = jon.fila@ (see [[project-m3-owner-email-cf-access]]).
Verify the EFFECT live, assert DB state, not just "tests pass" ([[live-smokes-catch-real-bugs]]).
