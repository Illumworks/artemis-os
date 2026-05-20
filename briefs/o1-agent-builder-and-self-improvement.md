# O1 — Agent-Builder + Self-Improvement (the jugular)

**Owner:** Lead designs, Worker implements. ~1,400 LOC implementation (~600 backend + ~400 frontend + ~200 self-improvement integration + ~200 tests). 1-2 days of focused Worker time with Lead consults at three architectural decision points (flagged inline).
**Depends on:** Skills lifecycle port from claudeck (queued for Codex, in flight — substrate the builder uses). Should NOT block on Codex's port if it slips — the builder can stub the "propose skill" flow temporarily.
**Blocks:** Skill-Builder, Workflow-Builder, Automation-Builder (future surfaces that ride on the same Builder-Engine).

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

Jon's diagnosis verbatim: *"they are too simple and broken and rely on people knowing what they are doing but not giving them the right tools to succeed."*

The current Operations CRUD forms for Agents/Skills/Workflows/Automations make you fill in fields without thinking about coherence. You end up with brittle definitions that "look right" but fail in execution. Worse: when an agent fails, there's no feedback loop — you have to manually diagnose what went wrong and edit the definition yourself.

This brief replaces the form-based creation pattern with a **chat-with-a-Senior-Engineer pattern**: you describe the problem you want solved, a builder agent helps you design the right meta-object via conversation, the builder commits the definition, and (crucially) the builder watches the definition's real-world performance and proposes improvements over time.

**Going for the jugular = Agent-Builder first.** Skills/Workflows/Automations are specializations of the Agent concept; build the conversational builder for Agents right and the same engine extends to the other three. Per Jon: *"we will be needing to build agents first, skills and workflows will come along with that."*

## Vision — the user experience

```
User: "Build me an agent that drafts weekly brief emails to my team based on Jira progress."

Agent-Builder: "Got it. Let me ask a few things first:
  • What's in the email? Just status, or also blockers and risks?
  • Who's the audience — your direct team, or also stakeholders?
  • What voice — formal, casual, your usual Slack tone?
  • When should this run? Friday afternoon, Monday morning?"

User: [answers]

Agent-Builder: "Here's the draft definition:
  Name: weekly-brief-author
  Goal: Draft a weekly status email summarizing Jira progress for [team]
  System prompt: [generated, ~150 words tuned to user's voice samples]
  Tools: jira.list_issues, jira.get_issue, slack.search_messages (for blockers context)
  Model: claude-sonnet-4-6 (well-suited for structured writing)
  Trigger: Friday 3pm (becomes an Automation that wraps this agent)
  
  I'm proposing two new skills along with it:
  • 'extract-blockers-from-slack' — searches recent Slack threads for risk signals
  • 'team-roster-lookup' — knows who's on your team without you having to list them each time
  
  Want me to test-run this against last week's data before we save?"

User: "Yes, run it"

Agent-Builder: [fires a test run] "Here's what it produced: [draft email]. 
  The blockers section is light because slack.search_messages doesn't have a 
  scope for #engineering — should I propose a permissions change, or scope 
  this agent narrower?"

User: "Propose the permission change"

Agent-Builder: [stages the permission proposal, surfaces it to user's approval queue, 
  saves the agent in 'proposed' state] "Done. Once you approve the scope change in 
  Connectors, the agent goes live and the Friday automation fires."

[Two weeks later, after the agent has run 4 times]

Agent-Builder (when user re-opens this agent): "I've reviewed the last 4 runs. 
  Pattern: emails are getting marked 'too long' in Slack reactions. I'm 
  proposing a revision to the system prompt that targets 200 words instead of 
  400. Diff: [shown]. Approve to roll it in."
```

That's the loop. Conversation → draft → test → commit → observe → propose improvements.

## Architecture

### Three reusable pieces

**1. Builder-Engine** (the substrate, ~300 LOC)

A conversational scaffold that powers any future Builder surface. Not user-facing on its own; provides the primitives:

- **Conversation state**: `builder_sessions` table tracks the in-flight builder conversation (analog of `dev_projects.sessions`)
- **Proposal state machine**: `draft → proposed → committed | rejected | superseded`
- **Test-run sandbox**: way to fire a trial run of a not-yet-committed definition
- **Diff renderer**: shows "current definition vs proposed change" cleanly
- **Tool primitives** the builder agent uses:
  - `read_existing(kind)` — see what's already in the catalog (agents/skills/etc.)
  - `read_capabilities()` — provider models, available tools, integrations
  - `read_recent_runs(agent_id, limit=10)` — for the self-improvement flow
  - `propose(kind, definition)` — stage a draft for review
  - `test_run(definition, prompt)` — sandboxed execution
  - `commit(proposal_id)` — graduate to real definition

**2. Agent-Builder** (first surface using the engine, ~600 LOC)

The actual builder agent. Has:
- Its own system prompt (tuned for "senior engineer designing agent definitions")
- Its own tool list (the Builder-Engine primitives + read access to user's existing agents/skills)
- Its own UI surface: chat at `/operations/agents/builder` (or wherever fits the routing)
- Lives alongside the existing CRUD form initially — both routes work, builder is the new path

**3. Self-improvement integration** (~200 LOC)

Closes the loop. Three triggers, each independent:

- **On-demand**: user re-opens an existing agent in the builder. Builder reads `read_recent_runs(agent_id)`, summarizes patterns ("3 of last 5 runs hit max_iterations before completing"), proposes definition changes.
- **Reactive**: every agent_run completion writes a one-line trajectory summary (what worked / what stalled / what was missing). When the user next opens the builder for that agent, those summaries are pre-loaded as context.
- **Proactive (deferred to v2 of this brief)**: background job watches for run patterns across agents. Surfaces "you have 3 agents that all could use a `search-confluence` skill — want me to draft it?" — too complex for v1, but the architecture should leave room.

### Lead consults (3 points where I want to pre-approve before Worker codes)

🚦 **Decision 1**: Builder-Engine vs Agent-Builder coupling. Are they really separable now, or does v1 ship them entwined and we refactor when Skill-Builder demands it? Lead call before Worker starts.

🚦 **Decision 2**: Test-run sandbox safety. A draft agent can call real tools. What's the blast radius for a misbehaving test run? Need a sandboxed-tool-subset or rate-limit before this is safe in production. Lead call before backend ships test_run.

🚦 **Decision 3**: Self-improvement attribution. When the builder proposes a definition change based on recent runs, the proposal must include "I observed X across run #s 47, 51, 53" — so the user can audit. Lead call on the citation format.

## Schema additions

```sql
-- New: in-flight builder conversations
CREATE TABLE builder_sessions (
  id              SERIAL PRIMARY KEY,
  builder_kind    TEXT NOT NULL,        -- 'agent' | 'skill' | 'workflow' | 'automation' (only 'agent' used in v1)
  target_id       INTEGER NULL,         -- if non-null, this is an EDIT session for existing definition
  user_id         TEXT NULL,            -- Jon, eventually multi-user
  status          TEXT NOT NULL,        -- 'active' | 'committed' | 'abandoned'
  conversation    JSONB NOT NULL,       -- full message history for resumption
  draft           JSONB NULL,           -- current draft of the meta-object
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- New: proposed definitions waiting for user approval
CREATE TABLE definition_proposals (
  id              SERIAL PRIMARY KEY,
  builder_session_id INTEGER REFERENCES builder_sessions(id) ON DELETE SET NULL,
  kind            TEXT NOT NULL,        -- 'agent' | 'skill' | 'workflow' | 'automation'
  target_id       INTEGER NULL,         -- non-null = revision of existing; null = new
  proposed_by     TEXT NOT NULL,        -- 'user' | 'builder' | 'self-improvement'
  proposed_definition JSONB NOT NULL,
  citations       JSONB NULL,           -- e.g. {"run_ids": [47, 51, 53], "rationale": "..."}
  status          TEXT NOT NULL,        -- 'pending' | 'approved' | 'rejected' | 'superseded'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- New: trajectory summaries per agent run (input to self-improvement)
CREATE TABLE agent_run_trajectory_summaries (
  run_id          INTEGER PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
  what_worked     TEXT NULL,
  what_stalled   TEXT NULL,
  what_was_missing TEXT NULL,
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

One migration, three tables. Standard alembic conventions. `git diff --staged` before commit (twice-bitten rule).

## Backend

### `artemis/builder/` package

- `__init__.py`
- `engine.py` — Builder-Engine primitives (read/propose/test_run/commit), used by any builder
- `agent_builder.py` — the Agent-Builder's system prompt + tool list + conversation handler
- `trajectory_summarizer.py` — generates per-run summaries (calls `resolve_adapter()` with the trajectory prompt; runs as part of agent-run completion)
- `routes.py` — HTTP routes (see below)

### Routes

```
GET    /api/builder/sessions                           # list user's in-flight builder sessions
POST   /api/builder/sessions                           # start a new session (body: {kind, target_id?})
GET    /api/builder/sessions/{id}                      # session detail + conversation
POST   /api/builder/sessions/{id}/messages             # send user message, get builder response (streams)
DELETE /api/builder/sessions/{id}                      # abandon

POST   /api/builder/sessions/{id}/test-run             # fire a test run of current draft
GET    /api/builder/sessions/{id}/test-run/{run_id}    # poll/stream test run output

GET    /api/builder/proposals                          # list pending proposals
GET    /api/builder/proposals/{id}                     # one proposal + diff
POST   /api/builder/proposals/{id}/approve             # commit to real definition tables
POST   /api/builder/proposals/{id}/reject              # decline

GET    /api/agents/{agent_id}/builder-context         # what the builder sees when you open an existing agent: recent runs + trajectory summaries + pending proposals
```

### Self-improvement hook

Existing agent-run completion path (`artemis/agent_loop.py` or wherever runs land) gets one new line: after the run row commits, fire `trajectory_summarizer.summarize_async(run_id)`. Runs in background, writes a row to `agent_run_trajectory_summaries`. No latency added to the run itself.

## Frontend

### Chat surface

Reuse the Dev Projects v3 chat shell pattern (already polished — composer at bottom, scrollable history above). New route `/operations/agents/builder` (or however Operations sub-routing settles).

- Left rail: list of in-flight builder sessions (similar to Dev Projects sessions list)
- Center: chat with the Agent-Builder
- Right rail (when active): live preview of the current `draft` definition. Shows the JSON-ish definition that's accumulating from the conversation. Updates as the builder commits to fields.

When the user re-opens an existing agent in the builder, the right rail starts pre-populated with the current definition, and the center starts with the builder's "I've reviewed your recent runs, here's what I noticed..." opener.

### Proposal review surface

Modal or sub-page at `/operations/proposals/{id}`:
- Side-by-side diff: current definition (left) vs proposed (right)
- Citation block: "Based on runs #47, #51, #53 — they all stalled at the same point because [X]"
- Approve / Reject buttons
- "Open in Builder" — re-enters the conversation if user wants to tweak before approving

### Coexistence with current Agents form

Keep the current Agents form route working. Add a "Build with Agent-Builder" button at the top of the form. Once Builder is solid, deprecate the raw form (Phase 2).

## Acceptance criteria

The brief is shippable when:

- [ ] **Conversation flow works**: user opens Agent-Builder, describes intent, builder asks clarifying questions across 3-5 turns, generates a draft agent definition, user approves, agent appears in the agents catalog.
- [ ] **Test-run works**: user can fire a test run from inside the builder conversation, see output inline, iterate on the definition.
- [ ] **Skill co-proposal works**: when the builder generates an agent definition that implies a missing capability, it proposes a new Skill in the same flow. User approves both atomically.
- [ ] **Resume works**: user closes the builder mid-conversation, comes back later, the session is exactly where they left it.
- [ ] **Edit-existing works**: user opens an existing agent in the builder. Builder reads recent run summaries, surfaces patterns, proposes definition changes. User approves or rejects.
- [ ] **Trajectory summaries land**: every agent run completion writes a row to `agent_run_trajectory_summaries` within 30s. Verify by running an agent 3 times and querying the table.
- [ ] **Citations show on self-improvement proposals**: when the builder proposes a change based on recent runs, the proposal record includes `citations.run_ids` and `citations.rationale`. The proposal review UI displays them.
- [ ] **Coexistence**: the current Agents CRUD form still works — Builder is additive, not replacing.

## Quality acceptance gates

- [ ] `git diff --staged` before every commit. Especially for the migration commit — the rename-without-content-staged pattern has bit this project twice.
- [ ] `pwd && git branch --show-current` before commits. CWD-trap defensive reflex.
- [ ] Lead 30-min consult at each of the three decision points above before Worker codes that area.
- [ ] Alembic migration up/down round-trip cleanly.
- [ ] `ruff check` + `mypy` clean across the new `artemis/builder/` package.
- [ ] Tests: route tests for every endpoint (happy + 1 failure path); unit tests for `trajectory_summarizer.summarize_async()` (handles partial run data, handles model errors); integration test that runs the full flow: open session → exchange 3 turns → propose → approve → verify agent in agents table.
- [ ] Manual smoke output pasted verbatim in the Worker's report: at least one full conversation trace + one self-improvement-on-existing-agent trace + screenshots of the proposal review UI.

## Out of scope (future briefs)

- **Skill-Builder, Workflow-Builder, Automation-Builder** — once Agent-Builder is solid and the Builder-Engine has proven its abstractions, these extend the engine with surface-specific prompts. Each is ~200-400 LOC follow-up.
- **Proactive background pattern detection** — the "watch all agent runs and surface cross-cutting opportunities" loop. Architecturally room is left for it (the trajectory summaries are the substrate), but the actual cron + LLM call layer is v2.
- **Multi-user builder sessions** — currently single Jon user. Multi-user adds session ownership semantics that don't matter yet.
- **Versioning / rollback of agent definitions** — `definition_proposals` records history, but a user-facing "see all past versions of this agent and roll back to v3" UI is separate.
- **Builder personality inheritance from Floating Artemis profile** — should the Agent-Builder talk in Jon's voice? Probably yes, but that's a small follow-up that reads `personality.PERSONALITY_PROFILE` and prepends to the system prompt.

## Kill criterion

This brief is ambitious. If at the halfway implementation point (~700 LOC in) the Worker reports they can't get the builder to produce *a clearly better agent definition than the current form would have produced*, **STOP and reconvene with Lead**. The failure mode for ambitious AI features is shipping something that "works" but doesn't actually help. Better to halt at halfway and rethink than ship a v1 that disappoints.

Concrete kill test: pick a real agent Jon wants to build (e.g. "weekly brief author"). Build it once via the current form (Jon hand-writes the definition). Build it once via the Builder (Builder leads the conversation). Compare side-by-side. If the Builder's version is not noticeably better (better system prompt, more coherent tool selection, fewer obvious gaps), the Builder isn't pulling its weight and we rethink.

## Where to start

1. Read this brief twice
2. Read `briefs/CONVENTIONS.md` "CWD trap" and "Commit Discipline" sections
3. Read `artemis/agent_loop.py` and `artemis/routes/builders/agents.py` to understand the current agent lifecycle
4. Read `artemis/floating_artemis/chat.py` for the existing chat-with-agent pattern (Builder will mirror this structure)
5. Ping Lead for Decision 1 (Builder-Engine vs Agent-Builder coupling)
6. Schema migration first (testable in isolation)
7. Backend in order: engine primitives → agent-builder system prompt + tools → routes
8. Ping Lead for Decision 2 (test-run sandbox safety) before implementing test_run
9. Frontend last — the backend should be fully smoke-testable via curl before the UI is touched
10. Ping Lead for Decision 3 (citation format) before wiring self-improvement proposals
11. Self-improvement integration last
12. Verify against acceptance criteria + kill criterion before reporting done

## Lineage note

This brief is labeled `O1` — first brief in the Operations slab proper (J-series has been the Personal slab). Subsequent Operations briefs follow O2, O3, etc. Marketing slab will be M-series when we get there.
