# FIX115 — Deliverable run advances workspace_state during drafting (honest live status)

**Paste-into:** Codex OR terminal Claude Lead.
**Recommended model / effort:** `gpt-5.4` · `medium`. Small, surgical backend change using the
existing state machine + the existing recompute helper.
**Target branch:** `worker/fix115-workspace-status`
**No migration.** Touches the deliverable-run executor path + reuses `approvals.py`'s existing
`_recompute_workspace_state_from_deliverables` (or the workspace path-walk it uses).
**LOC cap:** ~120.
**Priority:** MEDIUM (UX truthfulness).

## Why
The `marketing.campaign_deliverables` run creates a draft (deliverable → `draft_ready`) but **never
advances `candidate.workspace_state`** — so a campaign reads `pending_content` even while its draft
is sitting at Gate-2 awaiting review (confirmed live: candidates 5/7/8). Misleading status.

## The fix
As the deliverable run progresses, advance `candidate.workspace_state` through its **legal path**
(`pending_content → in_content_preparation → sent_to_writing_studio → content_in_review`):
- When content work begins (e.g. `content_asset_selector` node) → `in_content_preparation`.
- When the draft is enqueued/created in Writing Studio (`writing_studio_adapter` /
  `create_draft_from_candidate`) → `sent_to_writing_studio`.
- When the deliverable reaches `draft_ready` (at/just before Gate-2) → `content_in_review`.

**Reuse, don't reinvent:** `artemis/marketing/routes/approvals.py` already has
`_recompute_workspace_state_from_deliverables` + a `_workspace_path` BFS that walks the legal
transition path safely. Factor that into a shared helper (e.g. move to `artemis/marketing/sends.py`
or a `workspace.py`) and call it from the deliverable-run path so workspace stays in sync with
deliverable status — rather than duplicating transition logic. Must use the `DeliverableState`/
workspace state machine (no direct status writes; respect `test_no_direct_status_writes`).

Idempotent: re-running/resuming must not illegally re-transition (the path-walk already no-ops when
already at target).

## Acceptance
1. A campaign whose deliverable run reaches Gate-2 now shows `content_in_review` (not
   `pending_content`). **Paste DB before/after for a live run.**
2. Approve still works (the existing approve→`all_content_approved` path-walk is unaffected). **Paste.**
3. Tests for the run-side workspace advancement; `./scripts/check.sh` (j5b exempt). **Paste.**
4. **COMMIT on the branch, local git only.** Message ends `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Constraints
- Use the state machine; no direct status writes. Lossless. No new deps. Local-only git.
- Don't touch the outbound-send flag/path (it's OFF) — this is purely the workspace_state sync.
