# Brief — Content-drafting node hangs (auto-draft produces nothing) — P0

**Type:** P0 debugging + fix — the marketing auto-draft pipeline stalls at the content node, so campaigns
never produce a draft. **For terminal to take or delegate** (capable Sonnet or terminal-opus — it's
investigative). Own worktree, cwd inside, branch `worker/content-draft-node-hang` off `main`. Own test DB.
Do NOT merge — report.

## Symptom (confirmed live by Lead, 2026-06-06)
Drove the real auto-draft chain on campaign #15 on the dev app:
1. `POST /api/campaign-ops/candidates/15/brief/assemble` → ✅ brief id=4 assembled (fast).
2. `POST /api/marketing/campaigns/15/initiate` → ✅ initiated, deliverables pipeline run dispatched
   (run `cd38eca5-7e30-...`, target_candidate_id=15).
3. ❌ The run **hangs on node `content_asset_selector`**: status "running" for **6+ minutes**, `error: null`,
   `cost_usd: 0.0`, `output_summary: ""`, `ended_at: null`. No draft, no deliverable ever produced.
   A `claude --output-format …` CLI subprocess was alive the whole time — **the CLI call never returns and
   no timeout fires.**

`content_asset_selector` is an **agent node** run via `artemis/pipelines/node_executors/agent_executor.py`
(it references content_asset_selector ~line 355) → the claude-code adapter → the `claude` CLI.

## Likely root causes (investigate — don't assume)
This matches prior claude-code CLI issues in MEMORY.md:
- **No effective timeout on this path** → the CLI call hangs indefinitely instead of failing fast
  (`feedback`/`project-claude-code-concurrency-timeout`: CLI runs >300s → ClaudeCodeTimeoutError 408; but
  here nothing timed out at 6min).
- **claude-code adapter MCP/tool handshake** (`project-marketing-pipeline-tool-use-blocker`: adapter drops
  tools; `MCP_CONNECTION_NONBLOCKING`) — the content agent may be waiting on a tool/MCP handshake that never
  completes, or looping.
- Prompt size / a tool-use loop / the per-run MCP subprocess not connecting.

## Goal
Make a campaign's auto-draft actually produce a content draft (the outreach_email for #15), OR fail fast
with a clear error instead of hanging. Specifically:
1. **Root-cause the hang** — reproduce (assemble brief → initiate a campaign → watch the
   `content_asset_selector` node + the claude CLI subprocess). Determine why the CLI call never returns
   (timeout config, MCP/tool handshake, prompt, loop).
2. **Enforce an effective timeout** on the content node's adapter call so a stuck CLI run fails the node
   (clear error) rather than hanging forever — wire it the way other resilient nodes do; coordinate with
   the existing `continue_on_failure` / executor timeout patterns.
3. **Fix the actual drafting** so the node completes and emits a content draft (lands in Writing Studio +
   as a `content_draft` deliverable at Gate-2). If the cause is the tool/MCP handshake, apply the same fix
   pattern already used for scouts (`MCP_CONNECTION_NONBLOCKING=false`, `--no-session-persistence`).

## Verify (live — assert the EFFECT)
- A real campaign (e.g. a fresh test candidate, or #15) goes assemble-brief → initiate → **content node
  COMPLETES** → a content draft exists (WS `/overview` shows it; a `content_draft` Gate-2 approval is
  created). Report the actual drafted content.
- The timeout path: simulate/observe a hang → the node fails with a clear error in a bounded time, run
  doesn't hang forever.
- Existing pipeline/marketing tests pass. ruff + mypy clean.

## Notes / cleanup
- The dev-DB run `cd38eca5-…` is currently hung "running" + a `claude --output-format` subprocess (PID was
  14486) is stuck — safe to kill the subprocess + mark that run failed when reproducing in the dev env.
- Lossless; status transitions only; no destructive migration. Org dep rule. Isolated worktree + own test
  DB (serialize claude-code calls — don't run 3 concurrent, per the concurrency-timeout memory). Do NOT
  merge — report branch + SHA + the root cause + the live proof a draft now gets produced.
- Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
