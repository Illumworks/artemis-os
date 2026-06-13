# Worker Brief — Dismiss action-items ("never mind, drop it")

**Owner:** terminal sub-agent B (Codex rate-limited) — see `briefs/p3-terminal-parallel-orchestration.md`.
**Lead:** Artemis (Opus) verifies live + merges.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/p3-dismiss-action-items`; **commit before
reporting**, do-NOT-merge-report.
**Status:** READY.

## Why
The meeting summarizer's LLM sometimes extracts an action-item that isn't actually relevant/necessary. Jon
wants to **drop** such an item so it (a) leaves his open list and (b) stops nagging him as a commitment. This is
a *drop/irrelevant* action — distinct from "done" (completed) and "snooze" (later).

## Data facts (verified 2026-06-13)
- Action-items live in `meeting_summaries.action_items` (JSON list of `ActionItem{text, owner, due}` —
  **no stable id**, `extra="forbid"`).
- `ingest_meeting_commitments()` creates a commitment per action-item with `source_type="granola_meeting"`,
  `source_id=<granola_id>`, and the action-item embedded in metadata (`action_item: {...}`). So an action-item
  ↔ commitment link = **(`source_id`=granola_id, action_item `text`)**.
- Commitments already have done/snooze reply handlers in `proactivity/commitments.py`.

## What to build
1. **Stable dismissal record (lossless).** Do NOT delete the action-item. Persist a durable dismissal keyed
   stably to the item — recommend a small `meeting_action_item_dismissals` table keyed by
   `(meeting_summary_id, action_item_key)` where `action_item_key` is a content hash of the normalized text
   (stable across reloads; don't rely on list position alone). Migration included (Lead applies post-merge).
2. **Dismiss endpoint.** `POST` to dismiss an action-item → records the dismissal AND **closes/dismisses the
   linked commitment** (match by `source_id`=granola_id + text; reuse the commitments engine's existing
   close/dismiss path — add a `dismissed` terminal state if one doesn't exist, distinct from `done`).
3. **Don't resurrect.** `ingest_meeting_commitments()` (and any "open action-items" read) must **skip
   dismissed items** — re-summarizing the same meeting must NOT bring a dismissed item back. This is a key gate.
4. **Surfaces (recon the current ones first):**
   - **Slack:** alongside the existing done/snooze reply handlers on commitment follow-up DMs, add a
     **dismiss / "not relevant" / "drop"** reply that closes it with **no further follow-up** (distinct from done).
   - **App UI:** wherever action-items / commitments are shown to Jon, add a small **dismiss (×)** control that
     calls the endpoint. (Investigate the current surface; if none exists yet, the Slack path is the priority and
     the UI control can be minimal.)

## Constraints
- Lossless: dismissal is a flag/record, never a hard delete of the action-item or its raw_input.
- Reuse the existing commitments close path + Slack reply-handler pattern; don't fork a parallel system.
- Test with reviewer/owner = Jon only; never ping real people.

## Ship gate (Lead verifies LIVE)
- Dismiss an irrelevant action-item → it leaves the open list, its commitment is closed, and **no further DM
  nags** about it.
- Re-run the summarizer on that meeting → the dismissed item does **not** reappear (durable).
- The raw action-item row is preserved (lossless); done/snooze still work and remain distinct from dismiss.
