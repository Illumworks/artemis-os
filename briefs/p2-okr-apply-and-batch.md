# Worker Brief — Make the OKR Apply Actually Fire (kill the prose-trap) + Batch Multi-KR Confirm

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-apply-batch`. Builds on merged reconcile + opener-digest work. Test DB at head (0081).
Real DB-backed tests.

## The bug Jon hit (live) — verified root cause
The reconcile loop maps the word-dump to the right KRs and proposes good updates IN PROSE, but when Jon
approves ("make these additions"), Artemis claims "the update_okr_kr write tool isn't exposed on this MCP
server." **That is a confabulation.** Verified: a personal Slack DM resolves 47 tools and `update_okr_kr`
(layer 3), `list_okr_objectives`, `complete_okr_checkin` are all present after surface filtering. The tool is
wired.

Root cause is the reconcile context prompt (`artemis/floating_artemis/chat.py` `_get_okr_reconcile_context`,
~lines 274-294). It says *"PROPOSE `update_okr_kr`"* and ends with *"Propose, don't apply."* But **calling the
layer-3 tool IS the gated proposal** — invoking `update_okr_kr` does NOT write; it suspends and asks for "go"
(the conversational confirm). The "Propose, don't apply" wording makes the model NARRATE the updates in prose
and never call the tool, so the layer-3 → "go" → apply machinery never engages. When pushed to apply, it
rationalizes a missing tool instead of calling the one it has.

## Part A — Fix the reconcile context so she actually CALLS the write tool
Rewrite `_get_okr_reconcile_context` instructions so the mechanism is unambiguous:
- To propose a KR update, **CALL `update_okr_kr` (or `update_okr_krs` — Part B)**. Calling the tool IS the
  proposal: it is layer-3, so it **pauses and asks the operator for "go" before writing anything**. It does NOT
  apply on its own. There is no separate "apply" step she must avoid — the gate handles that.
- Remove the misleading "Propose, don't apply." Replace with: "Calling the tool proposes the change and pauses
  for the operator's explicit 'go'; it never writes without that confirm. Do NOT describe the update only in
  prose and wait — make the tool call so the confirmation can happen."
- Explicitly: never claim an OKR write tool is unavailable; the write path exists and is gated, not absent.
- Keep: cite the operator's own words as basis; don't invent KRs/progress; topic-change/done →
  `complete_okr_checkin`; engage with the substance too.

## Part B — Batch confirm so one "go" applies the whole approved set
A word-dump maps to several KRs (Jon's mapped to 3: KR 9, 7, 11). With single `update_okr_kr`, each call
suspends separately → the operator would have to say "go" once per KR. That's the clunk Jon shouldn't hit.
Add a **batch layer-3 tool `update_okr_krs`**:
- Input: a list of `{kr_id, progress, basis}` updates.
- Layer 3 (propose→confirm): the FIRST call suspends ONCE, posts a single proposal listing all KR changes +
  their bases ("I'll set KR9 to 78, KR7 to 62, KR11 to 70 — say go"). On "go" it applies ALL of them in one
  go (each writing its KR + logging an `okr.create_activity` entry "updated via Friday check-in, approved by
  Jon"). On "cancel" none apply.
- Keep single `update_okr_kr` too (for ad-hoc one-off updates outside the check-in).
- The reconcile context (Part A) should tell her to use `update_okr_krs` for the check-in's mapped set (one
  confirm for the batch), not N single calls.
- Reuse the existing conversational-confirm path (`confirmation_store` + `resume_after_confirm`) — the batch
  tool is just another layer-3 tool; one pending, one "go".

## Constraints
- **Approval-first / lossless:** still gated — `update_okr_krs` writes NOTHING until the operator's explicit
  "go". Never fabricate; every update in the batch carries a cited basis. No new deps; ruff + mypy strict.
- Don't regress: reconcile breadcrumb/clear, opener digest, morning brief, idempotency, single update_okr_kr,
  the web `/tool-confirm` path, or the conversational Slack confirm.

## Tests
- A personal-DM reconcile turn where the word-dump maps to KRs results in an actual TOOL CALL
  (`update_okr_krs` or `update_okr_kr`) that SUSPENDS at layer 3 — assert a pending confirmation is created,
  NOT just prose. (This is the regression that would have caught the live bug.)
- `update_okr_krs` with N updates → one pending; on "go"/run → all N KR rows updated + N activity entries
  logged; on "cancel" → zero writes.
- `update_okr_krs` is layer 3 (requires confirm); it does not write on the proposing call.
- Each batched update carries a basis; a batch with an empty/ungrounded update is rejected or that item dropped
  (no fabricated basis).
- Reconcile context no longer contains "Propose, don't apply"; asserts it instructs calling the tool.

## Acceptance
Friday round-trip works end to end: check-in → Jon word-dumps → Artemis maps to the right KRs and CALLS the
(batch) write tool → it pauses with a single proposal → Jon says "go" → all mapped KR rows update at once (and
only then), each citing his words → topic change → she closes the check-in cleanly. No confabulated "missing
tool". Lead verifies live with Jon (the real OKR write happens on his "go").
