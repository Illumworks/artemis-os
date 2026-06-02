# CC18 — Wire `target_id` from Agent Profile into Builder Session

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`) OR Codex direct — small enough either way.
**Target branch:** `worker/cc18-builder-target-id` (or Codex direct on `lead/j6a-granola-integration`)
**Browser smoke owner:** Lead, post-merge — open Builder from an agent profile, verify the session is target-scoped + Builder calls `read_recent_runs` automatically.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~80.
**Priority:** HIGH — the structural piece that unblocks the consumer side of the self-improvement loop. Pairs with the Proposals Inbox (the discovery layer).

---

## Why this exists — the consumer-side audit finding

Empirically verified 2026-05-28 (Lead audit, see `docs/self-improvement-consumer-side.md`):

- **Backend supports it.** `POST /api/builder/sessions` accepts `target_id` in the body (`artemis/builder/routes.py:113` — `target_id=body.target_id`). Per the agent_builder.py prompt, if a session has a `target_id`, the Builder LLM is instructed to call `read_recent_runs(target_id)` first and lead with *"I've reviewed your last N runs."*
- **UI doesn't pass it.** `public/js/features/agent-builder.js:37` calls `api.builderCreateSession({ builder_kind: "agent" })` with no `target_id`. Every Builder session created via the UI ends up agent-less.
- **Net effect:** even with the producer side closed (CC10-CC17 landed, summaries diagnostic + truthful), the Builder LLM has no `agent_id` to anchor against, so it never reads recent runs and never proposes. The 1-line bug that, alongside the producer-side hollowness, kept `definition_proposals = 0` historically.

This brief wires the `target_id` through from "click Edit with Builder on an agent's profile" to `POST /sessions` body.

---

## Scope

### Part A — Investigate (~10 LOC findings)

Before coding, answer in the report:

1. Where is `api.builderCreateSession()` called from? List every call site. (`agent-builder.js:37` is one; are there others — e.g. pipeline canvas, floating Artemis?)
2. When the user clicks "Edit with Builder" from an agent profile, what's the navigation path? Is the selected agent's `agent_id` (the slug, e.g. `marketing.scout.regional_news`) or the int PK already in the UI state at that point?
3. Does `builder_sessions.target_id` store the agent's int PK or the slug string? Check the column type + how `get_trajectory_summaries_for_agent` looks up runs.

Paste answers as Part A.

### Part B — Pass `target_id` when entering Builder from an agent

In the agent-profile flow (where "Edit with Builder" lives):
- Capture the currently-selected agent's identifier when the user clicks "Edit with Builder."
- Pass it as `target_id` to `builderCreateSession({ builder_kind: "agent", target_id: <id> })`.
- The form of the identifier (int PK vs slug) matches what `builder_sessions.target_id` expects per Part A's finding.

If the call comes from a context that doesn't have an agent selected (the generic "New session" button in the Builder's own sidebar), keep the current behavior — pass no `target_id` so the user can still create a fresh creation-style session. This is an additive, opt-in path.

### Part C — Confirm the Builder LLM acts on `target_id`

After the user enters Builder from an agent profile:
- The session is created with `target_id` set.
- When the user sends any message (or even just opens the session), the Builder's prompt (which already says *"If the user is opening an existing agent (edit session): Start by calling `read_recent_runs()` to load trajectory summaries"*) should fire correctly because the session has the target.
- Verify by adding ONE small UI hint: if the session has a `target_id`, show *"Reviewing: <agent_name>"* near the top of the chat so the user knows the context.

The Builder's tool-calling logic should already handle this — the prompt is in place, `read_recent_runs` already exists. We're just unblocking the data flow.

### Part D — Tests

`public/js/features/tests/test_agent_builder_target_id.spec.js` (or wherever JS tests live; if there's no UI test infra, a Python integration test calling the route is fine):
1. POSTing to `/api/builder/sessions` with `target_id` set → row created with that `target_id`.
2. Frontend: clicking "Edit with Builder" from an agent profile dispatches `builderCreateSession` with the right `target_id`. (If no UI test harness exists, skip; the Python route test plus manual smoke is enough.)

---

## Files owned

- EDIT: `public/js/features/agent-builder.js` (the `builderCreateSession` call + the "Edit with Builder" handler)
- EDIT: `public/js/features/agents.js` (where the "Edit with Builder" button is wired from the agent profile — confirm in Part A)
- EDIT or NEW: a small test (JS or Python — Worker picks the cleanest path)

**Do not touch:** the backend route (already supports `target_id`), the Builder LLM prompt, the trajectory_summarizer, the propose/approve flow. UI wire-up only.

---

## Acceptance criteria

1. Part A findings in the report.
2. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
3. **Manual proof (Worker can run if it has a server, else Lead does it):**
   - Open the app → Operations → Agents → click `marketing.scout.regional_news` → click "Edit with Builder"
   - A new builder session is created
   - `psql -c "SELECT id, target_id FROM builder_sessions ORDER BY id DESC LIMIT 1;"` shows `target_id` matching the agent
   - Optional: the new session's UI shows something like *"Reviewing: marketing.scout.regional_news"*
   **Paste the SQL output + a screenshot or text snippet from the UI.**
4. `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- Don't break the "New" creation-style flow — that path keeps creating agent-less sessions for users who want to build a NEW agent.
- The `target_id` form (int PK vs slug) must match the column's existing type. Confirm in Part A.
- Local-only git.

---

## Report-back format

```
CC18 — Wire target_id from Agent Profile report
1. Commit / branch / worktree
2. LOC diff stats
3. Part A findings — call sites, navigation path, target_id type
4. UI changes (paste the key diff)
5. Manual proof — SQL + UI confirmation that a session created from an agent has target_id set
6. check.sh summary
7. Anything surprising
```

---

**Worker: this is the smallest fix on the consumer side. Backend supports target_id fully; the UI just never passed it. After CC18, opening Builder from an agent profile means the Builder LLM has an agent_id to anchor `read_recent_runs()` against — the trajectory summaries produced by CC10-CC17 finally get read. Pairs with the Proposals Inbox stream (next brief) which handles cross-agent discovery.**
