# Worker Brief — Callie C3c: retired Artemis-DM marketing history → Callie's memory

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/callie-c3c-history-handoff`. **Plan:** `docs/callie-build-plan.md` C3c.
**Careful slice — investigate scope semantics first.**

## Why
Slice-1 retired the marketing conversation that had accumulated in Artemis's DM (session
`slack-T4MNZ8CCV-D0AN8CCJC4C-_`, ~246 messages) from her active context and tagged its metadata
`retired_history_owner=callie`, `callie_handoff_pending=true`. That marketing context should now become
**Callie's** memory so she has the history of the work.

## Step 1 — INVESTIGATE before building (flagged unknown)
Trace the memory keystone scope model: `artemis/floating_artemis/memory.py` (write_turn_drawer),
`artemis/memory/store.py` (write path), `artemis/memory/retrieval.py` (how observations are scoped/filtered),
`artemis/memory/schemas.py` (Scope). Determine:
- How memory is scoped per agent (is there an agent/owner scope dimension? does Callie's retrieval see only
  her scope?).
- Whether "hand to Callie" = copy observations into Callie's scope, or re-key, or replay the messages through
  Callie's memory write path.
Report the finding to Lead before the write step if the model is ambiguous.

## Step 2 — Ingest (lossless)
Write the retired session's marketing content into Callie's memory scope (so her retrieval surfaces it).
Do NOT delete or move the original Artemis session/messages (already retired from active context; keep for
provenance). Copy/derive into Callie's scope.

## Step 3 — Mark handoff complete
Update the retired session metadata: `callie_handoff_pending=false` + a `handed_to_callie_at` timestamp.
Keep `retired_history_owner=callie`.

## Constraints
- Lossless (no deletes; supersession/copy only). House rule.
- No new deps; ruff + mypy strict; DB-backed tests now possible (test DB repaired).

## Tests
- After handoff, a Callie memory retrieval surfaces content from the retired session; the original Artemis
  session/messages still exist; `callie_handoff_pending` is cleared.

## Acceptance
Callie can recall the prior marketing/signal work from the retired DM in her own context; nothing deleted;
handoff flag cleared. Lead verifies: ask Callie about a topic from the retired history; she recalls it.
