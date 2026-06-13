# Worker Brief — WS5 Residuals Cleanup (close the loose ends)

**Owner:** terminal or Codex (FE-heavy + one tiny backend guard). **Lead:** Artemis (Opus) verifies live with
2 browsers + merges. **Status:** READY.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/ws5-residuals`, do-NOT-merge-report.
**Context:** WS5 P0–P4 are shipped + live; these are the three documented residuals in
`docs/ws5-coedit-architecture.md`. **Hands-off selection toolbar** (no changes to `updateSelectionState`/
`positionNearSelection`/`show`/`hide`/`handleOutsidePointerDown`).

## 1. Collab-aware undo/redo (most user-facing)
**Problem:** under *simultaneous* editing, `Cmd+Z` may undo a peer's recent edit, not just the local user's.
**First verify the current behavior** (2 clients, both typing): does undo already only affect local edits?
The vendored `prosemirror-collab` already sets `addToHistory:false` on remote steps (`receiveTransaction`) and
`historyPreserveItems:true`, so the history plugin *should* skip remote steps — confirm whether the residual
is real or already handled by the integration. **If real,** make undo collab-aware: ensure the editor's
`history()` config (via `exampleSetup`) only undoes local steps and rebases correctly after remote steps land
(the collab × prosemirror-history interplay). Do it in the collab layer, not the toolbar.
- **Ship gate (Lead, 2 browsers):** A and B both edit; A presses undo → only A's own edit reverts, B's text is
  untouched in both windows.

## 2. Socket teardown on reload/navigate
**Problem:** the per-mount collab socket isn't fully torn down on composer unmount/navigate, so presence
avatars inflate when a user reloads the same draft repeatedly in a session (normal single-open = correct count).
**Fix:** close the collab socket in the composer's `destroy()` and on every re-mount/draft-switch (mirror how
the editor/plugins are torn down); make sure a navigated-away client leaves the room promptly.
- **Ship gate (Lead, 2 browsers):** open A + B → exactly 2 avatars; reload A 3× → still exactly 2 (no inflation);
  close A → drops to 1.

## 3. Multi-worker guard (defensive — NOT the full fan-out)
**Problem:** collab rooms are in-memory per-process; with >1 uvicorn worker they'd clobber. Full fan-out
(Redis pub/sub or Postgres LISTEN/NOTIFY, one elected flusher per draft) is premature — prod runs 1 worker.
**Close the loose end cheaply:** on app startup, if the server is configured with >1 worker, **log a loud
WARNING** that WS5 collab requires single-worker until fan-out lands (so a future scale-out can't silently
break co-editing). Reference `docs/ws5-coedit-architecture.md` R10. No fan-out build in this brief.
- **Ship gate (Lead):** the warning fires under a simulated >1-worker config; absent at 1 worker. (Lead checks
  at source + a quick run.)

## Acceptance
All three closed: undo is local-only (verified 2-browser), reload doesn't inflate presence (verified
2-browser), and a >1-worker config logs the guard warning. Lead verifies live + merges; updates the residuals
section of `docs/ws5-coedit-architecture.md` to ✅ on merge.
