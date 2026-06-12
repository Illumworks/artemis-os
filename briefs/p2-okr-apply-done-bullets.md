# Worker Brief — OKR Apply Must Add the Accomplishment Text to the KR (done_bullets), not just the number

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-done-bullets`. Builds on merged breadcrumb-staging + deterministic-confirm work.
Test DB at head (0082). Real DB-backed tests.

## What Jon caught (live, verified)
The Friday OKR round-trip now works: word-dump → stage → "go" → KR `prog` updates + an `okr_activity` row is
logged with his words. BUT only the **number** moves. The KR's visible **`done_bullets`** and **`note`** (what
OKR Studio actually displays as "what we did") are UNCHANGED — they still show the old May accomplishments.
So KR 9 jumps 50→78 but its bullets never mention the brand hub he just reported. The accomplishment text
lives only in the activity audit log, not on the KR. Jon: "make sure the actual accompanying text was added
to the okrs, not just the numbers changing."

## The fix — write the accomplishment onto the KR when applying
When staged updates are applied (the "go" path in `routes/integrations_slack_events.py`), in addition to
`update_key_result(prog=…)` + `create_activity(…)`, **append a concise, grounded accomplishment bullet to the
KR's `done_bullets`**.

1. **Capture a concise bullet at stage time.** `stage_okr_updates` (`floating_artemis/tools/okr.py`) currently
   takes `{kr_id, progress, basis}`. Add an optional `bullet` per update: a SHORT, scannable one-line summary
   of the accomplishment (the model already writes these well — its Slack proposal said "Brand hub centralizing
   all guidelines and messaging on a live server with version control"). The reconcile context (`chat.py`
   `_get_okr_reconcile_context`) should instruct: for each staged update include a concise `bullet` (grounded
   in Jon's words, no fabrication; if unsure, fall back to a trimmed `basis`). Persist `bullet` in the
   breadcrumb's `staged_updates` alongside `basis`.
2. **Append on apply.** In the route_inbound "go" branch, for each applied update append `bullet` (or a trimmed
   `basis` if no bullet) to that KR's `done_bullets` JSONB list. Read-modify-write the list (don't clobber
   existing bullets — append). Add a repository helper if cleaner (e.g. `append_done_bullet(session, kr_id, text)`),
   keeping it lossless (never drop existing bullets).
3. **Note field (optional but preferred):** leave `note` as-is OR append a short "+ <bullet>" — your call, but
   do NOT overwrite the existing note. Bullets are the primary target; note is secondary.
4. Keep the activity log write as-is (full basis + raw_text) — that audit trail stays.

## Grounding / approval-first
- The bullet must be grounded in Jon's word-dump — NO fabrication. It's a concise restatement of what he said,
  not new claims. (Model-authored at stage time, shown to Jon in the proposal before he says "go", so he sees
  exactly what will be written.)
- Still gated: bullets are written only on the "go" apply, same as the prog change. Nothing written without go.
- Lossless: append to done_bullets, never replace/delete existing bullets.

## Constraints
- No new deps; ruff + mypy strict; DB-backed tests. `done_bullets` is existing JSONB (list[str]); no migration
  needed (the `staged_updates` JSONB already exists and is schemaless — just carry the extra `bullet` key).
- Don't regress: the prog write, activity logging, breadcrumb complete/clear, the deterministic confirm
  classifier, reconcile/opener/morning-brief.

## Tests
- Stage with `bullet` → breadcrumb `staged_updates` carries it; "go" apply → the KR's `done_bullets` gains the
  new bullet AND existing bullets are preserved (assert append, not replace).
- Stage WITHOUT a bullet → apply falls back to a trimmed basis bullet (still grounded, non-empty).
- prog + activity write still happen (no regression); breadcrumb still completes + clears.
- Lossless: a KR with 3 existing bullets has 4 after applying one update.

## Acceptance
After "go", opening the KR in OKR Studio shows BOTH the new percentage AND a new bullet describing the
accomplishment in Jon's words (e.g. KR 9: a bullet about the brand hub), with prior bullets intact. Lead
verifies live + checks `done_bullets` on KR 7/9/11 in the DB.
