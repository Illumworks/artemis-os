# Brief: Marketing content agents don't call their MCP tools (prompt-grounding fix)

**Owner:** Codex — **model `gpt-5.4`, reasoning effort HIGH** (subtle agent-grounding +
live verification; not a -mini task).
**Branch:** `worker/tooluse-content-prompt-grounding` (own git worktree).
**Local-only git. Commit trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## The problem (verified live, 2026-06-03)

A `campaign_deliverables` pipeline run (id `385506cc-ecb7-4904-9feb-17bbcc3c1f79`) reached
Gate-2 but produced **no deliverable**: `campaign_deliverables` got no new row, the approval
context had `deliverable_ids: []`, no draft preview. The content agent `output_summary`s read:

- `writing_studio_adapter`: *"I cannot execute the database query because Bash is not available
  in this context. Let me check what tools are available."*
- `asset_selector`: *"I now have enough context…"* (then produced nothing persisted).

So the human-gate / Slack approval machinery (just hardened) has nothing real to review.

## What is NOT the cause (already checked — do not redo)

- **Tools ARE wired.** The claude-code adapter forwards tools correctly (cc2, merged). The MCP
  server subprocess spawns per run (observed: `python -m artemis.tools.mcp_server --agent-id
  marketing.content.asset_selector --run-id … --pipeline-run-id 385506cc`).
- **The agents HAVE tools in the DB** (verified via live query on `agents.tools`):
  - `marketing.content.asset_selector` → `['content_registry.list_approved_assets']`
  - `marketing.content.writing_studio_adapter` → `['writing_studio.enqueue']`
  - `marketing.content.brief_assembler` → `['campaign_brief.read','campaign_brief.write']`
- The `writing_studio.enqueue` tool factory works (creates a deliverable via
  `create_draft_from_candidate`, transitions to `draft_ready`, returns `deliverable_id`) — see
  `artemis/tools/content_agent_tools.py`. (An earlier "agents seeded with empty tools" hypothesis
  was WRONG — the DB tools arrays are populated. Verify against the live DB, not git history.)

## The actual root cause (verified)

The content agents' **system prompts never instruct them to CALL their MCP tools.** They are
pure role descriptions with an obsolete mental model:

- `writing_studio_adapter` prompt (1589 chars) describes an **HTTP POST integration** — "the POST
  payload", "a 4xx from Writing Studio", "5xx", "response body" — and **never mentions
  `writing_studio.enqueue`, the word "tool", or how to call it.** So the agent reaches for
  Bash/curl to "POST", finds no Bash, and gives up.
- `asset_selector` (966 chars) and `brief_assembler` (1473 chars): same — **zero mention of their
  own tool or of calling a tool.**

Contrast: the **scout** agents (e.g. `marketing.scout.legislative`) DO produce real signals
because their prompts ground on tool use (`signal_queue.write`, etc.). That is the working
pattern to mirror.

## Scope of fix

1. Find the source of truth for these prompts. They were seeded from markdown design docs
   (`marketing-ops-v1/` 5.x — e.g. `5.3-writing-studio-adapter.md`, `5.2-asset-selector-agent.md`,
   the brief-assembler doc) via a seed script (`artemis/marketing/seeds/marketing_agents.py` or
   similar). Determine whether the markdown already instructs tool calls (then it's a stale-seed →
   re-seed) or whether the markdown itself carries the obsolete HTTP/POST model (then rewrite the
   markdown AND re-seed). Fix at the source of truth, not just the DB row.
2. Rewrite the three content-agent prompts so each **explicitly instructs the agent to call its
   MCP tool** with exact field mappings:
   - `writing_studio_adapter` → call `writing_studio.enqueue` with the draft payload mapped from
     `campaign_workspaces.campaign_brief` / `asset_bundle` (keep the "mechanical precision, no
     invention" principle, but operationalize it as a tool call, not an HTTP POST). Drop all
     HTTP-status / POST-body language.
   - `asset_selector` → call `content_registry.list_approved_assets` and use the result.
   - `brief_assembler` → use `campaign_brief.read` / `campaign_brief.write`.
   Mirror the scout/qualifier prompt style that already works with tools.
3. Re-seed the affected agents so the DB `system_prompt` + `tools` reflect the fix. Confirm the
   re-seed is idempotent and does NOT clobber unrelated agents or any Writing Studio rules.

## Verification (REQUIRED — assert the EFFECT, not just "ran")

Live end-to-end: initiate a campaign_deliverables run for a candidate with a proposal (e.g. via
`POST /api/marketing/campaigns/{id}/initiate`, deliverable `outreach_email`), let it reach Gate-2,
then assert:
- a NEW `campaign_deliverables` row exists for that run/candidate, and a Writing Studio draft was
  created;
- the Gate-2 approval `pipe4_context.context.deliverable_ids` is **non-empty** and
  `draft_summary`/draft body is populated;
- the agent `output_summary` shows it CALLED its tool (no "Bash not available" flailing).
Capture the run id + the deliverable id in the PR notes. Unit tests for the prompt/seed change as
appropriate, but the live e2e is the acceptance bar (unit-green has hidden two real bugs here
already).

## Guardrails

- Local-only git; never push. Own worktree; do not commit into the main repo working tree.
- Do NOT touch Writing Studio rules or OKR Studio rows. Prompt + seed changes only.
- No dependency add/upgrade.
- If you discover the fix needs more than prompt grounding (e.g. the `--allowed-tools` allowlist or
  the MCP tool advertisement is actually wrong), STOP and report findings rather than expanding
  scope silently.

## Key files

- `artemis/builders/executor.py` (~line 326–410: ToolRegistry build + `_is_claude_code_tool_run`
  decision; tool path vs text-only `run_turn`).
- `artemis/providers/claude_code/adapter.py` (`run_with_tools`, `_build_launch_command`,
  `allowed_tools_for`).
- `artemis/tools/mcp_server.py` (`build_tool_set`, `list_tools`, `call_tool`).
- `artemis/tools/content_agent_tools.py` (`writing_studio.enqueue`,
  `content_registry.list_approved_assets`).
- `artemis/marketing/seeds/marketing_agents.py` (seed/extraction).
- `marketing-ops-v1/5.x` design docs (prompt source of truth).
- Working reference: any `marketing.scout.*` agent prompt that already calls tools.
