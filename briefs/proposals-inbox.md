# Proposals Inbox — Cross-Agent Discovery for the Self-Improvement Loop

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/proposals-inbox`
**Browser smoke owner:** Lead, post-merge — open Agents page, see Inbox section, click "Review" on a row with pending proposals, verify deep-link opens Builder session pre-scoped to that agent.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~500 (new UI section + API extension + tests).
**Parallel with:** CC18 (`worker/cc18-builder-target-id`). Both touch `agents.js` but in different sections; rebase before merge if CC18 lands first.
**Priority:** HIGH — without this, the producer side of the self-improvement loop (CC10-CC17 + CC18) has no discovery surface. Operators don't know there's anything to review.

---

## Design decisions (Lead leans, Jon sign-off before fire)

These shape the build; please confirm or override at the top of the brief before terminal-Lead spawns the worker.

1. **Placement** — Panel at the **top of the Agents page**, before the agent roster. Operators land on Agents anyway; the Inbox surfaces *"here's what needs your attention"* first. *Per Jon's 2026-05-28 ask: "in the agents box that shows them all instead of having to [click into one] individually."* — **Lean: panel at top of Agents page.** ✓
2. **What it shows** — Both *pending proposals* (definition_proposals.status='pending') AND *agents with new summaries since last review* (so even before a proposal lands, you see "scout_X has 3 new summaries you haven't looked at"). **Lean: both.**
3. **Last-seen tracking** — Single-user dev mode today, multi-user later. **Lean: per-agent `last_reviewed_at` column on `agents` table; single-user updates it on Builder session open.** When multi-user lands, gets a join table; the column stays as a fallback.
4. **Cross-kind unification** — One Inbox surface shows both agent-kind and skill-kind proposals. (Skills page can keep its inline "proposed" pill too.) **Lean: unify.**
5. **Approve/Reject ergonomics** — Inline Approve/Reject buttons on Inbox rows for low-friction action; "Open in Builder" button for full context + multi-turn refinement. **Lean: both inline + open.**

If any of these don't match what you want, edit the brief or pass a verbal correction before terminal-Lead fires.

---

## Why this exists

After CC10-CC17 closed the producer side, the consumer-side audit (`docs/self-improvement-consumer-side.md`) found:
- Backend complete: `definition_proposals` table, approve/reject routes, `engine.commit()` real and structurally correct.
- Builder right-rail rendering: works per-session.
- **MISSING: discovery.** Agent profile shows blueprint but not summaries. *"Edit with Builder"* opens a generic session list. No surface tells operators *"the Builder has 11 fresh summaries to review across 7 agents."* So even with everything else fixed, no one knows to look.

CC18 (in flight) fixes the per-agent Builder targeting. **This brief adds the cross-agent discovery surface** that makes the producer side's output actually visible.

---

## Scope

### Part A — Backend: aggregate query + new endpoints

Add 2 routes (or extend existing) under `/api/builder/`:

**`GET /api/builder/inbox`** — returns the cross-agent aggregate:
```json
{
  "agents_with_pending_proposals": [
    {
      "agent_id": "marketing.scout.regional_news",
      "agent_name": "Regional News Scout",
      "pending_count": 2,
      "last_proposal_at": "2026-05-29T10:31:14Z",
      "last_summary_at": "2026-05-29T10:35:02Z",
      "last_reviewed_at": null,
      "kind": "agent"
    },
    ...
  ],
  "agents_with_new_summaries": [
    {
      "agent_id": "marketing.scout.federal_funding",
      "agent_name": "Federal Funding Scout",
      "new_summary_count": 5,
      "last_summary_at": "2026-05-29T10:35:02Z",
      "last_reviewed_at": "2026-05-28T14:00:00Z",
      "kind": "agent"
    },
    ...
  ],
  "skills_with_pending_proposals": [...]
}
```

Aggregation queries:
- *Agents with pending proposals*: `JOIN agents ON definition_proposals.target_id WHERE status='pending' AND kind IN ('agent','skill') GROUP BY target_id`.
- *Agents with new summaries since `last_reviewed_at`*: `JOIN agent_runs ON agent_run_trajectory_summaries.run_id WHERE agent_runs.agent_id = X AND summaries.generated_at > agents.last_reviewed_at GROUP BY agent_id`.

**`POST /api/builder/agents/{agent_id}/mark-reviewed`** — operator action when entering Builder for an agent (called from CC18's per-agent session-open path) → updates `agents.last_reviewed_at = now()`.

Add a column to `agents`:
```sql
ALTER TABLE agents ADD COLUMN last_reviewed_at TIMESTAMP WITH TIME ZONE NULL;
```
Alembic migration. NULL default = "never reviewed."

### Part B — Frontend: Inbox panel on the Agents page

**Visual design note from Jon (2026-05-29):** the existing Agents-page hero (the *"Who Does Work / A roster for scanning..."* block + 4 buttons + 4 stat cards above the roster) is *too big and not functional*. The Inbox should land at the top and ideally make the page feel **more functional, less hero-heavy**. You have permission to **shrink or restructure the existing hero** (e.g., compress stats into a single denser bar; move "Build with Agent-Builder" / "Edit with Builder" buttons into a tighter action row; remove redundant descriptive text) to make the Inbox the primary above-the-fold surface without the page feeling visually bloated. Use your aesthetic judgment within the existing design system; Lead will verify the result in-browser post-merge. **Goal: when an operator lands on Agents, the first thing they see is what needs attention (Inbox), not a marketing-style hero.**

In `public/js/features/agents.js`, add a new section at the top of the Agents page (above the roster, ideally taking the visual real estate the oversized hero currently uses). The panel:

- **Header:** "Inbox — Agent Reviews Pending" with a count badge.
- **Section 1: Pending Proposals** — list rows for `agents_with_pending_proposals`:
  - Agent name + slug.
  - "N pending proposal(s)" pill.
  - Last summary timestamp.
  - **Inline Approve / Reject buttons** (POST to `/api/builder/proposals/{id}/approve` or `/reject`). On approve: row disappears, success toast.
  - **"Review with Builder" button** — opens Builder session pre-scoped to this agent (uses CC18's `target_id` mechanism — call `builderCreateSession({ builder_kind: "agent", target_id: <agent_id> })`, then navigate to `#/agents/builder/<session_id>`).
- **Section 2: New Summaries to Review** — list rows for `agents_with_new_summaries`:
  - Agent name + slug.
  - "N new summaries since you last reviewed" pill.
  - **"Review with Builder" button** — same as above. Opening Builder for an agent should call `POST /agents/{agent_id}/mark-reviewed` so the row drops out of the "new" list.
- **Empty state:** if both lists are empty, show *"Nothing to review right now. The Builder will surface proposals as your agents produce new trajectory summaries."*

CSS lives in `public/css/features/agents.css` (or whatever the existing agents-page CSS file is — match the existing pattern).

### Part C — Sidebar badge (small but high-leverage)

In `public/js/features/operations-shell.js` (the Operations sidebar nav), add a count badge to the "Agents" nav item showing the total pending-proposal count. Pulls from `GET /api/builder/inbox` (cached for 30s; refresh on Builder session close or approval).

This is the *"you have N to review"* notification — without it, operators have to remember to check.

### Part D — Tests

`artemis/builder/tests/test_inbox_routes.py`:
1. `GET /api/builder/inbox` returns the right shape with seeded fixtures.
2. `agents_with_pending_proposals` aggregates correctly across multiple agents.
3. `agents_with_new_summaries` correctly filters by `last_reviewed_at` (NULL = always include).
4. `POST /agents/{id}/mark-reviewed` updates the column + returns success.

`public/js/features/tests/test_agents_inbox.spec.js` (or Python integration if no JS test infra):
5. Approve button on an inline Inbox row calls the right approve endpoint.
6. Reject button on an inline row calls reject.
7. "Review with Builder" deep-link creates a session with `target_id` set AND marks reviewed.

### Part E — Migration

Alembic migration `0047_agents_last_reviewed_at.py`:
```python
op.add_column("agents", sa.Column("last_reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True))
```
Idempotent. No backfill needed (NULL = never reviewed).

---

## Files owned

- NEW: `alembic/versions/0047_agents_last_reviewed_at.py` (migration)
- EDIT: `artemis/builders/models.py` (add `last_reviewed_at` to `Agent`)
- NEW or EDIT: `artemis/builder/routes.py` (the 2 new routes)
- NEW: `artemis/builder/repository.py` extensions for the aggregate query (or inline if simple)
- EDIT: `public/js/features/agents.js` (add Inbox panel section — **NOT** the "Edit with Builder" button handler, which is CC18's territory; coordinate via comment)
- EDIT: `public/css/features/agents.css` (or equivalent — Inbox panel styling)
- EDIT: `public/js/features/operations-shell.js` (sidebar badge for Agents)
- NEW: `artemis/builder/tests/test_inbox_routes.py`

**Coordination with CC18 (`worker/cc18-builder-target-id`):**
- Both touch `agents.js`. CC18 modifies the existing "Edit with Builder" click handler; this brief ADDS a new Inbox panel section. Different code paths in the same file.
- If CC18 lands first (likely — smaller), rebase before submit.
- The Inbox's "Review with Builder" button uses the SAME `builderCreateSession({target_id})` pattern CC18 establishes. Reuse the helper if CC18 factors one out.

---

## Acceptance criteria

1. Migration applies: `uv run alembic upgrade head` shows `0047_agents_last_reviewed_at`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/ -v` — all pass incl. new inbox tests. **Paste.**
3. `GET /api/builder/inbox` returns the right shape with current DB state. **Paste the JSON.**
4. **Manual UI smoke (post-merge, Lead does this):**
   - Open Operations → Agents
   - Inbox panel renders at top of page
   - If `marketing.scout.regional_news` has pending proposals or new summaries, it appears in the right section
   - Click "Review with Builder" → new session created with correct `target_id`; navigates to Builder; `mark-reviewed` API called
   - (Sidebar badge — bonus): "Agents" nav item shows count if proposals are pending
5. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
6. `git diff --stat` + `git log --oneline -1` on `worker/proposals-inbox`. **Paste.**

---

## Hard constraints

- Migration is additive (new column, NULL default). No data backfill. No existing data touched.
- Inline Approve/Reject use the EXISTING `/api/builder/proposals/{id}/approve|reject` routes — don't duplicate.
- "Review with Builder" MUST set `target_id` on session creation (CC18's pattern). If CC18 hasn't merged yet, this brief independently passes `target_id` per the existing API contract (which already supports it).
- Don't unify the Skills "proposed" pill rendering into the Inbox in this brief — leave the Skills page as-is; the Inbox shows skill proposals as a separate section.
- **Aesthetics:** shrink/restructure the existing oversized hero so the Inbox is the primary above-the-fold surface. Use existing design tokens / CSS variables / components. Don't introduce new visual languages. Lead does the eyes-on-glass post-merge.
- Local-only git. Worker commits on `worker/proposals-inbox`; terminal-Lead merges after Lead approves.

---

## Report-back format

```
Proposals Inbox report
1. Commit / branch / worktree
2. LOC diff stats
3. Migration applies cleanly
4. Test pass summary
5. API smoke: GET /api/builder/inbox JSON
6. Coordination with CC18: rebased or independent (note if any agents.js conflict and how resolved)
7. check.sh summary
8. Anything surprising — especially the aggregate query shape, any unexpected `last_reviewed_at` semantics
```

---

**Worker: this brief surfaces the producer side's output (CC10-CC17) to operators. Without this, summaries pile up unread in the DB. The mechanism is mostly assembly — backend supports nearly everything via existing routes; this brief adds the cross-agent aggregate + the panel UI + the sidebar badge. The "Review with Builder" deep-link relies on CC18's session-with-target-id flow; both can coexist via the same API contract.**
