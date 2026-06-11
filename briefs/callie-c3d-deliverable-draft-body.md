# Worker Brief — Callie C3d: deliverable → editable draft body alignment (QW2)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/callie-c3d-draft-body`. **Plan:** `docs/callie-build-plan.md` C3d.
**NO external backend / NO Google Docs** (earlier framing retracted — that's a separate parked idea).

## Why
A Writing Studio "draft" IS a `campaign_deliverables` row (invoke.py:54), its content in
`deliverable_metadata`, surfaced to the composer via `_latest_draft_content`. For campaign #18, the
deliverables pipeline generated real bodies (deliverable 42's body is in metadata) but they don't render in
the editor because the pipeline wrote the body to a field/shape that `_latest_draft_content` does NOT read
(42 had it, 43-45 didn't). Result: drafts look empty though content exists.

## Scope
1. **Trace the read↔write mismatch.** Find exactly what `_latest_draft_content` reads from
   `deliverable_metadata` (the "live_content"/latest-version body shape) vs. what the deliverables pipeline
   (`artemis/marketing/writing_studio/invoke.py` create path + the content-draft node / agent_executor) WRITES.
   They're misaligned — that's the bug.
2. **Align them** so the composed body lands where the composer reads it, for ALL generated deliverables (not
   just whichever one happened to match). Prefer fixing the WRITE side (pipeline writes to the canonical
   field `_latest_draft_content` expects) over special-casing the read.
3. **Backfill #18** (demo): ensure deliverables 42-45 render their bodies in the editor. If they can't be
   backfilled cleanly from existing metadata, document why; do not fabricate content.

## Constraints
- The `external.py` Stub/Real adapter + `ARTEMIS_WRITING_STUDIO_URL/TOKEN` are NOT involved — leave them.
- Lossless; no new deps; ruff + mypy strict; DB-backed tests now possible (test DB repaired).

## Tests
- A deliverable created via the pipeline path has its composed body readable by `_latest_draft_content`
  (round-trip: pipeline writes body → composer read returns it).
- Regression: existing writing-studio draft CRUD tests stay green.

## Acceptance
Opening a campaign #18 draft in the Writing Studio shows its real body (not empty); new deliverables render
their composed content in the editor. Lead verifies in a browser (open a #18 draft → body present).
