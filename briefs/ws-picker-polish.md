# Worker Brief — Writing Studio picker polish (delete-draft + long-folder-name layout)

**Owner:** terminal (FE — Codex is on P3). **Lead:** Artemis (Opus) verifies live + merges.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/ws-picker-polish`; **commit your work on
the branch before reporting**, then do-NOT-merge-report. **Hands-off the selection-toolbar logic.**
**Status:** READY. FE-only (`composer-v5.js` drafts picker + its CSS).

## 1. Add a delete-draft affordance in the picker
The picker has rename for drafts but **no delete**. The backend already exists and is **lossless**:
`DELETE /api/writing-studio/drafts/{id}` **soft-archives** (`status='archived'`, row preserved). So this is
FE-only:
- Add a trash icon on each **draft** row in the picker (mirror the existing rename affordance), → calls the
  DELETE endpoint → on success, remove the draft from the list / re-fetch.
- **Light confirm** before deleting ("Archive this draft?") — it's recoverable (soft-archive), but a draft is
  more valuable than a rule, so confirm to avoid accidental clicks.
- After delete, if it was the open draft, fall back to the v5 empty state / next draft (reuse the existing
  no-draft handling).

## 2. Fix long folder names hiding the rename + trash icons
On folders with **long names**, the rename + trashcan action icons get pushed off and don't show/aren't
clickable. Fix the row layout so the **name truncates** (ellipsis) and the **action icons stay pinned and
visible**:
- Folder-row = flex; the name gets `flex: 1 1 auto; min-width: 0; text-overflow: ellipsis; overflow: hidden;
  white-space: nowrap`; the actions cluster is fixed-width and never shrinks. (Same pattern wherever draft
  rows already truncate.)
- Verify with a deliberately long folder name (e.g. "Texas HB27 Personal Financial Literacy…").

## Constraints
- FE only; reuse the existing picker action patterns + endpoints. Don't touch the selection-toolbar code.
- Match surrounding picker code/style.

## Ship gate (Lead verifies LIVE)
- Delete a draft from the picker → confirm prompt → it disappears from the list; DB shows `status='archived'`
  (recoverable, not hard-deleted).
- A folder with a long name shows **both** rename and trash icons, clickable; the name truncates cleanly.
