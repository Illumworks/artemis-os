# Worker Brief — P5 follow-up: surface the learning loop in the Skills tab + semi-autonomous trigger

**Owner:** Sonnet worker. **Lead:** Opus verifies (real browser for UI) + merges. **Isolation:** own worktree
(`worker/p5-skills-surface`), own test DB (name contains `artemis_test`); commit on the branch; do-NOT-merge.
**Status:** READY. Two tasks (A = UX, B = backend auto-trigger). The P5 backend is already merged + live
(`briefs/p5-learning-loop-skill-capture.md`); do NOT rebuild the distiller/injection/usage — reuse them.

## Context (what exists)
- Distiller endpoint (LIVE): `POST /api/builder/agents/{agent_id}/distill-skills` → creates skill
  `definition_proposals` (kind="skill", proposed_by="self-improvement", status="pending").
- Today those proposals + the "Distill skills" button only surface in the buried **Agents-sidebar Proposals
  Inbox** (`public/js/features/agents.js:renderInboxPanel` ~305; the inbox is a sub-panel, NOT a tab — Jon
  can't find it). The inbox API `/api/builder/inbox` returns `agents_with_new_summaries` +
  `skills_with_pending_proposals`.
- The **Skills tab** is a real Operations nav item: `public/js/features/operations-shell.js:renderSkillsPage`
  (~2400), with an existing **"Proposed"** sub-tab (`tab === "proposed"` ~2421) that today shows proposed
  *Skill records* (from `/api/skills`) — NOT the distiller's skill `definition_proposals`. That mismatch is why
  Jon expected to see them there and doesn't.

## Task A — surface the whole distill flow IN THE SKILLS TAB (Jon's expectation)
Make the Skills tab the home for skill capture + review:
1. In the Skills tab, add a **"Discover skills from recent runs"** action button. On click: fetch
   `/api/builder/inbox`, then for each agent in `agents_with_new_summaries` call
   `api.distillSkills(agent_id)` (the helper already exists in `public/js/core/api.js`); show a summary
   ("Proposed N skill(s) across M agents"); then refresh the Skills view. (Skills tab is skills-global, so this
   global trigger fits better than per-agent here. The per-agent button in the Agents-sidebar inbox can stay.)
2. In the Skills tab's **"Proposed"** sub-tab, ALSO render the pending skill `definition_proposals`
   (`skills_with_pending_proposals` from `/api/builder/inbox`, or fetch them) with **Approve / Reject** actions
   wired to the existing endpoints `POST /api/builder/proposals/{id}/approve` and `/reject`. After approve, the
   skill becomes an approved, agent-assigned Skill (the P5 closure handles that) — refresh so it moves into the
   approved list. Make clear in the UI which entries are "proposed by self-improvement" (use the proposal's
   `proposed_by` / citations).
3. Keep it consistent with the existing Skills-tab markup/classes. Don't remove the Agents-sidebar inbox.

## Task B — semi-autonomous trigger (auto-propose after N successful runs)
So proposals appear in the review queue without anyone clicking:
- After an agent accumulates **N=5** successful runs since its last distillation, auto-run
  `distill_skill_candidates(session, agent_id)` **fire-and-forget** (reuse the `summarize_async`
  `_BACKGROUND_TASKS` pattern in `artemis/builder/trajectory_summarizer.py`). The natural hook is right after a
  trajectory summary is written (`summarize_async` completion path) — check a per-agent run-count since last
  distill and fire when it crosses 5.
- STILL human-gated: it only creates proposals (never auto-approves). Dedup already prevents duplicate proposals
  (so re-firing is safe). Track "last distilled at"/run-count cheaply (a column, a small table, or count runs
  since the newest self-improvement proposal's citation — your choice; keep it simple + migration-light).
- Cost guard: at most one distill (one LLM call) per N runs per agent; never on every run. Fail-safe: a trigger
  error must never break the run or the summarizer.

## Constraints
- Reuse the live distiller + proposal/approve machinery + `api.distillSkills`. No new approval mechanism.
- Human gate stays. Fail-safe everywhere. If you add a migration, note it (Lead applies on prod).
- LLM (in the distiller) already uses `resolve_adapter_async` — don't touch that.

## Verify
- **A (real browser):** Skills tab → "Discover skills from recent runs" triggers distillation; pending skill
  proposals show in the Skills "Proposed" sub-tab with working Approve/Reject; approving moves it to approved.
  Screenshot. Confirm the Agents-sidebar inbox still works (no regression).
- **B:** a test proving that crossing N=5 successful runs for an agent fires the distiller once (mock the
  distiller/LLM), does not fire before 5, does not re-fire every run, and a trigger error doesn't crash the
  summarizer. Assert the EFFECT (distiller invoked / proposal created), not just "no error."
- Report: branch+commit (not merged); files changed (file:line); A screenshots; B test evidence; any migration;
  full test result (verify pre-existing failures vs main); confirm human-gated + fail-safe.
