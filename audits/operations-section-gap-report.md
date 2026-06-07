# Operations Section — Gap Report (Py vs Node)

Generated: 2026-05-18
Auditor: Codex

## TL;DR

The Operations slab is partially ported but not operationally whole: Python has builder tables and CRUD/execution routes for Agents, Skills, Workflows, Chains, DAGs, and Runs, but the copied frontend still calls several Node-era URLs and expects Node-era subresources. The biggest immediate blocker is a FastAPI trailing-slash mismatch: `GET /api/agents`, `GET /api/skills`, `GET /api/workflows`, `GET /api/agent-chains`, and `GET /api/agent-dags` return 404, while the Python list routes only answer with a trailing slash. The smallest unblock is to make the Python builder list routes accept no-slash URLs and then add compatibility endpoints for the missing Node subresources (`/instruction`, `/files`, `/skills`, run-history aliases); Automations and Memory need fuller backend slices.

## Per-surface audits

### 1. Automations

#### A. Frontend

The page shell is rendered by `loadAutomationsShell()` in `public/js/features/operations-shell.js`, imported and called from `public/js/features/home.js`. The actual render path is `loadAutomationsShell()` → `refreshAutomationsFromApi()` → `renderOperationsView("automations")` → `renderAutomationsPage()`.

The Automations surface calls these API helper functions from `public/js/core/api.js`:

- `GET /api/automations` via `listAutomationsApi()`
- `POST /api/automations` via `createAutomationApi(data)`
- `PUT /api/automations/:id` via `updateAutomationApi(id, data)`
- `DELETE /api/automations/:id` via `deleteAutomationApi(id)`
- `POST /api/automations/:id/run` via `runAutomationApi(id, opts)`
- `GET /api/automations/:id/runs?limit=&cursor=` via `listAutomationRunsApi(id)`
- `GET /api/approvals?status=pending&target_type=automation_run&limit=50` via `listApprovalsApi()`
- `POST /api/approvals/:id/decision` via `decideApprovalApi()`

It also reads frontend state populated by Agents and Workflows: `getAgents()` from `state.agents`, `getWorkflows()` from `state.workflows`, and uses those collections as automation target pickers. Expected automation rows are Node-shaped: `id`, `name`, `description`, `status`, `trigger_type`, `schedule_config`, `target_type`, `target_id`, `provider`, `model`, `approval_policy`, `output_type`, `output_config`, `metadata`, and `latest_run`.

No dedicated Web Component exists for Automations. It is all inside `operations-shell.js`.

Shape-level diff: both repos have `public/js/features/operations-shell.js`; Python has the same Automations UI code. Node also has `server/routes/automations.js`; Python has no `artemis/routes/automations.py` and no `automations` table.

#### B. Backend

Python route module: MISSING. There is no `artemis/routes/automations.py`, and `rg` found no Python `/api/automations` route.

Live trace:

```text
GET http://localhost:8000/api/automations
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Python does have `artemis/marketing/routes/approvals.py` and an `approvals` table, so the approval side exists, but the automation runtime registry and run records do not.

Database tables: no `automations` or `automation_runs` tables in Postgres. Verified table inventory includes `approvals`, builder tables, and memory tables only.

#### C. Stuck Loading

n/a for the known Agents spinner, but Automations will show its own load error because `refreshAutomationsFromApi()` catches the 404 and stores `_automationsError`.

#### D. Compared To Node

Node has `server/routes/automations.js` backed by SQLite tables `automations` and `automation_runs`. It lists automations with `latest_run`, validates target type (`agent` or `workflow`), supports active/paused/archived status, creates approval-gated runs, and fires headless agent/workflow dispatch in the background.

Behaviors to preserve:

- Automations target saved agents or workflows by ID.
- Manual run creates an `automation_run` record and returns quickly.
- `approval_policy=require_before_run` creates a pending approval and an `awaiting_approval` run.
- Delete archives rather than hard-deleting active history.
- Latest run is embedded in list/detail responses for UI summary state.

#### E. Gap Summary

Working: The frontend shell exists and can render loading/error/empty states. The generic approvals backend exists.

Broken: All `/api/automations*` calls 404 in Python.

Missing: Automation tables, repository, routes, run records, headless dispatch, scheduler semantics, approval-resume side effects.

#### F. Suggested Divvy

Owner: Worker, with Lead review on approval/resume semantics.

Scope estimate: ~500 LOC backend + tests, plus ~50 LOC frontend compatibility if route shapes differ. This is a mechanical Node port with a clear contract, but approval side effects touch shared behavior.

### 2. Skills

#### A. Frontend

The Skills page shell is rendered by `loadSkillsShell()` in `public/js/features/operations-shell.js`, called from `home.js`. It calls `refreshSkillsFromApi()`, which loads approved skills, proposed skills, and categories before rendering `renderOperationsView("skills")`.

The Skills surface calls:

- `GET /api/skills?kind=<status-or-category>` via `fetchSkills({ status, category, kind })`
- `GET /api/skills/:id` via `fetchSkill(id)`
- `POST /api/skills` via `createSkillApi(data)`
- `PATCH /api/skills/:id` via `updateSkillApi(id, data)`
- `DELETE /api/skills/:id` via `deleteSkillApi(id)`
- `POST /api/skills/:id/approve` via `approveSkillApi(id)`
- `POST /api/skills/:id/archive` via `archiveSkillApi(id)`
- `POST /api/skills/:id/assign` via `assignSkillApi(id, agentId)`
- `POST /api/skills/:id/unassign` via `unassignSkillApi(id, agentId)`
- `GET /api/skills/slug/:slug` in Node, but Python helper now maps slug lookup to `GET /api/skills/:slug`
- `GET /api/skills/categories` via `fetchSkillCategories()`
- `GET /api/skills/templates` via `fetchSkillTemplates()`
- `POST /api/skills/import-zip` via `importSkillFromZip(file)`
- `POST /api/skills/import-url` via `importSkillFromUrl(url)`

State/data expected: Skills are grouped by `status` (`approved`, `proposed`, `archived`) and `category`; rows may include `slug`, `name`, `description`, `body`/`instructions`, `scope`, `provider_compat`, `when_to_use`, `origin`, `uses`, `agents`, and assignment metadata. Python’s API adapter currently maps Python `slug/name/instructions/kind/sourcePath` into a minimal `id/name/instructions` shape.

Web Components: none specific. The edit/import UX is in `operations-shell.js` and `public/js/features/skill-edit-modal.js`.

Shape-level diff: both repos have the same frontend files. Node has `server/routes/skills.js` and `server/skills-store.js` with disk-backed skill bodies plus SQLite metadata. Python has `artemis/routes/builders/skills.py`, but it only implements bare CRUD.

#### B. Backend

Python route module: `artemis/routes/builders/skills.py`.

Endpoints:

- `GET /api/skills/` → `{ "skills": [SkillRead...] }`
- `POST /api/skills/` → `SkillRead`, 201
- `GET /api/skills/{slug}` → `SkillRead`
- `PATCH /api/skills/{slug}` → `SkillRead`
- `DELETE /api/skills/{slug}` → 204

Return shape: `SkillRead` has `id`, `slug`, `name`, `description`, `instructions`, `tools`, `kind`, `sourcePath`, `ownerUserId`, `createdAt`, `updatedAt`.

Mismatch: the frontend calls `/api/skills` without a trailing slash, and the live app returns 404. With a trailing slash, the endpoint works:

```text
GET http://localhost:8000/api/skills
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

GET http://localhost:8000/api/skills/
HTTP/1.1 200 OK
{"skills":[]}
```

Even after the slash is fixed, Python does not implement Node’s status/category model. The adapter maps `status` and `category` into Python `kind`, so `approved` and `proposed` become kinds rather than statuses. Node subroutes `/categories`, `/templates`, `/import-url`, `/import-zip`, `/approve`, `/archive`, `/assign`, and `/unassign` are missing.

Database tables: `skills` exists and is empty in the live DB (`skills|0`). There is no `agent_skills` join table in Python.

#### C. Stuck Loading

n/a, but Skills can fail its initial load because `GET /api/skills?kind=approved` has the same no-slash 404 as Agents.

#### D. Compared To Node

Node stores skill metadata in SQLite and writes skill bodies/helpers to disk under skill directories. It supports categories, status transitions, URL/ZIP import, front-matter parsing, `GET /api/skills/slug/:slug`, usage counting, and agent-skill assignment through an `agent_skills` table.

Behaviors to preserve:

- Skills have lifecycle status, not just kind.
- Skills have categories and provider compatibility.
- Skill body lives as editable durable text, not just metadata.
- Proposed skills can be approved or archived.
- Skills can be assigned/unassigned to agents and listed from an agent detail view.
- Import from URL/ZIP must parse `SKILL.md` front matter and helper markdown files.

#### E. Gap Summary

Working: Python has a `skills` table and basic CRUD schema/routes.

Broken: The frontend’s no-slash list URL 404s. Status/category filters are semantically wrong when mapped to `kind`.

Missing: Categories, templates, imports, lifecycle routes, agent assignment, disk body/helper file behavior, usage counting.

#### F. Suggested Divvy

Owner: Worker.

Scope estimate: ~500 LOC if keeping the Python DB-only shape; ~800-1000 LOC if preserving disk-backed skill packages and import behavior. This is mostly a mechanical port, but the storage decision (DB-only vs package files) deserves Lead confirmation.

### 3. Agents ← KNOWN BROKEN

#### A. Frontend

The sidebar/page loader is `loadAgentsShell()` in `public/js/features/home.js`, which simply calls `loadAgents()` from `public/js/features/agents.js`. The richer Operations page also has an Agents tab inside `operations-shell.js`, with `refreshAgentsFromApi()` and `loadEnrichedAgent()`.

The core Agents load path calls:

- `GET /api/agents` via `fetchAgents()`
- `GET /api/agent-chains` via `fetchChains()`
- `GET /api/agent-dags` via `fetchDags()`
- `GET /api/stats/agent-metrics` via `fetchAgentMetrics()`

Agent CRUD/execution calls:

- `POST /api/agents`
- `PATCH /api/agents/:id`
- `DELETE /api/agents/:id`
- `POST /api/agents/:id/run`
- `GET /api/agents/:id/runs`
- `GET /api/agent-runs/`
- `GET /api/agent-runs/:runId`
- `GET /api/agent-runs/:runId/context`

Node-compat helper calls still used by the Operations Agents panel:

- `GET /api/agents/:id/instruction`
- `PUT /api/agents/:id/instruction`
- `DELETE /api/agents/:id/instruction`
- `GET /api/agents/:id/files`
- `GET /api/agents/:id/skills`
- `GET /api/agents/context/:runId`
- `GET /api/agents/runs/active`
- `GET /api/agents/runs/recent`
- `GET /api/agents/runs/search`
- `GET /api/agents/runs/:runId`

State/data expected: `state.agents`, `state.agentChains`, `state.agentDags`, `state.agentMetrics`, `state.agentsLoaded`, and `state.agentsError`. The adapters normalize Python `agentId/name` into Node `id/title`, chains `steps[]` into `agents[]`, and DAG `nodes[].deps` into `edges[]`.

Web Components: `public/js/components/agent-modal.js`, `agent-monitor-modal.js`, `chain-modal.js`, `dag-editor-modal.js`, `workflow-modal.js`, plus feature modules `agents.js`, `agent-monitor.js`, and `dag-editor.js`.

Shape-level diff: both repos have `agents.js` and the modal components. Node’s backend route is `server/routes/agents.js` under one `/api/agents` prefix; Python splits it into `/api/agents`, `/api/agent-chains`, `/api/agent-dags`, and `/api/agent-runs`.

#### B. Backend

Python route modules:

- `artemis/routes/builders/agents.py`
- `artemis/routes/builders/agent_chains.py`
- `artemis/routes/builders/agent_dags.py`
- `artemis/routes/builders/agent_runs.py`
- `artemis/routes/builders/execution.py`

Endpoints:

- `GET /api/agents/` → `{ "agents": [AgentRead...] }`
- `POST /api/agents/` → `AgentRead`, 201
- `GET /api/agents/{agent_id}` → `AgentRead`
- `PATCH /api/agents/{agent_id}` → `AgentRead`
- `DELETE /api/agents/{agent_id}` → 204
- `GET /api/agents/{agent_id}/runs` → `{ "runs": [AgentRunRead...] }`
- `POST /api/agents/{agent_id}/run` → `AgentRunRead`
- `GET /api/agent-chains/`, `POST /api/agent-chains/`, `GET/PATCH/DELETE /api/agent-chains/{chain_id}`
- `POST /api/agent-chains/{chain_id}/run` → `{ "runs": [AgentRunRead...] }`
- `GET /api/agent-dags/`, `POST /api/agent-dags/`, `GET/PATCH/DELETE /api/agent-dags/{dag_id}`
- `POST /api/agent-dags/{dag_id}/run` → `{ "results": { nodeId: AgentRunRead } }`
- `GET /api/agent-runs/` → `{ "runs": [AgentRunRead...] }`
- `GET /api/agent-runs/{run_id}` → `AgentRunRead`
- `GET /api/agent-runs/{run_id}/context` → `{ "context": [AgentContextRead...] }`

Return shape: Python models use camelCase aliases (`agentId`, `systemPrompt`, `maxIterations`, `createdAt`) and envelope list responses. The adapter can normalize this, but only if the list calls reach the backend.

Database tables: `agents`, `agent_runs`, `agent_context`, `agent_chains`, `agent_dags` exist. Live counts: `agents|1`, `agent_runs|1`, `agent_chains|0`, `agent_dags|0`.

#### C. Stuck Loading Trace

Exact live curl trace from the running Python app:

```text
GET http://localhost:8000/api/agents
HTTP/1.1 404 Not Found
content-type: application/json

{"detail":"Not Found"}
```

The same route with a trailing slash succeeds:

```text
GET http://localhost:8000/api/agents/
HTTP/1.1 200 OK
content-type: application/json

{"agents":[{"id":1,"agentId":"ws-rid-agent","name":"WS Integration Agent","description":null,"goal":"Test WS events","systemPrompt":"You are a test agent.","tools":[],"model":"claude-sonnet-4-6","provider":"anthropic","maxIterations":10,"ownerUserId":null,"createdAt":"2026-05-18T19:10:05.074660Z","updatedAt":"2026-05-18T19:10:05.074660Z"}]}
```

The frontend’s first request is the no-slash URL because `fetchAgents()` calls `fetch("/api/agents")`. `loadAgents()` awaits `Promise.all([fetchAgents(), fetchChains(), fetchDags(), fetchAgentMetrics()])`; any one hard failure prevents `agentsLoaded` from becoming true. `GET /api/agent-chains` and `GET /api/agent-dags` have the same no-slash 404 pattern, so there are three blocking requests in the first load batch.

Additional live traces:

```text
GET http://localhost:8000/api/agent-chains
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

GET http://localhost:8000/api/agent-chains/
HTTP/1.1 200 OK
{"chains":[]}
```

Render gate: `agents.js` sets `agentsLoading=true`, then on catch sets `agentsError` and `agentsLoaded=false`, then finally sets `agentsLoading=false`. If the page remains spinner-only, the render layer is not surfacing `agentsError`; the network failure above is the root request failure.

#### D. Compared To Node

Node’s `server/routes/agents.js` exposes all agent, chain, DAG, run-observability, instruction-file, supporting-file, and assigned-skill subresources under `/api/agents`. Node stores agent/chain/DAG definitions in JSON files and run/context history in SQLite.

Behaviors to preserve:

- `/api/agents` list works without a trailing slash.
- Chain and DAG list endpoints are available under the URLs the frontend calls, or the frontend adapters are updated consistently.
- Agent detail is enriched with `instructionFileExists`, `linkedSkills`, and `supportingFileCount`.
- Instruction files can be read/saved/deleted.
- Supporting files and assigned skills can be listed per agent.
- Run observability supports active, recent, search, by-id, and context lookups.
- Agent package policy fields (`fallbackProvider`, `fallbackModel`, `memoryPolicy`, `permissionMode`, `outputContract`) are validated and persisted.

#### E. Gap Summary

Working: Python has real builder tables, CRUD routes, run routes, execution wiring, and at least one live agent/run row.

Broken: Initial frontend load calls no-slash URLs that return 404. Several Node-compat subresources still 404. Route split (`/api/agent-chains`) differs from Node (`/api/agents/chains`) but the adapter already targets Python’s split path.

Missing: Agent instruction/files/skills endpoints, legacy run-observability aliases under `/api/agents/runs/*`, `/api/agents/context/:runId` alias, agent package policy fields, enriched agent detail.

#### F. Suggested Divvy

Owner: Codex for the no-slash compatibility and route aliases; Worker for the package/enrichment persistence slice.

Scope estimate: ~100 LOC to unblock the spinner and alias list endpoints; ~500 LOC for full Node parity on instruction files, supporting files, skill assignment, and package policy.

### 4. Workflows

#### A. Frontend

The Workflows shell is `loadWorkflowsShell()` in `home.js`, which calls `loadWorkflows()` from `public/js/features/workflows.js`. The Operations Workflows tab also uses `refreshWorkflowsFromApi()` in `operations-shell.js`.

API calls:

- `GET /api/workflows` via `fetchWorkflows()`
- `POST /api/workflows` via `createWorkflow(workflow)`
- `PATCH /api/workflows/:id` via `updateWorkflow(id, workflow)`
- `DELETE /api/workflows/:id` via `deleteWorkflowApi(id)`
- `POST /api/workflows/:id/run` via `runWorkflowApi(id)`
- `GET /api/workflows/:id/runs` via `listWorkflowRunsApi(id)`
- `GET /api/workflows/:id/runs/latest` via `getLatestWorkflowRunApi(id)`

State/data expected: `state.workflows`, `state.workflowsLoaded`, `state.workflowsError`, and workflow rows normalized into Node shape: `id`, `title`, `description`, `steps[]`; each step expects labels/prompts for display.

Web Components: `public/js/components/workflow-modal.js`; most behavior lives in `public/js/features/workflows.js` and Operations shell detail panels.

Shape-level diff: both repos have `public/js/features/workflows.js`. Node has `server/routes/workflows.js`; Python has `artemis/routes/builders/workflows.py` and `execution.py`.

#### B. Backend

Python route module: `artemis/routes/builders/workflows.py`, plus execution route in `artemis/routes/builders/execution.py`.

Endpoints:

- `GET /api/workflows/` → `{ "workflows": [WorkflowRead...] }`
- `POST /api/workflows/` → `WorkflowRead`, 201
- `GET /api/workflows/{workflow_id}` → `WorkflowRead`
- `PATCH /api/workflows/{workflow_id}` → `WorkflowRead`
- `DELETE /api/workflows/{workflow_id}` → 204
- `GET /api/workflows/{workflow_id}/runs` → `{ "runs": [WorkflowRunRead...] }`
- `POST /api/workflows/{workflow_id}/run` → `WorkflowRunRead`

Mismatch: the frontend calls `GET /api/workflows`, but Python only answers `GET /api/workflows/`.

```text
GET http://localhost:8000/api/workflows
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

GET http://localhost:8000/api/workflows/
HTTP/1.1 200 OK
{"workflows":[]}
```

Python does not implement `GET /api/workflows/:id/runs/latest`; the frontend tolerates 404 and returns `null`.

Database tables: `workflows` and `workflow_runs` exist. Live counts: `workflows|0`, `workflow_runs|0`.

#### C. Stuck Loading

n/a, but Workflows shares the same no-slash 404 failure pattern as Agents.

#### D. Compared To Node

Node’s workflow route stores definitions in `workflows.json`, returns flat arrays, uses `PUT` for updates, creates a run record, returns `{ runId }` with 202, and executes the workflow asynchronously in the background through `server/workflow-runner.js`.

Behaviors to preserve:

- `/api/workflows` works without a trailing slash.
- The list shape is normalized to the frontend’s expected `id/title/steps`.
- Manual run returns quickly and records durable run state.
- Latest-run endpoint supports the Operations detail panel.
- Workflow steps remain ordered and each step’s prompt/label survive round trip.

#### E. Gap Summary

Working: Python has workflow CRUD, DB tables, run table, and synchronous execution wiring.

Broken: Initial no-slash list URL 404s. Latest-run route is missing but gracefully treated as `null`.

Missing: Async/background run semantics from Node, latest-run endpoint, possibly Node-compatible `{ runId }` launch response if the UI depends on it.

#### F. Suggested Divvy

Owner: Codex for route compatibility and latest-run endpoint; Worker for async run semantics if needed.

Scope estimate: ~100-200 LOC for compatibility; ~300-500 LOC if adding background execution behavior and richer run-state parity.

### 5. Memory

#### A. Frontend

The Memory shell is imported into `home.js` from `public/js/features/memory-shell.js`; `home.js` calls `loadMemoryShell()` when the active view is `MEMORY_VIEW`.

Initial load calls:

- `GET /api/memory?project=&category=` via `fetchMemoryList(projectPath)`
- `GET /api/memory/stats?project=` via `fetchMemoryStats(projectPath)`
- `GET /api/sessions?project_path=` via `fetchSessions(projectPath)`
- `GET /api/agents` via `fetchAgents()`

Additional Memory actions call:

- `GET /api/memory/search?project=&q=&limit=`
- `GET /api/memory/top?project=&limit=`
- `GET /api/memory/embeddings/status`
- `POST /api/memory/embeddings/ensure`
- `GET /api/memory/archive/export`
- `POST /api/memory/archive/sqlite-backup`
- `POST /api/memory/archive/import/dry-run`
- `POST /api/memory/archive/import/apply`
- `GET /api/memory/:observationId/evidence`
- `GET /api/memory/drawer/:drawerId`
- `GET /api/memory/entities?scopeKind=&scopeId=&kind=&limit=`
- `GET /api/memory/entities/:entityId/neighborhood?hops=`
- `PUT /api/memory/:id`
- `DELETE /api/memory/:id`
- `POST /api/memory/`
- `POST /api/memory/optimize`
- `POST /api/memory/optimize/apply`
- `POST /api/skills` when promoting memory to a skill

State/data expected: Node-style memory rows with `id`, `project`, `category`, `content`, recency/score metadata, evidence links, scopes/wings/rooms, graph entities, stats counts by category, embedding readiness status, and archive import/export results.

Web Components: `public/js/components/memory-inspector.js` exists, but the main shell is `memory-shell.js`.

Shape-level diff: both repos have `memory-shell.js` and memory frontend code. Node has `server/routes/memory.js`; Python has memory keystone modules under `artemis/memory/`, but no FastAPI memory route module mounted.

#### B. Backend

Python route module: MISSING for HTTP. There is no `artemis/routes/memory.py` and no `/api/memory` routes mounted in `artemis/main.py`.

Live trace:

```text
GET http://localhost:8000/api/memory
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Python does have substantial non-HTTP memory backend modules: storage, embeddings, retrieval, consolidation, graph extraction, archive, backup, raw inputs, and MCP read handlers.

Database tables: memory tables exist. Live counts: `memory_drawers|0`, `memory_observations|1`, `memory_evidence|0`, `memory_entities|0`. The one observation appears to be test/dev residue, not populated UI data.

Status inventory marks `memory-shell` unavailable:

```json
"unavailable_surfaces":["analytics","chat","cost-dashboard","dags","memory-shell","projects","sessions","telegram","voice"]
```

#### C. Stuck Loading

n/a for Agents. Memory will fail once a project is selected because both `/api/memory*` and `/api/agents` no-slash calls fail. If no project is selected, the shell renders the project-required prompt without calling memory APIs.

#### D. Compared To Node

Node’s `server/routes/memory.js` exposes the complete UI HTTP contract over the keystone: list/search/top/stats, create/update/delete via supersession, evidence and drawer lookup, archive export/import, SQLite backup, embedding readiness/download, optimization/apply, maintenance, entities, aliases, mentions, relations, and entity neighborhoods.

Behaviors to preserve:

- Lossless rule: delete/supersede removes observations from active retrieval without deleting drawers/evidence.
- Project-scoped list/search/top/stats endpoints feed the UI.
- Evidence and drawer detail are first-class inspectable endpoints.
- Archive import/export remains portable and non-destructive.
- Entity and neighborhood endpoints back the wings/rooms graph UI.
- Embedding readiness is explicit; startup does not block on model download.

#### E. Gap Summary

Working: Python memory keystone internals and tables exist, including graph/MCP modules.

Broken: The Memory UI HTTP contract is absent; `/api/memory*` 404s.

Missing: FastAPI memory routes, Node-compatible row shapes, stats/search/top wrappers, archive/backup HTTP routes adapted from SQLite naming, optimizer HTTP routes, graph HTTP routes.

#### F. Suggested Divvy

Owner: Lead + Worker. Lead should define the Python HTTP compatibility contract because the internals are cleaner than Node and the lossless rule is load-bearing; Worker can port route wrappers and tests once the contract is set.

Scope estimate: 1000+ LOC including tests. The internals exist, but the UI-facing contract is broad and safety-sensitive.

## Cross-cutting Observations

- The copied frontend expects Node-friendly no-slash collection URLs. Python builders define `@router.get("/")` under prefixes, so `/api/<thing>/` works but `/api/<thing>` 404s. This breaks Agents, Skills, Workflows, Chains, and DAGs before shape adapters can help.
- Python status marks `agents`, `skills`, and `workflows` available even though their frontend entry URLs fail; it correctly marks `memory-shell` unavailable. There is no `automations` status entry.
- The Python API client already contains useful adapters for builder shapes: list envelopes to arrays, `agentId/name` to `id/title`, chain `steps` to `agents`, and DAG `deps` to `edges`. The next work should preserve that adapter layer or move compatibility server-side consistently.
- Node kept agent, chain, and workflow definitions in JSON files while Python moved them to Postgres. That is good for the rebuild, but package-like file behavior still matters for agent instructions and skill bodies.
- Automations and Memory are not merely shape mismatches; they are unmounted backend surfaces. Memory has internal modules but no UI HTTP layer. Automations has neither tables nor routes.
- Current live DB is essentially empty for this slab: one agent/run from a test path, zero skills, zero workflows, zero chains/DAGs, zero drawers, one memory observation, and no automation tables.
- Several frontend helpers silently treat 404 as “not wired yet.” That keeps the app from crashing, but it can hide incomplete parity unless the report/brief names each missing endpoint.

## Recommended Sequencing

1. Fix no-slash collection route compatibility for builders: `/api/agents`, `/api/skills`, `/api/workflows`, `/api/agent-chains`, `/api/agent-dags`. This is the smallest win and should unblock the visible Agents spinner.
2. Add Agents compatibility subresources: instruction file, supporting files, assigned skills, and legacy run/context aliases. This makes the shipped agent system feel real in the Operations panel.
3. Add Workflows latest-run endpoint and decide whether runs should be synchronous or backgrounded in Python. The UI can survive without latest-run, but the Operations detail panel becomes more trustworthy with it.
4. Port Skills lifecycle and assignment. Skill bodies/imports are more meaningful once Agents can display assigned skills.
5. Port Automations after Agents/Workflows are dependable targets. Automations depend on those targets and approvals.
6. Add Memory HTTP routes after Lead signs off on the compatibility contract. The internals are there, but the UI contract is wide and the lossless rule raises the bar.

## Estimated Total Effort

Total estimate: ~2500-3500 LOC including tests.

Split:

- Codex: ~200-400 LOC for route compatibility, no-slash aliases, and latest-run shims.
- Worker: ~1200-1700 LOC for Agents subresources, Skills lifecycle/assignment/imports, and Automations backend.
- Lead + Worker: ~1000+ LOC for Memory HTTP contract and tests.

Rough calendar effort: 3-5 focused half-days for builder compatibility plus Skills/Agents parity; 2-3 additional half-days for Automations; 3-5 half-days for Memory HTTP depending on how strictly the Node contract is preserved.
