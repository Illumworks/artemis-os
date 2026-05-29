# Self-Improvement Loop — Consumer Side (Proposal Review + Approval)

**Audit date:** 2026-05-28. Captures how the Builder's proposals are surfaced + accepted/rejected, and what's missing.

## How it works (the flow)

```
Agent run completes
  → trajectory_summarizer writes a summary (what_worked/what_stalled/what_was_missing)
  → user opens Agent Builder for that agent
  → Builder reads recent trajectory_summaries → notices patterns
  → Builder calls propose() with kind="agent" (definition update) or kind="skill" (new capability)
  → Proposal lands in definition_proposals with status="pending" + citations (run IDs)
  → User sees proposal card in Builder right rail
  → User clicks Approve → POST /api/builder/proposals/{id}/approve → engine.commit() → applied to agents/skills table
  → OR User clicks Reject → POST .../reject → status="rejected", no change applied
```

The Builder's prompt **explicitly forbids** the agent from calling `commit()` directly — *the human owns approval*. Agents propose; humans dispose.

## Surfaces today

| Surface | Scope | File |
|---|---|---|
| Builder chat right rail | Per-agent — pending proposals for the current session | `public/js/features/agent-builder.js` |
| Pipeline AI Panel | Pipeline-level proposals | `public/js/components/pipeline-ai-panel.js` |
| Skills page | Cross-cutting "N proposed" pill (skill-kind only) | `public/js/features/operations-shell.js` |
| Writing Studio | Pending proposals for writing rules | `public/js/features/writing-studio.js` |

## Backend

| Endpoint | Purpose |
|---|---|
| `GET /api/builder/proposals?status=pending&kind=...` | List proposals (filterable) |
| `POST /api/builder/proposals/{id}/approve` | Commit `proposed_definition` JSONB to real table |
| `POST /api/builder/proposals/{id}/reject` | Mark rejected; no application |

Citations live in `definition_proposals.citations` (JSONB) — the run IDs the proposal is based on. The UI shows them on the proposal card so users see the evidence.

## What's never fired (the dormant state)

`definition_proposals` is at **0 rows** for the lifetime of the app — the producer side (trajectory summaries) has been broken for the same lifetime. The UI is dormant *because there's nothing to show*. Once **CC14 + CC15** land, summaries flow → Builder reads them → proposals start landing → the UI surface lights up for the first time.

## Gaps — empirically confirmed 2026-05-28 (not theoretical)

After CC10-CC17 closed the producer side (summaries diagnostic and truthful), Lead opened the Builder UI to verify the consumer side. **The discovery flow doesn't exist:**

1. Click an agent in the roster (e.g. `marketing.scout.regional_news`) → agent profile loads. **Trajectory summaries are NOT shown on the profile.**
2. Click "Edit with Builder" → opens the GENERIC Builder session list. **Sessions are NOT auto-linked to the selected agent.** Existing sessions are user-initiated creation conversations (*"build me a small agent that summarizes my emails"*).
3. Click "New" → blank session, prompt says *"Describe the agent you want to build"*. **Creation-first UX, not edit-first.** No agent picker. No list of agents-with-recent-summaries.
4. To actually trigger a review, the user has to *type* something like *"Review marketing.scout.regional_news and propose improvements based on its recent runs."* The Builder LLM then calls `read_recent_runs()` and `propose()` — the mechanism works, but the user has to KNOW to do this.

**Net effect:** even after CC10-CC17 made summaries diagnostic + truthful, the loop doesn't fire end-to-end because **no one knows to ask the Builder about the agents that have new summaries.** `definition_proposals = 0` historically wasn't only the producer-side bug; it's ALSO the consumer-side discovery gap.

### Highest-priority gap — the Proposals Inbox (task #32, now empirically required before SP1)

A cross-agent "Agents with summaries to review" surface, with proposal counts and a one-click "Review with Builder" that opens a session **pre-scoped to that agent** (Builder reads `read_recent_runs` automatically as the prompt instructs).

Without this: summaries pile up in the DB unread.

2. **No diff/preview of what changes.** The proposal card shows the proposed JSONB but not a side-by-side diff vs the current definition. Review is harder than it should be. Polish.

3. **No reason-for-rejection capture.** Reject is just a button click; we don't capture *why*. That feedback would feed trajectory summaries usefully (*"Builder proposed X, user rejected because Y"* → Builder learns from rejection).

4. **No notification when proposals arrive.** No badge, no count in the nav, no Slack-DM-on-new-proposal. Operators have to remember to check.

## Recommended sequencing

- **Now (in flight): CC14 + CC15** — make summaries land reliably. Without this, none of the consumer side has data.
- **Then SP1** — Signal Playbook (the marketing-criteria editing surface).
- **Then a "Proposals Inbox + Notification" stream** — addresses gaps #1 + #4 above. The single most important addition for operator-driven self-improvement.
- **Then polish** — gaps #2 + #3 once we have real usage feedback.

## Architectural notes

- Proposals are durable (table) — survive session loss, app restart.
- Approval applies the JSONB transactionally. Lossless invariant respected (no destructive delete; agent rows are updated, but the proposal row itself preserves the old state's "before" if needed — verify in `engine.commit`).
- The Builder is domain-agnostic (verified 2026-05-28) — this proposal flow works for any agent in the system, not just marketing.
- The skill co-proposal path (kind="skill") creates new Skills rows on approval — that's how agents extend their own capability surface over time, matching the original "agents suggest skills" goal.
