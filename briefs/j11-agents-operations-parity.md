# J11 — Agents Operations parity: subresources + enriched detail + run aliases

**Owner:** Worker (Sonnet) for the subresource/alias slice; Lead 30-min consult on the agent-package policy fields before Worker codes them.
**Scope:** ~500 LOC + tests. Half-day to a day.
**Depends on:** J10 (trailing-slash compatibility) must have landed. Otherwise the basic list endpoints these routes hang off still 404.
**Unblocks:** The Operations Agents page rendering with real enriched detail; subsequent Skills/Automations work that needs the Agents surface to feel complete.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

The Codex Operations audit (`audits/operations-section-gap-report.md`, section 3) traced the "Agents stuck loading" report to its root: `GET /api/agents` was 404'ing because of the trailing-slash issue (J10 fixes that). But once the list loads, the Operations Agents panel still calls a series of Node-era subresources that Python doesn't yet implement:

- `GET /api/agents/:id/instruction` / `PUT` / `DELETE` — instruction file read/save/delete
- `GET /api/agents/:id/files` — supporting files list
- `GET /api/agents/:id/skills` — skills assigned to this agent
- `GET /api/agents/context/:runId` — run context lookup (alias for `/api/agent-runs/:runId/context`)
- `GET /api/agents/runs/active` — currently-running agents
- `GET /api/agents/runs/recent` — recent run history across agents
- `GET /api/agents/runs/search` — full-text search across runs
- `GET /api/agents/runs/:runId` — single run by ID (alias for `/api/agent-runs/:runId`)

Plus the agent detail object Python returns is missing the enrichment fields the frontend expects: `instructionFileExists`, `linkedSkills`, `supportingFileCount`. Without those, the Operations panel renders agents as bare stubs.

Plus the agent package policy fields the Node app validated and persisted: `fallbackProvider`, `fallbackModel`, `memoryPolicy`, `permissionMode`, `outputContract`. These need a small migration to add to the `agents` table — but the schema decisions deserve a Lead check before locking them in.

This brief delivers all three pieces in one Worker pass.

## Scope — three slices

### Slice A — Subresource routes (no schema changes)

The instruction file and supporting files live on disk under a per-agent directory. Mirror Node's pattern: `~/.artemis/agents/<agent_id>/instruction.md` and `~/.artemis/agents/<agent_id>/files/*`. Linked skills come from a join table that may not exist yet — see Slice C.

- [ ] `GET /api/agents/{agent_id}/instruction` → `{exists: bool, content: str}` (empty content + `exists: false` if file missing)
- [ ] `PUT /api/agents/{agent_id}/instruction` → body `{content: str}`. Writes the file. Returns `{ok: true}`.
- [ ] `DELETE /api/agents/{agent_id}/instruction` → 204. Removes the file if present, no-op if not.
- [ ] `GET /api/agents/{agent_id}/files` → `{files: [{name, size, modified_at}]}` listing the agent's `files/` directory. Empty array if directory doesn't exist.
- [ ] `GET /api/agents/{agent_id}/skills` → `{skills: [SkillRead]}` listing skills assigned to this agent (from join table — see Slice C).

All routes return 404 with `{error: "agent_not_found"}` if the agent_id doesn't exist in the `agents` table.

### Slice B — Run-observability aliases (no schema changes)

Pure compat layer over the existing `agent_runs` table. These all read; no writes.

- [ ] `GET /api/agents/runs/active` → `{runs: [AgentRunRead]}` where `status` is `running` or `pending`. Order by `started_at` DESC.
- [ ] `GET /api/agents/runs/recent?limit=50` → `{runs: [AgentRunRead]}` last N runs across all agents, regardless of status. Order by `started_at` DESC.
- [ ] `GET /api/agents/runs/search?q=<query>` → `{runs: [AgentRunRead]}` substring match against `prompt` and `output`. Order by `started_at` DESC. Cap at 100 results.
- [ ] `GET /api/agents/runs/{run_id}` — alias for `GET /api/agent-runs/{run_id}`. Same response shape. (Just register the route at a second prefix; don't duplicate the handler logic.)
- [ ] `GET /api/agents/context/{run_id}` — alias for `GET /api/agent-runs/{run_id}/context`. Same response shape.

### Slice C — Schema: agent_skills join + package policy fields

**LEAD CONSULT REQUIRED BEFORE THIS SLICE.** The fields below are guesses based on Codex's audit; Lead should confirm types + defaults before the migration lands. Spawn a 5-min ping to Lead with the schema below; proceed once confirmed.

- [ ] New table `agent_skills` (alembic migration, next sequential revision number — verify via `ls alembic/versions/` and review `git diff --staged` before commit):
  ```sql
  CREATE TABLE agent_skills (
    agent_id     INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_slug   TEXT NOT NULL REFERENCES skills(slug) ON DELETE CASCADE,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, skill_slug)
  );
  ```
- [ ] Add columns to `agents` table (same migration):
  - `fallback_provider TEXT NULL` — e.g. "anthropic" / "openai" / "claude-code"
  - `fallback_model TEXT NULL` — e.g. "claude-haiku-4-7"
  - `memory_policy TEXT NULL DEFAULT 'session_scoped'` — values: `session_scoped` / `agent_scoped` / `user_scoped` / `none`
  - `permission_mode TEXT NULL DEFAULT 'ask'` — values: `ask` / `auto_approve` / `dry_run`
  - `output_contract JSONB NULL` — optional JSON schema the agent's final output is validated against
- [ ] Update `AgentRead` schema to expose new fields with camelCase aliases (`fallbackProvider`, `fallbackModel`, `memoryPolicy`, `permissionMode`, `outputContract`).
- [ ] Update enriched detail response on `GET /api/agents/{agent_id}` to include:
  - `instructionFileExists: bool` (check `~/.artemis/agents/<id>/instruction.md`)
  - `supportingFileCount: int` (count entries in `~/.artemis/agents/<id>/files/`)
  - `linkedSkills: [{slug, name}]` (join via `agent_skills`)
- [ ] **Run `git diff --staged` before the migration commit** — `git mv` and `Edit` only stage half the chain unless you re-add. This bit us twice already (`bc13611`, `720e2c8`).

### Slice D — Skill assignment routes (depends on Slice C)

- [ ] `POST /api/skills/{slug}/assign` → body `{agent_id: int}`. Creates an `agent_skills` row. Idempotent (PK constraint).
- [ ] `POST /api/skills/{slug}/unassign` → body `{agent_id: int}`. Deletes the row.

These two routes round out the Skills surface enough that the Operations Skills panel can show "assigned to agents" — even if Skills lifecycle (categories, approval, imports) is still missing. That broader Skills slice is J12.

## Acceptance — what done looks like

- [ ] All 12 new routes (5 subresource + 5 run-observability + 2 skill-assignment) return the correct status codes and shapes. Curl smoke pasted verbatim in report.
- [ ] Migration round-trip: `alembic downgrade -1 && alembic upgrade head` produces the same schema, idempotent.
- [ ] Open the Operations Agents page in the browser. The existing seed agent (`ws-rid-agent` per the Codex audit) should render with:
  - Instruction file section (initially empty since no file exists yet)
  - Supporting files section (initially 0)
  - Linked skills section (initially empty since no assignments yet)
- [ ] Create a test instruction file via `PUT /api/agents/1/instruction`, refresh the page, confirm it renders.
- [ ] Active/recent/search run endpoints all return JSON. With at least one run row in the DB, they return at least one result.
- [ ] `GET /api/agents/runs/{run_id}` and `GET /api/agent-runs/{run_id}` return the same payload for the same id.
- [ ] No regression on existing routes — run the J10 curl smoke loop and confirm still-200 across the board.

## Quality acceptance gates

- [ ] Lead 5-min consult on Slice C schema done; schema confirmed before migration commit
- [ ] Manual smoke output pasted **verbatim** in your report (curl outputs for each new endpoint + screenshot or textual confirmation of the Agents page rendering)
- [ ] `git diff --staged` checked before each commit that touches the alembic versions directory
- [ ] `ruff check` + `mypy` clean
- [ ] Tests: route test per endpoint group (happy + 1 failure mode each). Migration up/down round-trip test. ~150 LOC of tests is reasonable.
- [ ] No `TODO` or stub responses in shipped code

## Out of scope (separate briefs)

- Skills lifecycle: `approved`/`proposed`/`archived` status transitions, categories, `/templates`, `/import-zip`, `/import-url` — that's J12.
- Automations backend — depends on Agents+Workflows being walkable. J14.
- Memory HTTP routes — separate, Lead-led, J15.
- Frontend changes to surface the new `outputContract` / `memoryPolicy` / `permissionMode` fields in the Agent edit modal — separate UX brief.

## Where to start

1. Read this brief twice
2. Read `audits/operations-section-gap-report.md` section 3 (Agents) — Codex traced the exact endpoint shapes
3. Read `artemis/routes/builders/agents.py` and `artemis/routes/builders/agent_runs.py` to map the existing handler patterns
4. Ping Lead with the Slice C schema for a 5-min consult — do not skip this
5. Implement Slice A first (no schema), then B (no schema), then C (migration, depends on Lead confirm), then D (depends on C)
6. Run the curl smoke + browser test before reporting done
