# Worker Brief — Floating Assistant: confirm-path fix + stuck badge + profile-image avatars

**Owner:** terminal. **Lead:** Opus verifies (real browser for UI) + merges. **Isolation:** own worktree
(`worker/fa-polish`), own test DB (name contains `artemis_test`); commit before reporting; do-NOT-merge.
**Status:** READY. Supersedes `briefs/p3-floating-callie-frontend.md` (Task 3 below absorbs it).
Three independent tasks — Task 1 must NOT be blocked by Task 3's asset dependency.

## Task 1 — Confirm-path tool registry is missing integration tools (BUG — gates P3 sends; HIGH)
`resume_after_confirm` in `artemis/floating_artemis/chat.py:1029-1036` rebuilds the tool registry by hand with
only: `register_core_tools`, `register_builders_tools`, `register_system_tools`, `register_okr_tools`,
`register_writing_rules_tools`, `register_marketing_tools`. It OMITS the integration tools (gcal, gmail, slack,
jira, granola). So when an operator approves a layer-3 INTEGRATION write (create_event, send_slack_message/dm,
create_jira_issue/transition, and the upcoming Gmail-send/Slack-send), `auth_registry.get(pending.tool_name)`
returns None → `"Tool '<x>' not found."` and the confirmed action silently fails.
- **Fix:** build the resume registry from the SAME canonical source the main turn path uses
  (`build_authorized_tool_registry` in `floating_artemis/tool_registry.py:24-51`, which registers the full set
  incl. gcal/gmail/slack/jira/granola), passing the resolved `agent_id` and the session's available surfaces —
  do NOT hand-maintain a partial list. Preserve the M3 `trusted_agent_id` gating already there.
- **Test:** propose→confirm a gcal `create_event` (or a slack send) through `resume_after_confirm` and assert
  the tool is FOUND and its impl runs (mock the external client) — not "Tool not found." Add a regression test
  so the resume registry can't silently drift from the main one again.
- **Why it matters:** this gates `briefs/p3-agency-messaging-sends.md` (Gmail/Slack sends are confirmed via
  this exact path) — without it, those sends will propose fine then fail on "go."

## Task 2 — Stuck "2" badge on the floating button (BUG; confirmed it's the FAB badge)
The `.assistant-fab-badge` on `#assistant-fab` shows the count from `/api/floating-artemis/active-runs`
(`floating-artemis-api.js:116`), refreshed every 15s (`features/floating_artemis.js:107,117`). It's stuck at a
phantom "2" because the backing view (`v_floating_artemis_active_runs`, def in
`alembic/versions/0009_floating_artemis.py`) counts runs with `status IN ('running','queued')` that never
cleared (crashed/timed-out runs). There is no UI to clear them.
- **Fix (backend):** stale runs must not count forever. Add a guard so runs older than a cutoff (e.g.
  `started_at < now() - interval '2 hours'`) are excluded from the active-runs count, AND/OR a small periodic
  sweep that flips genuinely-stale `running`/`queued` runs to `failed`. Pick the cleaner of the two; if you add
  a migration, note it so Lead runs `alembic upgrade head` post-merge.
- **Verify:** with ≥1 stale run present, the active-runs count drops to reflect only genuinely-active runs and
  the FAB badge clears. Don't break the badge for genuinely active runs.

## Task 3 — Use agent PROFILE IMAGES on the floating button instead of the generic icon (Jon's request; D11)
The FAB currently shows a generic mark. Render the **agent's profile image**, resolved by identity (D11):
owner → Artemis avatar, marketing user → Callie avatar. This is the UI half of the persona-by-identity work
(M3 already resolves `agent_id` server-side; `getSession` returns `metadata.agent_id`).
- **Assets:** `public/icons/artemis.png` exists (use for Artemis). **There is NO Callie image** — DEPENDENCY:
  Jon will provide a Callie profile image. Until then, degrade gracefully (use a clearly-distinct placeholder /
  initial, NOT the Artemis image) so a marketing user never sees Artemis's face. Flag this dependency in your
  report; do not block Tasks 1-2 on it.
- **Build:** drive the FAB image (and ideally the in-panel header avatar + message attribution) from the
  server-resolved `metadata.agent_id`. Owner→Artemis, else→Callie. Trust the server value; never let the client
  pick the persona.
- **Verify (real browser):** owner sees Artemis image (regression — don't break the existing widget); a
  marketing/`agent_id=callie` session shows the Callie avatar (or the distinct placeholder). Screenshot both.

## Report back
Branch + commit (NOT merged); per-task: what changed (file:line), test/verify evidence (Task 1: confirm a
gcal/slack write resolves; Task 2: badge clears with a stale run present; Task 3: before/after screenshots +
the Callie-image dependency status); any migration added. The floating UI is hard-won — real-browser verify,
don't regress Artemis's existing widget.
