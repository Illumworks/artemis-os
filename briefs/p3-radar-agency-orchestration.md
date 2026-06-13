# Orchestration — radar + agency-writes in one run (two parallel lanes)

**For:** terminal (orchestrator), or Codex when limits reset. Runs the two remaining P3 slices as **two parallel
lanes**, each in its own worktree + own test DB. **Lead:** Artemis (Opus) merges both and holds the Slack seam.
Do-NOT-merge — build + verify + report.

## The two lanes
| Lane | Spec brief | Branch | Owns |
|---|---|---|---|
| **R — awaiting-reply radar** | `briefs/p3-awaiting-reply-radar.md` | `worker/p3-awaiting-reply-radar` | Slack **user-token** OAuth (`search:read`+`users:read`+`chat:write`), Slack mention search, Gmail-unanswered, proactivity surfacing |
| **G — agency-writes** | `briefs/p3-agency-writes.md` | `worker/p3-agency-writes` | the propose→confirm **gate** + **2a calendar** + **2c Jira** |

## Scope discipline (this is why they can run in parallel)
- **Lane G builds the FOUNDATION (gate) + 2a (calendar) + 2c (Jira) only.** **DEFER 2b (Slack-send)** — it reuses
  Lane R's Slack user token, so Lead wires it after R lands. **DEFER 2d (Gmail-send)** — needs a `gmail.send`
  re-consent. Build the gate generically (action_type-driven) so 2b/2d slot in later with no rework.
- **Lane R owns ALL Slack-token work.** Lane G must NOT add its own Slack OAuth/token — it consumes what R
  stores.

## The one shared seam (Lead resolves at merge)
Both lanes touch the **Slack reply-handler dispatch**: R adds a radar "dismiss" route (reuse the existing dismiss
pattern), G adds the gate's **approve / reject** route. Keep each addition **localized + additive** (a new
matcher + handler, no rewrite of the dispatch loop). If they collide, it's a trivial 3-way — Lead merges it.

## Guardrails (AGENTS.md rule 6 + hard-won lessons)
1. Each lane in its **own worktree**, off current `main`. Commit on the branch **before reporting**.
2. Each lane uses its **OWN test DB** — never share `artemis_test` (parallel TRUNCATE = deadlock/wipe). Use
   `artemis_test_radar` (R) and `artemis_test_agency` (G); migrate each from its own worktree; set both
   `ARTEMIS_DB_URL` + `ARTEMIS_TEST_DB_URL` to it for pytest.
3. Both add a migration (R may add a user-token column/table; G adds `proposed_actions`). Lead applies both on
   prod post-merge + restarts the launchd service.
4. Do not edit main's working tree. Do not merge. Report each branch + test result; Lead merges both.

## Acceptance (each lane verifies before reporting)
- **R:** Slack user-token flow stores `search:read`/`chat:write`; an unanswered @mention surfaces, an answered one
  doesn't; a Gmail thread awaiting reply surfaces; nudge fires via the existing proactivity path; dismiss stops
  re-nag. (Jon does the live Slack re-auth at Lead's verify.)
- **G:** a proposed calendar event DMs Jon a preview; **"no" → nothing happens**; "yes" → event really created +
  link returned; **double-"yes" executes once**; expired proposal never executes; **no execution path exists
  without `status=approved`** (assert in tests). Jira create works the same way through the gate.

## After both land (Lead)
Merge R + G; apply both migrations + restart; verify live (Jon re-auths Slack). Then wire **2b Slack-send** onto
the gate using R's user token, and schedule **2d Gmail-send** behind a `gmail.send` re-consent.
