# O2 + O3 — Agent Card detail surface + persona/soul

**Owner:** Lead designs (this brief is the design); Worker implements. ~500 LOC. 1 day of focused Worker time. One Lead consult flagged inline.
**Depends on:** O1 merged to lead. The new `agents.persona` column requires a migration that chains after O1's 0029.
**Blocks:** O5 (Builder nav polish) — the "commit redirects to Agent Card" flow in O5 needs the Card to exist.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

Two real product gaps surfaced during Jon's O1 empirical kill-criterion test:

1. **Agents have no identity.** The current `agents` table has `name`, `goal`, `system_prompt`, `model`, `tools`, etc. — but no concept of *who* this agent is. Jon explicitly wants each agent to feel like a character with purpose: he named one "Iris" with the role "watches and brings me insight and notification of what's happening." That's not just metadata vanity — it's the difference between "I have 9 agents" and "Iris watches my Jira, Scout monitors competitor pricing, Watcher tracks Slack pulse." Names + purposes make the catalog scannable and the outputs feel intentional.

2. **The Builder is great for creation but you can't *live* with what it creates.** Jon's exact words: *"I don't necessarily need to see all the settings here but in the agent card on the agents page (which is where you can update their profile image, prompt settings rules see and edit the supporting files if wanted and things like that)."* The Builder ships the definition; the Agent Card is where you tune it day-to-day. Right now there is no Agent Card — just a list row.

This brief delivers both pieces together because they're tightly coupled (the persona shapes how the card renders) and because shipping them separately would produce two awkward intermediate states.

## Vision — the user experience

### Agents catalog page

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Agents                                          [+ Build with Agent-Builder] │
├─────────────────────────────────────────────────────────────────────────────┤
│  [👤]  Iris                              Anthropic · claude-sonnet-4-6     │
│  ●     Watches my Jira board and brings  Last run: 2h ago · 4 runs today   │
│        morning insight to me directly                                       │
│        🔧 7 tools · 📎 2 files · 📋 1 linked skill                          │
│                                                                             │
│  [👤]  Scout                             Anthropic · claude-opus-4-7        │
│        Monitors competitor pricing       Last run: yesterday · 0 runs today │
│  ●     pages, alerts on changes                                             │
│        🔧 4 tools · 📎 0 files · 📋 2 linked skills                         │
│                                                                             │
│  [👤]  ws-rid-agent                      Anthropic · claude-sonnet-4-6      │
│        (no persona set)                  Last run: never · 0 runs ever      │
│        🔧 0 tools · 📎 0 files · 📋 0 linked skills                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

Each row is now a real card: avatar (the profile image), name, purpose, model+provider, recent activity stats, and a quick-glance count of attachments. Clicking a row drills into the detail.

### Agent Card detail surface (the new one)

Two layouts depending on viewport — wide screens get side-by-side, narrow screens stack. Wide:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [← Agents]   Iris                                  [Edit with Builder]      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐  PERSONA                            RECENT RUNS               │
│  │          │  Name: Iris                         ✓ 2h ago  · 1m12s         │
│  │  [img]   │  Purpose: Watches my Jira board     ✓ 5h ago  · 47s           │
│  │          │  and brings morning insight         ✓ yesterday · 1m08s       │
│  └──────────┘  Voice: lowercase, concise          ✗ 2d ago · stalled at...  │
│  [Edit img]    Ghostwrite: yes — messages feel    [view all 24 runs]        │
│                like they came from Jon                                      │
│                                                                             │
│                                                                             │
│  SYSTEM PROMPT                                    SETTINGS                  │
│  ┌─────────────────────────────────────────┐     Provider: Anthropic        │
│  │ You are Iris. Your job is to watch...   │     Model: claude-sonnet-4-6   │
│  │                                          │     Max iterations: 10        │
│  │ [editable inline]                        │     Memory policy: agent_scoped│
│  │                                          │     Permission mode: ask      │
│  └─────────────────────────────────────────┘     Fallback: claude-haiku-4-5│
│  [Save] [Cancel]  [Regenerate via Builder]       [Edit settings]            │
│                                                                             │
│                                                                             │
│  TOOLS                                            LINKED SKILLS             │
│  • slack.send_message       [✗ remove]            • extract-blockers        │
│  • slack.read_channel       [✗ remove]              [✗ unassign]            │
│  • jira.list_issues         [✗ remove]            [+ assign skill]          │
│  • jira.get_issue           [✗ remove]                                      │
│  • jira.search_issues       [✗ remove]                                      │
│  [+ add tool]                                                               │
│                                                                             │
│                                                                             │
│  SUPPORTING FILES                                                           │
│  📎 instruction.md (12.4 KB · edited yesterday)   [open] [delete]           │
│  📎 jira-team-roster.json (2.1 KB · 3 days ago)   [open] [delete]           │
│  [+ upload file]                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

Five sections, each independently editable. "Edit with Builder" in the top-right opens the Builder seeded with this agent's existing definition (the self-improvement loop the O1 brief described). Inline edits commit immediately on save.

## Architecture

### Schema additions

```sql
-- New migration (next sequential after O1's 0029):
ALTER TABLE agents ADD COLUMN persona JSONB NULL;

-- The persona shape:
-- {
--   "name": "Iris",
--   "purpose": "Watches my Jira board and brings morning insight to me directly",
--   "voice_notes": "lowercase, concise, no greetings",
--   "ghostwrite": true,            -- when true, outputs framed as if from the user
--   "profile_image_path": "/uploads/agents/{agent_id}/avatar.png" | null
-- }

-- Existing agents without persona render with placeholder + (no persona set) badge.
```

`profile_image_path` is a server-side file reference, not a URL. The endpoint that serves it (`GET /api/agents/{id}/avatar`) handles auth + cache headers.

🚦 **Lead consult**: should `persona` be a separate table or a JSONB column on `agents`? JSONB is simpler now but loses queryability (e.g. "find all agents that ghostwrite"). I lean JSONB — querying agent personas isn't a real use case in v1 and the field is small. Worker can flag if they hit a case I haven't thought of.

### Backend routes

Three additions, all under existing `/api/agents/` prefix:

```
PATCH  /api/agents/{agent_id}/persona              # body: persona JSONB; returns updated agent
POST   /api/agents/{agent_id}/avatar               # multipart upload; writes to profile_image_path
GET    /api/agents/{agent_id}/avatar               # serves the image (with cache headers)
```

The existing `PATCH /api/agents/{agent_id}` already handles the other inline edits (system prompt, settings, etc.) — no new route needed for those.

`GET /api/agents/{agent_id}` enriched response should now also include:
- `linked_skills`: list of `{slug, name, description}` from the agent_skills join
- `supporting_files`: list of `{filename, size_bytes, modified_at}` from the existing files dir
- `recent_runs`: array of last 10 runs with `{id, status, duration_s, started_at, trajectory_summary}`

Most of this is already in place from J11 — just need to combine into the detail endpoint response. ~50 LOC of route handler work.

### Ghostwriting frame

When `persona.ghostwrite == true`, the agent's output destination should NOT show the agent as sender. Concretely:

- If the agent sends a Slack message via `slack.send_message`, the message body should read like Jon wrote it (system prompt instructs the agent on voice; persona reinforces). The Slack username is still Jon's connected bot identity.
- If the agent posts to a channel, no "from Iris" prefix unless the user explicitly opts in.

Implementation: the `ghostwrite` flag gets injected into the agent's system prompt at run-time as a directive: *"Your output is framed as if Jon wrote it. Do not refer to yourself as 'Iris' or 'I' as the agent. Match Jon's voice precisely."*

That's a soft contract — the LLM can't be hard-prevented from breaking it, but a well-tuned system prompt + voice samples from the existing personality profile (already loaded by floating-artemis) makes it reliable.

The personality profile loader at `artemis/floating_artemis/personality.py` exposes `PERSONALITY_PROFILE` and `select_voice_samples()`. When `persona.ghostwrite` is true, the agent's system prompt builder should call these and prepend the voice samples.

### Frontend

Three changes:

1. **Agents catalog page** (`public/js/features/agents.js` or wherever it lives — grep `loadAgents`): replace the existing list-row rendering with the card layout described above. Each card pulls from the enriched `GET /api/agents` response.

2. **Agent Card detail page** — new route `/operations/agents/{agent_id}`. Web component `agent-detail.js` that renders the 5-section layout. Inline edit pattern: click a section → it becomes editable → Save commits via `PATCH /api/agents/{agent_id}` (or `PATCH .../persona` for the persona block specifically). Cancel reverts.

3. **"Edit with Builder" button**: opens `/operations/agents/{agent_id}/builder` (URL pattern from O1). Routes the user into a new builder session seeded with this agent's existing definition (the `read_existing_agents(id)` tool the Builder already has). The Builder UI shows this as an "Edit Iris" session (with the agent name in the breadcrumb), not a fresh build.

## Acceptance criteria

The brief is shippable when:

- [ ] **Persona schema**: alembic migration adds `agents.persona` JSONB column, up/down round-trips clean
- [ ] **Persona edit**: in the Agent Card, change Iris's purpose from one string to another, save, refresh — change persists
- [ ] **Profile image upload**: upload a PNG, see it render in the Agent Card avatar slot AND in the catalog row's avatar slot
- [ ] **Catalog cards**: Agents page lists all agents with the new card layout (name, purpose, model, last run, counts). Agents without persona render with `(no persona set)` placeholder.
- [ ] **Inline system prompt edit**: edit the system prompt inline in the Agent Card, save, run the agent, confirm the new prompt is what's used
- [ ] **Tools edit**: add and remove tools from an agent inline
- [ ] **Skill assignment**: assign a skill via the Agent Card's skill panel, confirm it appears in the agent's `linked_skills` response
- [ ] **Supporting files**: upload a file, see it in the list, download it, delete it — all from the Agent Card without leaving the page
- [ ] **Recent runs**: see last 10 runs with status + duration + trajectory summary preview
- [ ] **Edit with Builder**: click "Edit with Builder" → Builder opens with this agent's existing definition pre-loaded as conversation context, breadcrumb shows "Edit Iris"
- [ ] **Ghostwrite test**: build an agent with `persona.ghostwrite = true`. Run it through a Slack send. The Slack message body matches Jon's voice (no "Hi, this is Iris..." or third-person framing)

## Quality acceptance gates

- [ ] `git diff --staged` before every commit, especially the migration commit (twice-bitten rule).
- [ ] `pwd && git branch --show-current` before commits (CWD-trap defensive reflex).
- [ ] Lead consult on the persona-storage shape (JSONB vs separate table) before the migration commit.
- [ ] Alembic migration up/down clean.
- [ ] `ruff check` + `mypy` clean.
- [ ] Tests: route tests for `/persona` PATCH (happy + invalid persona shape), `/avatar` upload (happy + non-image rejection + size limit), enriched `GET /api/agents/{id}` response (returns linked_skills + supporting_files + recent_runs).
- [ ] Manual smoke: at least one screenshot of the new Agents catalog (with personas visible) + one of the Agent Card detail surface (fully populated for a real agent).
- [ ] **Verbatim port note**: where the Agents catalog or Agent Card has visual equivalents in claudeck-artemis, port the CSS verbatim rather than designing fresh. The slop pattern of "describe + guess" was costly during Dev Projects v3; don't repeat. If no equivalent exists in claudeck (likely true for these surfaces), design from existing Artemis primitives (`page-section`, `card`, `page-hero--slim`, etc.) — don't invent new design tokens.

## Out of scope (separate briefs)

- **Agent activity dashboard** — a global "what's running right now" view across all agents. Different surface, separate brief.
- **Cross-agent workflows** — chaining outputs. Belongs in Workflow-Builder.
- **Profile image generation** — auto-generating an avatar from the persona description. Cute feature, not blocking.
- **Voice sample tuning per agent** — currently agents inherit voice samples from the global personality profile when ghostwriting. Per-agent voice tuning is later polish.
- **Avatar in the run output** — when an agent posts to Slack (without ghostwriting), should the bot's display picture show the agent's avatar? Probably yes but it's a Slack bot config issue, separate task.

## Where to start

1. Read this brief twice
2. Read `briefs/CONVENTIONS.md` ("CWD trap" + "Commit Discipline" sections — non-optional)
3. Read `artemis/routes/builders/agents.py` for the existing agent CRUD pattern
4. Read `artemis/floating_artemis/personality.py` for the voice-sample loader (you'll reuse this for ghostwrite)
5. Read `public/js/features/dev_projects.js` for the canonical "detail surface with inline editing" reference in this codebase
6. **Ping Lead** for the persona-storage consult before writing the migration
7. Backend first: migration → persona routes → enriched detail endpoint → ghostwrite system prompt assembly
8. Frontend in order: catalog cards → Agent Card detail surface → "Edit with Builder" entry point
9. Run the acceptance criteria checklist top to bottom before reporting done
10. Surface any deviations clearly in the report

## Paste-ready Worker prompt (for terminal-Lead to spawn)

```
Implement briefs/o2-o3-agent-card-and-persona.md.

Scope: ~500 LOC. Half-day to a day. Isolated worktree off lead/j6a-
granola-integration HEAD. Background execution. Branch auto-creates.

CRITICAL framing:
- This is a port-style brief, not a redesign brief. Wherever an Artemis
  primitive already exists (page-section, card, page-hero--slim, inline
  editing pattern from Dev Projects v3), USE IT verbatim. The slop
  pattern of "describe + guess" was costly during Dev Projects v3;
  don't repeat.
- One Lead consult flagged: persona storage shape (JSONB column vs
  separate table). Ping terminal-Lead before writing the migration —
  do NOT decide yourself.
- CWD-trap reflex: pwd && git branch --show-current before EVERY commit.
- git diff --staged before EVERY commit involving renames.
- Single PR / worker branch — do not merge to lead yourself.

The brief has a paste-ready acceptance checklist at the bottom. Run
each bullet against the actual running app before reporting done. No
analytical-instead-of-empirical shortcuts (the slop pattern we caught
in O1's kill-criterion gate).

Report when complete: branch SHA, LOC count per file via full-diff
insertions (no estimating), acceptance checklist verbatim with each
bullet either green or explained, manual smoke screenshots.
```
