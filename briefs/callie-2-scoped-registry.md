# CALLIE-2 — Give Callie a scoped registry instead of everything-minus-unplugged

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this is a security boundary.
Over-trim and you break what Josh is actively using; under-trim and the finding stands.

The security analysis is done (see `docs/marketing-intelligence-direction.md`, "What
Callie can see"). This brief is the execution. **Do not re-litigate the keep/drop
lists** — if you think one is wrong, say so in your report rather than deviating.

## The finding

Callie falls through to the **general** registration path in
`artemis/floating_artemis/tool_registry.py::build_authorized_tool_registry`, so she
holds everything the app offers minus whatever is not plugged in. Kai and Ares each get
an explicit registry behind an early return. Callie should too.

Verified in her **production** path (claude-code MCP, layer ≤ 2 only) on 2026-08-14:

- She can **`spawn_subagent`**, **`run_agent`**, **`run_workflow`** — invoke other agents.
- She can **`propose_fix`**, **`propose_edit`**, **`propose_agent`** — propose code and
  agent changes.
- She can read internal operating state: **`recent_failures`** (agent names, error
  classes, run ids), **`list_scopes`** (the whole agent roster), `list_agents`,
  `list_dags`, `list_routes`, `health_check`, `surface_status`, `read_file`.
- `gcal` and `gmail` are registered **unconditionally** for every agent on that path.
  Gmail and Granola currently answer "no credential" / "not connected" — so she is
  protected by absence, not by design. Connect either integration for any unrelated
  reason and she silently gains Jon's inbox and meeting transcripts.

Jon: *"I want to make sure she can't expose internal operating information."*

## What to build

A `_build_callie_tool_registry(...)` behind an early return, in the shape of
`_build_ares_tool_registry` — which is the model to copy, because it hand-picks
individual tools from `core` rather than calling `register_core_tools` wholesale.

**KEEP** — everything she demonstrably needs for marketing work:

| area | why |
|---|---|
| `register_marketing_tools` | signals, candidates, the new `get_district_contacts`. Her job. |
| `register_argus_tools` | `dispatch_research`. Josh asks for this constantly. |
| `register_callie_dm_tool` | her guarded DM, with its own allowlist (CALLIE-1). |
| `register_slack_tools(registry, include_dm=False)` | read/post channels. **`include_dm=False` is load-bearing** — see the existing comment on that call site. |
| `register_writing_rules_tools` | she drafts copy and needs the house rules. |
| `register_screentime_report_tools` | she reports the screentime section. |
| `register_directory_tools(participants=participants)` | name → person, and the ambiguity fix from this week. |
| `QUERY_MEMORY` + `WRITE_MEMORY` from `core` | her own continuity. `query_memory` MUST stay scope-gated via `_make_query_memory(agent_id)` — that is the M3 control and dropping it would widen her memory reach, not narrow it. |

**DROP** — everything else, specifically:

- `register_builders_tools` entirely — `run_agent`, `run_workflow`, `spawn_subagent`,
  `list_agents/workflows/skills/chains/dags`, `propose_agent/workflow/skill`.
- `register_system_tools` entirely — `health_check`, `recent_failures`, `propose_fix`.
- `register_gcal_tools`, `register_gmail_tools`, `register_granola_tools` — personal
  data, and the latent exposure above.
- `register_okr_tools` — CLAUDE.md flags OKR Studio rows as an owner-judgment surface.
- `register_jira_tools`.
- From `core`: `READ_FILE`, `PROPOSE_EDIT`, `LIST_SCOPES`, `SURFACE_STATUS`,
  `LIST_ROUTES`, `SET_PREF`, `SET_BRIEF_EXCLUSION`, `CLEAR_BRIEF_EXCLUSION`,
  `SPAWN_SUBAGENT`, and the Forge/git tools (already absent for her — keep them absent).

## Hard constraints

- **Early return, no fallthrough**, exactly like Kai's and Ares's. A future
  `register_*` added to the general path must not silently reach Callie. Say in the
  docstring that this is the point.
- Artemis, Kai, Ares and every other agent's registry must be **unchanged**. Prove it.
- Do not touch `artemis/argus/*`, `artemis/crisis_content/*`, `artemis/market_signals/*`,
  `artemis/memory/*`, or `artemis/directory/*`.
- No new dependencies. No migration.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_worker_b uv run pytest artemis/floating_artemis tests/test_directory.py tests/unit_no_db -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Both env vars required; never run against `artemis_os`. Expect one pre-existing mypy
error about `Dimension` not being exported and 22 pre-existing ruff errors in unrelated
files — confirm yours are not among them.

## Tests (all required)

- [ ] Callie's production registry (`_build_auto_invoke_tool_registry` over her
      authorized registry) contains **none** of: `run_agent`, `run_workflow`,
      `spawn_subagent`, `recent_failures`, `list_scopes`, `health_check`, `read_file`,
      `propose_edit`, `propose_fix`, `list_agents`, `list_dags`, `list_routes`, and no
      tool whose name starts with `gmail`/`calendar`/`event`/`okr`/`jira`.
- [ ] She still has: `dispatch_research`, `send_guarded_dm`, `query_memory`,
      `write_memory`, `get_district_contacts`, `resolve_person`, and Slack read/post.
- [ ] She does **not** have the raw `send_slack_dm` (the CALLIE-1 guarantee).
- [ ] `query_memory` is still the scope-gated variant for `agent_id='callie'`.
- [ ] **Artemis's registry is byte-for-byte the same set as before this change** —
      assert against an explicit expected list, not a snapshot of the new behaviour.
- [ ] Kai's and Ares's registries are unchanged.
- [ ] A tool newly registered on the general path does not appear for Callie (prove the
      early return holds — e.g. register a sentinel in a test and assert its absence).

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste Callie's **full** production tool list before and after, so the diff is
      reviewable at a glance.
- [ ] Confirm from `#demand-gen-callie`'s actual usage that nothing dropped was in use:
      Josh asks for signals, district research, contacts and email drafts. If you
      believe something on the DROP list is genuinely needed for that, say so rather
      than keeping it silently.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than building to it silently.
