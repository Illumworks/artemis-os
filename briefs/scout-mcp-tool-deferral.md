# Brief — Scouts can't invoke MCP tools (claude-code deferral) → 0 signals

**Type:** P0 top-of-funnel (no scouts → no signals → no campaigns). **Model:** Codex or terminal Sonnet
(needs live experimentation — single scout runs). **Own worktree**, branch `worker/scout-mcp-deferral`,
cwd INSIDE the worktree, branch off `main`. May run single scouts against artemis_os read-mostly (scouts
only INSERT signals — acceptable; do not delete).

## Diagnosis (done — confirmed live 2026-06-05)

Marketing scout `agent_invocation` nodes (run via the claude-code CLI adapter) intermittently emit 0
signals. The scout LLMs narrate (seen in pipeline run 7679fe7e node output_summaries):
"the artemis MCP server tools are **deferred and not yet fully connected for invocation**",
"the artemis MCP server tools (federal_register_search, grants_gov_search, signal_queue_write...) [not
connected]". Confirmed reproduces in isolation: `uv run python -m artemis.marketing.scout_cli
marketing.scout.legislative` → status completed, **emitted 0**.

Key facts established:
- The phrase is NOT in our codebase — it's the scout LLM describing **claude-code's deferred-tool
  mechanism** (tools presented in a deferred catalog that must be "fetched"/loaded before invocation).
- The claude-code adapter spawns a per-run MCP subprocess: `claude -p --mcp-config <tmp>
  --strict-mcp-config --allowed-tools mcp__artemis__<tool>...` running `python -m artemis.tools.mcp_server`
  (`artemis/providers/claude_code/adapter.py:267-399`, `allowed_tools_for` ~448). So this is NOT app-restart
  timing (each run has its own MCP server).
- Intermittent: a scheduler run hours earlier produced 9 signals (tools worked then). So sometimes the
  tools are directly available, sometimes deferred.

## Investigate + fix (likely candidates — find which, with runtime evidence)

1. **Is claude-code deferring the MCP tools?** Determine what triggers deferral (tool-count threshold?
   context size? CLI version/setting?). Check the `claude` CLI flags/version the adapter invokes — is there
   a way to force tools EAGER / disable deferral (a flag, a setting, or keeping the tool count below the
   threshold)? Capture the actual claude-code session output for a failing scout (the adapter likely has a
   debug/verbose path or writes the subprocess transcript) to SEE the deferred-tool catalog.
2. **Prompt fix (most likely robust):** the scout agent's instructions probably don't tell it that some
   tools may be deferred and must be loaded/fetched first. Teach the scout prompt (the scout agent
   definitions / their instruction templates) to fetch its MCP tools before concluding they're unavailable
   — OR confirm the adapter can present them non-deferred so no prompt change is needed.
3. **Tool-count reduction:** if deferral is triggered by too many tools in the catalog, scope each scout's
   `agent.tools` to only what it needs (e.g. legislative needs legiscan/legislative search + signal_queue
   .write, not all tools), keeping the catalog small enough to stay eager.

Also note the secondary, lower-priority scout gaps (do NOT let them block this fix; just log/triage):
- `scout_procurement`: "SAM.gov API key not configured" (missing data-source key).
- `scout_board_minutes`: "403 CloudFront blocks" (scraper being blocked).
- `scout_starbridge`: prompt-grounding confusion (got distracted by seeing its own system prompt).

## Verify (the EFFECT — runtime, not just code)

- Run a single scout live (`scout_cli marketing.scout.legislative` and one or two others) → it INVOKES its
  MCP tools (the MCP subprocess logs "bound ... tools=[...]" and the agent actually calls them) and
  **emits ≥1 signal** (or legitimately finds nothing, but WITHOUT the "tools deferred/not connected"
  narration). Re-run a couple times to confirm it's reliable, not intermittent.
- A full `marketing.main` run is the final proof but expensive — confirm with single scouts first; only
  do one full run once single scouts reliably invoke tools.

## Constraints
- Lossless; scouts only insert signals. No schema/migration likely (prompt/adapter/agent-tools-config
  changes). Org dep rule: nothing <7 days old. ruff + mypy + tests clean. Report whether the fix was a CLI
  flag, a prompt change, or tool-scoping — and the runtime evidence (a scout now invoking tools + emitting).
  Do NOT merge. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
