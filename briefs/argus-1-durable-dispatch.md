# ARGUS-1 — Research that survives the turn that asked for it

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this is a queue with
at-least-once delivery and a restart in the middle of it. Getting the claim wrong
double-runs a district or loses it silently.

## The gap

`_dispatch_research` (`artemis/floating_artemis/tools/argus_tools.py`) inserts a
`pending` row and then fires the work with
`loop.create_task(_safe_research_and_post(...))`.

That runs inside the **MCP subprocess** — `python -m artemis.tools.mcp_server`,
spawned per turn by `artemis/providers/claude_code/adapter.py::_complete_with_tools`.
The subprocess exits the moment the CLI turn finishes, killing the task mid-research.

Observed 2026-08-12: four dispatches landed as `pending` and none progressed. All four
only completed after the app was restarted, when `recover_pending_requests`
(`artemis/main.py:185`, lifespan hook) re-fired them **in the long-lived app process**.
The net works. But "research runs when the app next happens to restart" is not a design,
and nobody would notice it silently not running — which is exactly the failure this
package just spent five weeks in.

## Context you need before designing

Read the commit that fixed the adjacent root cause first (`git log` on
`artemis/tools/mcp_server.py`). Summary: contextvars do not cross the subprocess
boundary, `dispatch_research` read `None` for the session, resolved no channel, and
returned `{"status": "dispatched"}` while persisting nothing. Argus had never run once.
Two lessons carry into this slice:

- **A tool must never report success for work it did not do.** Whatever you build, the
  tool's return value must describe what actually happened. "Queued" is honest and is
  what you want here; "dispatched"/"running" is not, unless it is.
- **A passing test asserted that lie.** Do not write tests that encode the contract you
  assumed; assert the observable effect.

## What to build

Make the tool **enqueue only**, and run the work somewhere that outlives a turn.

1. `_dispatch_research` inserts the `pending` row and returns — no `create_task`. Its
   return value says the research is **queued**, not running, and does not promise a
   time.
2. A claimer in the **app process** picks up `pending` rows and runs them. Use the
   existing APScheduler wiring (see `artemis/crisis_content/poller.py` for the
   established pattern in this repo: interval job, in-process overlap lock,
   failure isolated so one bad row cannot stop the loop). A short interval is fine —
   this is a DB poll, not an API call.
3. **Claim atomically.** Two things must be impossible: two workers researching the same
   district at once, and a row silently stuck because a claim never completed. Prefer
   `UPDATE ... SET status='running', claimed_at=now() WHERE id=(SELECT id FROM ...
   WHERE status='pending' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`, or
   argue for something better in your report.
4. **Reclaim stale `running` rows.** A crash mid-research must not park a row forever.
   Add a bounded age after which a `running` row is re-claimable, plus the existing
   `attempts` column as a cap so a permanently failing district gives up as `failed`
   with the reason recorded rather than looping. `attempts` already exists on the table.
5. `recover_pending_requests` **stays** as a startup backstop, but must no longer be the
   mechanism. Keep it, and make sure it cannot double-run a row the claimer already holds.

Migration is **`0116`** with `down_revision = "0115"` if you need a `claimed_at`.
Check the live table first — `id district_key channel_id team_id signal
triggering_signal_id status attempts error created_at completed_at` — and do not add a
column you do not use.

## Hard constraints

- Do not change what Argus researches or how it posts (`artemis/argus/*`,
  `_post_as_callie`). This slice is about *when and where* the work runs.
- Do not touch `artemis/crisis_content/*`, `artemis/pipelines/*`, or
  `artemis/floating_artemis/tools/callie_dm.py`.
- No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- The tool must stay fast to return — a Slack turn is waiting on it.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest artemis/floating_artemis/tests/test_argus_async_dispatch.py -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Both env vars are required; worktrees have no `.env`.

**⚠️ `artemis/floating_artemis/tests/test_argus_async_dispatch.py` inserted rows into the
PRODUCTION database until today** — it mocks the session to `slack-callie-TABC-CABC-_`
and did not patch `artemis.db.SessionLocal`, which reads `ARTEMIS_DB_URL`. There is now an
autouse fixture stopping that. **Do not remove it**, and if you add a test that exercises
the insert, request the `insert_spy` it yields rather than reaching a real DB. Count rows
in `artemis_os.argus_research_requests` before and after your test run and report both
numbers.

## Tests (all required)

- [ ] The tool returns without starting research; no research runs in-process.
- [ ] Its return value says queued and does **not** claim the work is running or done.
- [ ] The claimer picks up a `pending` row and completes it.
- [ ] Two concurrent claimers never take the same row (drive it concurrently, do not
      assert the SQL text).
- [ ] A `running` row older than the stale window is re-claimed.
- [ ] A row failing repeatedly stops at the `attempts` cap, ends `failed`, and records
      why.
- [ ] `recover_pending_requests` cannot double-run a row the claimer holds.
- [ ] A failure on one row does not stop the claimer processing the next.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] **Prove it end to end without an app restart**: dispatch through a real MCP
      subprocess (not a mock — drive it over stdio JSON-RPC: `initialize` →
      `notifications/initialized` → `tools/call`), then show the row reaching `done`
      and the Slack post landing, with the app never restarted. Paste the log lines.
- [ ] Production row count before/after your test run.
- [ ] State plainly what happens if the app dies mid-research.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you think is wrong rather than building to it silently.
