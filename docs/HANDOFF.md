# Artemis OS — Session Handoff

**Last updated:** 2026-05-23 (end of long multi-day session)
**Lead branch tip:** `lead/j6a-granola-integration` — see `git log --oneline -10` for current state.

## TL;DR for the next session

**Substrate is complete and demoable.** Click Run on Marketing Pipeline → executor walks 21 nodes → suspends at Gate 1 (or completes cleanly if 0 signals via Lane C fast-path) → Approval Queue cards render PIPE4 context → Run History surface shows past runs → live-view overlay shows fresh runs with correct timing.

The **only gap to full live demo** is real scout adapters. Without them, scouts return empty, pipeline completes in <1s, no real signals flow. Architecture works; data sources are next.

**Next concrete task:** Implement 1 real scout adapter (`regional_news` — RSS-based, simplest, no external API key needed beyond LLM). See "Next-Session Priorities" below for the brief outline.

## State at session close

### Live on lead branch (this session's headline landings)

- **PIPE1-PIPE5** — full pipeline orchestration substrate: data model, visual canvas, pan + smooth drag + edge tracking, per-type config forms (agent/trigger/gate/conditional/sub_pipeline), marketing pipeline seed (21 nodes), execution engine with provider cascade
- **PIPE4 execution engine** — Run button actually executes. Trigger fires, agents invoke via CLI cascade (claude-code default, anthropic fallback), human gates suspend + create approval rows, conditional evaluates predicates, sub_pipeline recurses
- **Provider cascade resolver** — extracted into `artemis/providers/resolver.py`; agent execution path routes through `resolve_adapter(provider, fallback_provider)` instead of bare AnthropicAdapter
- **Connectors architecture** — `connectors` + `agent_connectors` tables, encrypted credentials at rest, UI in account-popup Connections panel for Starbridge/OpenAI/Anthropic/Gemini/Tavily kinds, runtime resolver via `connectors/resolver.py`
- **AI Assistant Panel** — inline in pipeline canvas (not separate Builder page), SSE-streamed proposals via O1 infrastructure, accept/reject affordances
- **Agent Card Operating Blueprint** — surfaces cadence/inputs/urgency tiers/failure modes/db tables touched/implementation notes from markdown
- **Reason codes injection** — `agents.reason_codes_emitted` JSONB column, runtime injects allowlist into LLM system messages, registry-backed multi-select UI editor
- **Pipeline JSON export/import** — n8n-style with agents bundled, credentials scrubbed
- **Browser history nav** — back/forward + deep-links work via setState→pushState wiring
- **Custom agent folders** — `agents.metadata.display_folder` JSONB; toggle Slug ↔ Custom view
- **Signals Inbox tree refresh** — 5 grouping modes + "By Pipeline Run" + contextualized empty states
- **Approval Queue PIPE4 context** — populated cards with brief preview/reason codes/districts (migration 0045)
- **Pipeline Run Live-View** — canvas node state visualizations + bottom-right overlay + Run History surface at `#/pipeline-run-history`
- **Empty-signals handling** — pipeline halts cleanly with "No signals this run" + downstream marked `skipped` (no phantom approvals)
- **15+ cleanups** — ruff baseline + format + mypy + node --check in check.sh, stale tests, start-app.sh path portability, dead endpoint stubs, j1b state-leakage, retro-terminal.css drop, cron picker presets, kebab dropdown, etc.

### Documentation living docs

- `docs/ARTEMIS-OS-MASTER-PLAN.md` — canonical reference, updated this session with D6 lock + landings
- `docs/MARKETING-PIPELINE-CANONICAL.md` — 21-node pipeline mental model for safe future edits
- `docs/SITE-MAP.md` — app information architecture; closes the "where do things live" gap
- `briefs/*` — 50+ briefs documenting every landed and queued task

### Known exempt failures (in check.sh)

- `test_j5b_jira_team_members::test_get_team_members_no_project_key_returns_empty_all` — real-network Jira flake, needs mocking pass (bank for test-infra brief)

## What's NOT live (banked / next-session priorities)

In priority order:

### 1. Real scout adapters — THE BIG ONE for demo viability

The pipeline executes correctly but scouts are stubs returning empty lists. Without at least one real scout, all pipeline runs produce zero signals (architecturally correct fast-path completes in <1s, no real data flows).

**Recommended first adapter:** `regional_news` — RSS-based, no external API key needed beyond an LLM for parsing. Should be ~300-500 LOC.

**Then:** `starbridge_researcher` if you have an API key (env scaffold already exists).

**Brief draft for next session:** `briefs/scout-adapter-regional-news.md` (not yet written; this is the next-session task).

### 2. PIPE6 — Workflows + Automations sunset

Per D6 lock: legacy Workflows and Automations tabs should be deleted and their data migrated into Pipelines. Brief not yet drafted; lives next-session.

### 3. Real Slack OAuth + DM delivery

Connectors architecture supports Slack, but the actual OAuth + workspace setup hasn't been done. PIPE4's human_gate executor falls back to in-app Approval Queue when Slack DM delivery fails. Once Jon configures Slack workspace + bot in Connections panel, gate approvals can land in Slack DMs directly.

### 4. Comprehensive UI polish pass

Many cosmetic items banked across this session:
- Tooltip cutoff at browser edge
- on_timeout visual noise in human_gate form
- Lean appearance + naming consistency (Pipelines → Workflows rename pending after PIPE6 sunset)
- Floating chat icon for AI Assistant when closed
- Ghost-node visual on canvas vs. proposal cards in sidebar
- Toast styling, error message clarity
- Cron picker edge cases
- Approval card layout polish

Bank as `briefs/ui-polish-pass-v1.md` for next session.

### 5. Test infrastructure pass

- Mock Jira client in j5b test (closes the j5b exempt failure)
- Mock Slack send in j8/j9 tests
- happy-dom render smoke for any form-emitting JS files
- Convention: every new .js component needs at least one render-mount smoke test (prevents the 3 browser-only bugs we hit this session)

### 6. Smaller backlog items

- Agents tab loading race condition (~30-50 LOC Codex)
- M3 transition() wire-up follow-up (~150 LOC Sonnet)
- JSONB MutableDict audit across other columns
- AI button bubble-catch hardening
- Trajectory summary `KeyError: '\n  "what_worked"'` JSON parsing bug
- 5 sibling worktrees per-branch decision (`artemis-os-d4`, `artemis-os-lead`, etc.)

## Operational invariants codified this session

Terminal-Lead has saved these as feedback memories:

1. **Post-merge runtime sync:** if merge touches Python source, `pkill -9 uvicorn` + restart. `--reload` is unreliable for new modules and SQLAlchemy model changes.
2. **Post-merge alembic:** if migration added, `uv run alembic upgrade head` before browser smoke.
3. **Worker "done" means committed:** "files in working tree" ≠ "shipped." Verify with `git log <branch>`.
4. **`git diff --staged` reflex** for migration renames: file rename + revision string edits must be in ONE staged change.
5. **Browser smoke MUST happen for UI changes:** unit tests don't catch render-time bugs. Workers in isolation worktrees need to actually run the dev server.
6. **Provider cascade is CLI-first:** marketing agents default to `claude-code` (no API key); anthropic API is fallback only.
7. **Re-verify "pre-existing failure" claims on parent branch in main repo,** not just on worker worktree.

## Next-session prompt (ready to paste)

```
Read docs/ARTEMIS-OS-MASTER-PLAN.md first. Then docs/HANDOFF.md for current state. Then git log --oneline -15 lead/j6a-granola-integration for recent commits.

Substrate is complete: PIPE1-5 + connectors + AI panel + reason codes + blueprints + cron + Figma reconciliation + export/import + browser history + agents tree + custom folders + signals inbox tree + approval cards + live-view + empty-signals + all the cleanups.

Only gap to full demo: real scout adapters. Scouts currently return empty lists; pipeline completes in <1s with "No signals this run" fast-path.

PRIORITY 1: Draft + ship `regional_news` scout adapter. RSS-based (e.g. Google News RSS for state-level education news), no external API key needed beyond claude-code CLI for parsing. Acceptance: at least 3 real signals flow into signal_queue per pipeline run, qualifier picks them up, brief_composer creates real brief, Gate 1 shows real content in Approval Queue.

PRIORITY 2: After regional_news works end-to-end, draft + ship PIPE6 (Workflows + Automations sunset + auto-migrate to Pipelines).

PRIORITY 3: Real Slack OAuth setup in Connections panel + verify Gate 1 DM delivery works.

Operational invariants from previous session (codified in terminal-Lead's feedback memories):
1. After merge touching Python source: pkill -9 uvicorn + restart (don't trust --reload)
2. After migration: uv run alembic upgrade head
3. Browser smoke MUST happen for UI changes (Workers shipped 3 browser-only bugs this session by skipping it)
4. Worker "done" = git log shows commit hash, not files-in-tree
5. Provider cascade is CLI-first; agents default to claude-code

LOC discipline reality check: Workers consistently overran briefs 1.4-3.4x this session. Brief estimates should reflect tests + CSS + structural enumeration honestly. Stop-at-cap rule is calibration-based, not contract-based.

Recommended model: Opus 4.7 (Lead). Workers via terminal-Lead Agent({isolation:"worktree"}); Codex pastes for mechanical work; never spawn workers in main worktree.
```

## What I recommend the next session do FIRST

1. Read this HANDOFF.md
2. Read `docs/ARTEMIS-OS-MASTER-PLAN.md` for context
3. `git log --oneline -15 lead/j6a-granola-integration` to see recent landings
4. Run `./scripts/check.sh` to verify substrate state (should be 2500+ passed, 1 exempt failure)
5. Hard-refresh browser, walk Pipelines + Agents + Connections + Approval Queue to confirm UI works
6. Then draft `briefs/scout-adapter-regional-news.md` and fire it

That gets you from "substrate works invisibly" to "substrate produces real signals" — the only gap remaining.
