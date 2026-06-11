# Worker Brief — Connect the OKR Check-in to the Word-Dump → KR Reconcile Loop + Voice Tune

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-reconcile`. Builds on the merged conversational-confirm + proactivity-voice work.
Test DB at head — real DB-backed tests.

## The bug Jon hit (live)
The Friday check-in is **fire-and-forget**. `_fire_okr_checkin` posts the proposal text via `post_dm` and
walks away — it creates NO agent session, stores NO proposal, leaves NO breadcrumb. The agent loop
(`handle_turn`) has ZERO awareness that a check-in is in flight. So when Jon replied with a real word-dump of
his week's accomplishments, the DM loop treated it as a fresh chat: Artemis was helpful, but she never mapped
his wins to KRs, never proposed an `update_okr_kr`, and so the **propose→"go"→apply loop (already built)
never fired** — nothing ever triggered a pending confirmation.

His word-dump mapped cleanly to real KRs (brand hub → KR 9 Asset Hub / KR 11 Governance; AppScript + PPTX
skill → KR 6 Template Library / KR 7 Self-Service / KR 8 Multimedia Wrappers). The data was there; the wiring
wasn't.

## Part A — Connect the two halves (the real fix)
Bridge the scheduled check-in to the conversational reconcile→propose→apply path.

1. **Leave a breadcrumb when the check-in fires.** In `_fire_okr_checkin`, after posting the proposal,
   persist a lightweight **"OKR check-in awaiting word-dump"** context keyed to the recipient (Jon's DM
   session / Slack user), with a **short TTL** (e.g. expires end of the following Monday — survives the
   weekend, doesn't linger). Store: the current KR snapshot (objectives + KRs + current/target values from
   `okr.repository.list_objectives`/`list_key_results`) and the cited proposal. Reuse existing persistence
   (a small table, or the memory/observation store — pick the lightest correct option; lossless, no DELETE).
2. **Inject reconcile context into the next DM turn.** When a DM arrives for a recipient who has a LIVE
   check-in breadcrumb, `handle_turn` must receive system context along the lines of:
   *"You ran a Friday OKR check-in. Current KRs: [snapshot]. The user's reply is their word-dump of what they
   moved this week. Map concrete accomplishments to SPECIFIC KRs and PROPOSE `update_okr_kr` for each one,
   citing the basis (their own words). Do NOT invent KRs or progress; if something doesn't map to an existing
   KR, say so plainly. update_okr_kr is layer-3 — it will pause for Jon's explicit 'go' before writing."*
   This makes her *propose* KR updates, which trips the layer-3 pending → the existing conversational confirm
   handles "go"/"no" → applies. **Do not bypass the gate** — the write still only happens on Jon's "go".
3. **Clear / expire the breadcrumb** once the reconcile turn(s) complete (applied or Jon moves on), and on TTL.
   It must not hijack unrelated future DMs — only the window right after a check-in.
4. **Keep her helpful.** Reconciling to KRs is ADDITIVE, not a replacement — she can still engage with the
   substance of what Jon describes (as she did well). The new behavior is: also map it to KRs and propose.

## Part B — Voice: less formal, more dry-witty-Jarvis
Jon's note: her current voice reads like a consultant/McKinsey deck (headers, "a few things worth pinning
down", bolded lookup/transformation labels), not his chief of staff. Her inspiration is **a British, witty,
Jarvis-style** chief of staff: confident, dry, conversational, economical. Tune the voice-render pass
(`proactivity/voice_render.py`) + the OKR-checkin/brief phrasing:
- Conversational and warm-but-crisp, light dry wit, NOT formal/consultant. Short sentences. Talks TO Jon, not
  AT a room.
- Drop the deck-style scaffolding (no bolded section labels in casual replies, no "Two things I need:" lists
  unless genuinely a list). Keep the no-em-dash/no-emoji/no-tables lint.
- Still grounded — wit never invents facts.
- (The reconcile turns in Part A run through the normal DM agent loop, whose persona is `load_agent_profile`
  — make sure that persona/system prompt also reflects the dry-witty-Jarvis register so the *conversation*
  matches the *scheduled posts*. If the formality lives in the persona_core, tune there; if in voice_render,
  tune there; check both.)

## Part C — Show KR state in the check-in
The check-in opened with "Here is where your KRs stand" then showed nothing. Surface the actual current KR
values (from the snapshot) so the opener isn't an empty promise — a tight list of objectives + KR current/target,
then the ask for his word-dump.

## Constraints
- **Approval-first / lossless:** OKR writes stay gated (layer-3 `update_okr_kr` → conversational "go"). Never
  fabricate a KR, an objective, or progress. Every proposed update cites Jon's own words as basis.
- No new deps; ruff + mypy strict on touched files; DB-backed tests. Don't regress the morning brief,
  idempotency/reservation, gather_sources fix, or the just-merged confirm + voice work.
- Breadcrumb is scoped + TTL'd — it must never make Artemis treat an unrelated DM as OKR word-dump.

## Tests
- After a check-in fires, a breadcrumb exists for the recipient with the KR snapshot + proposal + TTL.
- A DM reply while the breadcrumb is live → handle_turn receives OKR-reconcile context (assert the context/
  system block is present); a word-dump that names a real accomplishment leads to a PROPOSED `update_okr_kr`
  (layer-3 pending), NOT an immediate write.
- "go" on that pending applies the KR update (reuse the confirm path); "no" cancels; nothing written without go.
- A DM with NO live breadcrumb → normal turn, no OKR-reconcile context injected (no hijack).
- Breadcrumb expires on TTL and after completion.
- Voice: scheduled posts + reconcile replies are lint-clean and do NOT contain consultant-deck scaffolding
  (assert no bolded "section:" label patterns in a casual reply fixture); grounded facts preserved.

## Acceptance
Friday: Artemis DMs the check-in showing where KRs stand + asks what Jon moved. Jon word-dumps. She maps it to
specific KRs, proposes the updates (cited to his words), and on his "go" the KRs actually update — and she
sounds like a sharp, dry-witted chief of staff, not a consultant. Lead verifies live: fire the check-in,
word-dump the brand-hub/AppScript/skill work, confirm she proposes the right KRs (6/7/8/9/11), say "go",
confirm a KR row changes only then.
