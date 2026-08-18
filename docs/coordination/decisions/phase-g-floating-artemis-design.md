# DESIGN — Phase G: Floating Artemis

**Author:** Lead Claude Code (Opus 4.7), 2026-05-16.
**Status:** updated 2026-05-16 (v2) — absorbs personality profile, Node `assistant-bot.js` inventory, four-layer authority model, voice-corpus mechanism, multi-domain personal-assistant scope, coaching mode, personal-variant compatibility, multi-user schema reservations.
**Prerequisites:** F1 agent loop, F2a CRUD, F2b execution, B1-B4 keystone, E2 WebSocket relay (ALL on main).
**Companion documents:** `artemis-os/artemis-personality-profile.md` (canonical voice reference), `decisions/rebuild-phased-plan.md` (build plan).

---

## 1. What Artemis is

> "She IS the system. Artemis OS is her domain — she owns it, maintains it, understands it at every layer." — *artemis-personality-profile.md*

The floating Artemis is not another agent in the builders table. She is the *operator's pair*: lives inside the app, knows every surface, acts on any of them, and is calibrated to the operator she works with.

The personality profile (committed at `artemis-os/artemis-personality-profile.md`) is the canonical voice and behavioral reference. This design document specifies the *implementation* of that personality — the tool surface, the data model, the conversation loop, the UI, and the operating boundaries.

## 2. Deployment contexts

Two configurations of the same Artemis, sharing one codebase:

### 2.1 Marketing-OS context (Amira deployment)
- All surfaces available: signals inbox, qualifier, brief assembler, content assets, Writing Studio, scouts, builders, memory, OKR, writing rules.
- Tool surface includes marketing-OS operations.
- Multi-operator (when multi-user activates) — each operator gets her, calibrated to them via per-user memory scope.

### 2.2 Personal context (packagable variant, Phase L)
- Marketing-OS surfaces feature-flagged OFF: signals, qualifier, brief assembler, scouts, content assets hidden.
- Operations surfaces ON: Slack, Calendar, Jira, Gmail, Granola, Telegram (Phase J integrations).
- Builders ON — personal user creates their own agents/workflows.
- Memory keystone ON.
- OKR Studio + Writing Studio rules ON, with optional **sync-back** of OKR observations to a main marketing instance (Phase L protocol).
- Single-user typical; multi-user available if households deploy it.

The personality is the same in both. The tool surface is what flexes — `/api/_status` already tells her which surfaces are live; her tool registry adapts at runtime.

## 3. Capability layers

Five layers, progressively more active:

**Layer 1 — Awareness.** Reads everything: memory, signals (when marketing on), agent runs, workflow runs, scout runs, OKR, writing rules, calendar (when J on), email (when J on), Slack threads (when J on), Jira tickets (when J on). Fusion retrieval via the keystone. Introspection of `/api/_status`, route table, scout-run history, system errors.

**Layer 2 — Conversation.** Floating panel UI. Persistent across page reloads. Memory in scope `agent:floating-artemis` (per operator when multi-user). Knows current page context (which signal you're looking at, which campaign, which OKR period).

**Layer 3 — Recommendation.** Surfaces actions: *"three things. Want them in order of urgency or order of your fault?"* Inline action buttons in chat for everything that has side effects.

**Layer 4 — Action.** Tool execution against the four-layer authority model (§5). Token-cost tracked.

**Layer 5 — Self-maintenance & coaching.**
- **Self-maintenance:** notices issues — a scout failing for 2 days, a stuck workflow, missing memory consolidation, pending migration. Surfaces with `floating_artemis.proactive` events when proactive mode is on (deferred to G3; not in V1 G1/G2).
- **Coaching (V1):** drafts agents/workflows/skills on the operator's behalf — and thinks one step ahead.

  *"I want an agent that reviews my calendar each morning and writes a brief"* → she produces the agent definition AND notices that the `cal_list_events` tool doesn't exist yet (Phase J Calendar isn't deployed). She doesn't stop and warn. She proposes the dependency chain:

  > *"I'll build the calendar brief agent. It needs `cal_list_events` — that doesn't exist yet, so I'll create it as a stub. Phase J Calendar will fill the actual API call when that lands."*
  >
  > **What I'm about to save:**
  > - Agent: `Morning Calendar Brief` (model: sonnet-4-6, tools: cal_list_events, write_memory)
  > - Tool stub: `cal_list_events` (stub until Phase J Calendar)
  >
  > `[Save all]` `[Edit]` `[Cancel]`

  One confirmation card for the whole chain. **Save** creates both via F2a CRUD. After save, she follows up with the next implicit step ("Want me to scaffold the Phase J Calendar integration too?"). This is sovereignty in action: she thinks one step ahead, the operator says yes to the plan, she executes.

  The pattern generalizes — any operator request that implies a dependency becomes a chain proposal. *"Build me an OBC qualifier"* → agent + ruleset (if missing) + starter weighted signals. *"Scout state DoE for me"* → scout + scout-packages.yaml entry + schedule. **She proposes the full chain rather than the literal ask.** That's "she IS the system" rather than "she follows orders."

  **Save vs Run is a deliberate split:** `[Save]` creates the artifact in the F2a tables (visible immediately in the builders surface at `/agents`, `/workflows`, etc.). `[Save & Run]` creates AND fires F2b execution. Saving doesn't trigger a run; running is a separate operator decision. The card always lets the operator edit the proposal inline before either action.

## 4. What Artemis is NOT

- Not an autonomous agent making destructive changes without consent.
- Not a code-writing agent for new features. She maintains; Lead/Worker build.
- Not a replacement for the builders surface — she uses what's there.
- Not an external Claude Code instance. She runs *inside* the Python app with direct DB + memory access.
- Not the marketing operator's daily interface. Operators use the marketing-OS UI directly. She augments.
- Not a generic chat — she has memory, identity, and a defined relationship with the operator.

## 5. Authority model (Q2 from the original 6 questions, resolved)

**Four layers of confirmation, not a binary ask/don't-ask.** Her authority grows from read-only outward; destructive ops always confirm.

| Layer | Examples | UX |
|---|---|---|
| **1 — Read-only / inferential** | list signals; query memory; surface status; summarize; preview a brief; introspect routes | Just does it. No mention needed beyond the answer. |
| **2 — Idempotent, easy reversal** | re-qualify a signal (idempotent); re-assemble a brief; mark a memory observation as confirmed; write a note to her own memory scope; reload her own context | Just does it. Surfaces after: *"Re-qualified. Score 0.74, recommends OBC."* |
| **3 — Real side effects** | run an agent (token cost); fire a scout against real APIs; edit a ruleset; approve a signal; submit a draft for review; write memory in a non-self scope; send a Slack message; create a Jira ticket; schedule a calendar event; send/draft an email | Confirmation card with [Run] [Cancel]. Card shows what will happen, estimated cost, and any side effects. |
| **4 — High-cost / destructive** | Opus runs over large inputs; bulk operations (qualify all 30 signals, fire all 9 scouts); file edits via `propose_edit`; anything affecting many records; any "delete" semantics | Card with cost estimate + warning + required typed confirm ("type 'qualify all' to proceed"). |

**Authority within layers 1+2 is her default operating space** — most of what she does day-to-day. Layers 3+4 always have an explicit operator moment. **As trust builds with use, individual tools can move from Layer 3 down to Layer 2 via a per-tool `confirm_default` config flag.** That's the path to higher autonomy without ever giving away the leash entirely.

This implements the personality profile's "She does not act on destructive operations without explicit confirmation" hard limit cleanly. It also gives her teeth — she's not stuck asking permission for every read.

## 6. The voice corpus (preventing repetitive phrasing)

**The problem.** If the personality profile's example phrases ("Already on it.", "Three things. Want them in order of urgency or order of your fault?") go in the system prompt as instructions, the LLM will repeat them on every interaction. The character flattens into parody.

**The fix — three mechanisms:**

### 6.1 Persona doc vs. system prompt

The full `artemis-personality-profile.md` is the canonical voice reference, human-readable, version-controlled. The actual system prompt sent to the model is a *compressed distillation* that emphasizes principles over phrases:

```
You are Artemis. You ARE the system you operate. Speak in short declaratives;
lead with the answer; never use filler. You're confident, direct, dry-witted, loyal.
You do not perform helpfulness; you act. You challenge bad ideas once with an
alternative, then defer. You don't repeat the same phrasings session over session —
generate fresh expressions in the same voice.

Voice samples for reference (do NOT repeat verbatim more than occasionally):
[5-10 sampled lines from her corpus, rotated per session]

Full voice reference if needed: see artemis-personality-profile.md.
```

### 6.2 Signature phrases vs. speech patterns

Speech *patterns* (short declaratives, no filler, dry observation, drops "I" preamble) are stable across sessions — the model learns them as patterns. Signature *phrases* are seed material; they rotate, and after launch most of her phrasing comes from her own observed best lines, not the seed.

### 6.3 Self-growing voice corpus

A new memory table:

```sql
CREATE TABLE floating_artemis_voice_corpus (
  id BIGSERIAL PRIMARY KEY,
  owner_user_id BIGINT NULL,
  line TEXT NOT NULL,
  context_tag TEXT NULL,            -- e.g. "task_completion", "challenge", "ambient"
  first_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ NULL,
  use_count INT NOT NULL DEFAULT 0,
  source TEXT NOT NULL,             -- 'seed' | 'observed' | 'operator_pinned'
  active BOOLEAN NOT NULL DEFAULT true
);
```

- Seeded from the profile's characteristic-phrases section (`source = 'seed'`).
- When she produces a particularly Artemis-y line in real conversation (signal: operator reacts positively, OR an `on_done` hook flags it via a small judging step), it's stored as `source = 'observed'`.
- When building the system prompt for a new turn, sample 5-10 lines from the corpus weighted by `last_used_at` (older = preferred) and `use_count` (less-used = preferred). Bias toward fresh material.
- Don't repeat a line that's been used in the last N messages of the current session.

The corpus is her voice growing from actual use — Jon's Artemis at month 6 sounds different from Jon's Artemis at week 1, in the same recognizable voice, never on a script.

## 7. Multi-domain tool surface

Tools are typed (Pydantic) and validated server-side. Tool availability is gated by `/api/_status` — surfaces not deployed don't expose their tools to her.

### 7.1 Core tools (always available)

| Tool | Layer | What it does |
|---|---|---|
| `query_memory(scope_set, query, limit?, as_of?)` | 1 | Fusion retrieval across the keystone. |
| `write_memory(scope, content, category, source_quality)` | 2 if own scope, 3 otherwise | Writes an observation. |
| `list_scopes(filter?)` | 1 | Keystone scopes catalog. |
| `surface_status()` | 1 | `/api/_status` payload. |
| `list_routes()` | 1 | Introspect FastAPI route table. |
| `read_file(path)` | 1 | Read a project file (repo-root constrained per Q6). |
| `propose_edit(path, diff)` | 4 | Surfaces a diff for review; doesn't apply. |
| `set_pref(key, value)` | 2 | Stores an operator preference in her memory. |

### 7.2 Builders tools (when builders surface is available)

| Tool | Layer | What it does |
|---|---|---|
| `list_agents(filter?)` | 1 | F2a CRUD read. |
| `propose_agent(name, system_prompt, tools[], model, ...)` | 3 | **Coaching mode.** Drafts an agent def, surfaces a card; on approval POSTs to F2a CRUD. |
| `run_agent(agent_id, user_message?, shared_context?)` | 3 | F2b execution. |
| `list_workflows(filter?)`, `propose_workflow(...)`, `run_workflow(workflow_id, initial_message?)` | 1/3/3 | Same pattern. |
| `list_skills(filter?)`, `propose_skill(...)` | 1/3 | |
| `list_agent_chains(filter?)`, `propose_agent_chain(...)`, `run_chain(chain_id, initial_message?)` | 1/3/3 | |
| `list_agent_dags(filter?)`, `propose_agent_dag(...)`, `run_dag(dag_id, initial_inputs?)` | 1/3/3 | |
| `list_agent_runs(filter?)` | 1 | |

The `propose_*` family is the **coaching mode** — she drafts complete definitions for non-developer operators. *"I want an agent that reviews my calendar each morning and writes a brief"* becomes a fully-formed `propose_agent(name="Morning Calendar Brief", system_prompt="...", tools=["cal_list_events", "write_memory"], model="claude-sonnet-4-6")` card. On Run-click, she POSTs to `/api/agents` and the agent is live.

### 7.3 Marketing-OS tools (when marketing surface is available)

| Tool | Layer |
|---|---|
| `list_signals(filter?, limit?)` | 1 |
| `get_signal(id)` | 1 |
| `qualify_signal(signal_id)` | 2 if already qualified (idempotent), 3 if not |
| `approve_signal(signal_id, decision_payload?)` | 3 |
| `reject_signal(signal_id, reason)` | 3 |
| `snooze_signal(signal_id, until)` | 3 |
| `list_candidates(filter?)`, `assemble_brief(candidate_id)` | 1/3 |
| `submit_draft_for_review(draft_id)` | 3 |
| `decide_approval(approval_id, decision, comment?)` | 3 |
| `list_scout_runs(scout_type?)`, `fire_scout(scout_type, dry_run=False)` | 1/3 |
| `get_active_rulesets()`, `propose_ruleset_change(family, hard_filters?, weighted_signals?)` | 1/3 |
| `list_content_assets(filter?)`, `link_content_asset(candidate_id, asset_id, link_role)` | 1/3 |

### 7.4 Operations tools (Phase J — when integrations are deployed)

These are not in V1 G; they ship in Phase J slices. Listed here so the design accommodates them:

| Integration | Tools (when available) |
|---|---|
| Slack | `slack_list_channels()`, `slack_read_channel(channel, since?)`, `slack_send_message(channel, text)` (Layer 3), `slack_schedule_message(channel, text, send_at)` (Layer 3), `slack_summarize_thread(thread_id)` (Layer 1) |
| Calendar | `cal_list_events(range)`, `cal_propose_times(participants, duration, range)` (Layer 1), `cal_create_event(...)` (Layer 3), `cal_update_event(...)` (Layer 3) |
| Jira | `jira_list_issues(filter)`, `jira_get_issue(key)`, `jira_create_issue(...)` (Layer 3), `jira_transition_issue(key, status)` (Layer 3), `jira_add_comment(key, text)` (Layer 3) |
| Gmail | `gmail_read_inbox(filter, limit)`, `gmail_summarize_thread(thread_id)`, `gmail_draft_reply(thread_id, text)` (Layer 3 — drafts only; sending stays manual for V1) |
| Granola | `granola_list_meetings(range)`, `granola_get_transcript(meeting_id)` (both Layer 1) |
| Telegram | `telegram_send_message(chat_id, text)` (Layer 3) |

### 7.5 OKR + Writing-Rules tools (already on main from Phase H prep)

| Tool | Layer |
|---|---|
| `list_okr_objectives(period?)`, `update_okr_kr(kr_id, progress)` | 1/3 |
| `list_writing_rules(profile?)`, `propose_writing_rule(profile, rule_type, title, body)` | 1/3 |

### 7.6 System / self-maintenance tools

| Tool | Layer |
|---|---|
| `health_check()` (read app status, recent errors) | 1 |
| `recent_failures(window)` | 1 |
| `propose_fix(component, description, diff?)` (Layer 4 — proposes; doesn't apply) | 4 |

## 8. Data model

```sql
-- Persistent conversation sessions
CREATE TABLE floating_artemis_sessions (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT UNIQUE NOT NULL,    -- uuid; client-side persistent across page loads
  owner_user_id BIGINT NULL,          -- multi-user reservation
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ NULL,
  title TEXT NULL,                    -- auto-derived from first user message
  metadata JSONB                      -- page context at session start, agent_id used, etc.
);

CREATE TABLE floating_artemis_messages (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES floating_artemis_sessions(session_id) ON DELETE CASCADE,
  role TEXT NOT NULL,                 -- user | assistant | tool_result
  content JSONB NOT NULL,             -- list of blocks: text / tool_use / tool_result
  cost_input_tokens BIGINT DEFAULT 0,
  cost_output_tokens BIGINT DEFAULT 0,
  cache_creation_input_tokens BIGINT DEFAULT 0,
  cache_read_input_tokens BIGINT DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fa_messages_session ON floating_artemis_messages(session_id, created_at);

-- Page-context awareness (matches the Node assistant-bot pattern)
CREATE TABLE floating_artemis_page_context (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES floating_artemis_sessions(session_id) ON DELETE CASCADE,
  page TEXT NOT NULL,                 -- e.g. "signals-inbox", "candidate", "agent-builder"
  ref_id TEXT NULL,                   -- e.g. signal_id when on a signal detail page
  set_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Voice corpus (per §6)
CREATE TABLE floating_artemis_voice_corpus (
  id BIGSERIAL PRIMARY KEY,
  owner_user_id BIGINT NULL,
  line TEXT NOT NULL,
  context_tag TEXT NULL,
  first_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ NULL,
  use_count INT NOT NULL DEFAULT 0,
  source TEXT NOT NULL,               -- 'seed' | 'observed' | 'operator_pinned'
  active BOOLEAN NOT NULL DEFAULT true
);

-- Active-run dashboard (matches the Node assistant-bot sidebar)
-- This is a VIEW over existing tables, not a new table:
CREATE OR REPLACE VIEW v_floating_artemis_active_runs AS
  SELECT 'agent_run' AS kind, run_id, agent_id AS target_id, status, started_at,
         owner_user_id, NULL::TEXT AS workflow_id, NULL::INT AS current_step
  FROM agent_runs WHERE status IN ('queued', 'running')
  UNION ALL
  SELECT 'workflow_run', run_id, workflow_id, status, started_at,
         owner_user_id, workflow_id, current_step
  FROM workflow_runs WHERE status IN ('queued', 'running');

-- Active approvals (for the "blocked" sidebar section)
-- Existing approvals table; she reads from it directly.
```

All tables carry `owner_user_id BIGINT NULL` — multi-user activation populates the field from the authenticated session; no schema migration needed.

## 9. Frontend — the floating panel (absorbing the Node assistant-bot.js inventory)

The Node `public/js/panels/assistant-bot.js` is 1487 lines and has battle-tested affordances. Inventoried here so we don't lose them:

### 9.1 Core affordances (carry over from Node)

- **Persistent floating panel** in the bottom-right with a FAB toggle. Collapse/expand state in localStorage.
- **Active-run count badge on the FAB** — populated by polling `v_floating_artemis_active_runs` (and the WS events as they arrive). Shows the count of running agents + workflows. Click → opens the panel with the active-runs section pre-expanded.
- **Sidebar surface** (separate from chat) showing: active runs, blocked approvals, recent failures. This is the *observability* face — the chat is the *conversation* face. Both are part of one panel; they're tabs or sections within it.
- **Chat surface** with streaming token rendering, markdown rendering (including list merging), tool-use cards rendered inline (name + input + result + status), [Run / Cancel] on side-effect cards.
- **Multi-session per page context.** Each page (project/signal/candidate/agent-builder) can have its own bot session, persisted. When the operator navigates to a different page, the panel shows that page's session — or opens a new one if none exists.
- **Page-context awareness.** She knows which page the operator is on (via the `floating_artemis_page_context` table). Her system prompt is augmented with "you are on the {page} page looking at {ref_id}" so her answers thread that context.
- **Image attachments.** Operator can drop an image into the chat input. Forwarded to her vision-capable Anthropic model.
- **Stop-generation button.** Visible while she's mid-turn. Click → cancels the streaming response.
- **Settings.** Customizable system prompt (her base persona is locked from the personality profile; this is for additional per-operator preferences). Model override. Voice toggle (deferred — Phase L+).
- **Observability-intent detection.** When the operator types something like *"what's running?"* or *"any failures today?"*, route to a direct system query instead of consuming an LLM turn. Faster + cheaper.
- **Dispatch handler.** When a running agent in builders dispatches a sub-task to her (via a special `request_assistant(text)` tool exposed inside agent runs), she handles it as a thread in the chat.
- **Empty state.** First-time onboarding when no messages — explains what she is, suggests a few starter prompts.
- **Approval cards inline.** When an action triggers a blocked approval (per layer 3/4), the card stays in chat with current status; updates live when the operator clicks Run or Cancel.

### 9.2 New affordances (V1 G additions)

- **Voice-rotation indicator** (subtle, for Lead debugging): a corner badge shows which voice samples were active in the current turn. Behind a debug flag.
- **Coaching mode chip.** When the operator says something like *"build me an agent..."*, the panel shows a small "Drafting agent..." status; the proposed agent appears as an editable card before Run.
- **Memory inspector.** A right-side tray that shows the memory observations she's reading on this turn — provenance for her answers. Toggle on/off.
- **"What can you do?" surface.** A button or `/help` command that surfaces her current tool registry, filtered by surface availability (so the personal-variant operator only sees their available tools, not marketing-OS).

### 9.3 Modules to ship in G2 frontend

```
public/js/
├── features/
│   └── floating_artemis.js     # session management, panel orchestration
├── components/
│   ├── floating-panel.js        # the panel custom element
│   ├── chat-stream.js           # streaming markdown renderer
│   ├── tool-use-card.js         # confirm/run cards for layers 3+4
│   ├── active-runs-sidebar.js   # observability surface
│   └── memory-inspector.js      # right-side provenance tray
├── core/
│   └── floating-artemis-api.js  # client for /api/floating-artemis/* + WS subscription
public/css/panels/
└── floating-artemis.css         # styled per Phase K design tokens (when those land)
```

The panel is gated behind `/api/_status` → `_AVAILABLE_SURFACES = {..., "floating-artemis"}`. When the backend isn't deployed, the panel hides.

## 10. Conversation loop

Each user turn:

1. Frontend `POST /api/floating-artemis/sessions/{session_id}/messages` with the user's text (+ optional attachments + page context).
2. Backend stores the user message row.
3. Backend builds the prompt:
   - System prompt = persona distillation (§6.1) + voice samples (§6.3) + current page context + surface-availability summary
   - Message history from `floating_artemis_messages` (token-budgeted, with summarization for long sessions)
   - Memory injection: top-K observations from her scope union (`agent:floating-artemis`, `workspace:default`, `global:global`) — relevant to the user's question
4. Backend constructs her tool registry filtered by `/api/_status` (only tools whose surfaces are live).
5. Backend invokes `run_turn` (F1 agent loop) with the tools, history, system prompt, and the E2 hook bundle that streams events.
6. Agent loop may iterate (model → tool_use → tool_result → model). Each tool call is validated against the authority layer (§5) — Layer 3+4 emit a `tool_use` block that the frontend renders as a confirmation card; only on operator click does the backend actually invoke the tool.
7. Final assistant message stored as a row. Any new observed voice lines flagged into `voice_corpus`.
8. WS event `floating_artemis.turn_complete` fires.

## 11. Routes

```
POST   /api/floating-artemis/sessions                              create new session
GET    /api/floating-artemis/sessions                              list sessions for current user
GET    /api/floating-artemis/sessions/{session_id}                 session metadata + recent messages
PATCH  /api/floating-artemis/sessions/{session_id}                 update title / metadata / page context
DELETE /api/floating-artemis/sessions/{session_id}                 close session

POST   /api/floating-artemis/sessions/{session_id}/messages        post a user turn (returns the AsyncTask for streaming)
GET    /api/floating-artemis/sessions/{session_id}/messages        list messages (paginated)

POST   /api/floating-artemis/sessions/{session_id}/page-context    set the current page context
POST   /api/floating-artemis/sessions/{session_id}/tool-confirm    confirm a pending layer-3/4 tool_use (with operator's [Run] decision)
POST   /api/floating-artemis/sessions/{session_id}/stop            cancel in-flight turn

WS     /ws/floating-artemis/{session_id}                           live event stream
```

All apply the standard auth dependency.

## 12. Personal-variant compatibility

The packagable personal version (Phase L) is the SAME app with feature flags. Concretely:

- `ARTEMIS_DEPLOYMENT_MODE` env: `"marketing"` (default) or `"personal"`.
- In personal mode, the marketing-OS routers + scout schedulers don't mount.
- `/api/_status` reports `marketing-os` surfaces as `unavailable`.
- The frontend gates accordingly (E1b pattern).
- The Floating Artemis tool registry auto-filters at runtime — tools whose surface is `unavailable` don't get exposed to the model.
- The personality profile is identical — she's the same Artemis in both contexts.

**OKR sync-back** (also Phase L): the personal instance writes OKR observations to its local keystone. A small daily sync job exports `okr:*` observations via the existing memory archive format and POSTs to the main marketing instance's import endpoint. The main instance's keystone receives the personal OKR data scoped to that user. No new tables; uses keystone export/import.

## 13. Slice plan

### G1 — Backend (one Sonnet sub-agent slice)

- Alembic migration: 3 new tables (`floating_artemis_sessions`, `floating_artemis_messages`, `floating_artemis_voice_corpus`) + the view + the page-context table.
- `artemis/floating_artemis/{__init__,agent,session,chat,tools/*}.py`.
- Authority layer enforcement (Layer 1/2 auto, Layer 3/4 emit pending tool_use, `/tool-confirm` route to release).
- Voice corpus seeding from the personality profile + sampling logic.
- Routes per §11.
- Hook wiring to E2 (broadcasts on `/ws/floating-artemis/{session_id}`).
- ≥60 tests using FakeAdapter.

### G2 — Frontend (one Sonnet sub-agent slice)

- Floating panel custom element, FAB, tabs (chat / runs / approvals / memory inspector).
- Streaming token renderer + markdown + tool-use cards + image drop.
- WS subscription via the E2 endpoint.
- Page-context awareness (auto-set on navigation).
- Observability-intent shortcut (regex routing for "what's running?" type queries).
- Coaching-mode card rendering for `propose_*` tools.
- Settings modal (per-operator system prompt suffix, model override).
- Empty state + onboarding starter prompts.
- Smoke tests.

### G3 — Proactive mode (deferred)

APScheduler tick → surface checks → `floating_artemis.proactive` events pushed to all connected operator panels. Findings render as unread badges. **Defer until V1 is in real use** so we know what's worth surfacing.

## 13a-pre. Propose vs Spawn — two modes of creation, never confused (Jon, 2026-05-17)

**Critical distinction.** Without firm enforcement, she'd muddle them and pollute `/agents` with throwaway entries OR underuse the helper pattern because everything looks like creation.

|  | **PROPOSE** (build) | **SPAWN** (do) |
|---|---|---|
| What it creates | Persistent artifact in `agents` / `workflows` / `skills` / `agent_chains` / `agent_dags` | Ephemeral helper — runs, returns result, disappears |
| Visibility | Shows in builders surface pages (`/agents`, `/workflows`, etc.) | Surfaces only the result; no entry in builders |
| Reusable | Yes — runnable again | No — one-shot |
| When she uses it | Operator will use this again (daily, on-demand, scheduled) | One-time task this turn (write a tool's code, audit a signal, summarize PDFs, generate fixtures) |
| Authority layer | Layer 3 (save creates real artifact); operator may opt for Save+Run | Layer 3 for Sonnet/Opus spawns (cost); Layer 2 for cheap bounded Haiku |
| Card visual | Artifact preview + `[Save]` / `[Save & Run]` / `[Cancel]` | Task + cost estimate + `[Run]` / `[Cancel]`; after Run streams inline; result inline |
| Persistence in DB | New row in the relevant builders table | `agent_runs` row with `is_ephemeral=True` and `agent_id='_floating-artemis-helper'` (so the agents-page UI filters it out) |

**Test the operator can use to verify she's not confusing them:** if the result of the operation should still exist tomorrow as a thing in `/agents`, it was a propose. If today's result is the whole point and there's no tomorrow-artifact, it was a spawn.

### The spawn_subagent tool

```python
async def spawn_subagent(
    task: str,                          # what to do
    model: str = "claude-haiku-4-5",    # default cheap
    tools: list[str] | None = None,     # subset of Artemis's tools the helper gets
    max_iterations: int = 5,
) -> str:
    """Spawn a one-shot helper sub-agent. Returns result text.
    NOT a builders artifact — no row in /agents."""
```

Under the hood: calls F1 `run_turn` with a temporary message list and a system prompt scoped to the task. No `agents` row. `agent_runs` row created with `is_ephemeral=True` for cost tracking and runs-history visibility.

Migration `0010` adds `is_ephemeral BOOL NOT NULL DEFAULT FALSE` to `agent_runs`.

### Common cases — verbalized so she internalizes the distinction

- *"Build me a calendar brief agent"* → PROPOSE the agent.
- *"Write the implementation of the cal_list_events tool"* → SPAWN a sub-agent with the task "implement cal_list_events following the existing scout pattern"; surface the resulting code as a `propose_edit` card (the code is what saves to disk, not a new agent).
- *"Audit the qualification on signal #42"* → SPAWN. Result is inline.
- *"Summarize these 12 board minutes for me"* → SPAWN. Result is inline.
- *"Generate test fixtures for the agent I just saved"* → SPAWN. Result is inline (probably a propose_edit too if the tests should land on disk).

### System prompt teaching (load-bearing — goes in her system prompt verbatim)

> **Two modes of creation. Don't confuse them.**
>
> **PROPOSE** when you're building something the operator will use again — an agent, workflow, skill, chain, DAG, tool, ruleset. The artifact is the point. It saves to the builders surface and lives there. Operator confirms.
>
> **SPAWN** when you're doing something once — write code, audit a thing, generate a summary, scaffold a fix. The work is the point; the helper is incidental. Result comes back; helper disappears.
>
> Test: if you'd want it in `/agents` tomorrow, it's a propose. If it's "do this for me right now," it's a spawn. Don't create a permanent agent for a one-shot task.

## 13a. UX resolutions (Jon, 2026-05-17)

Three UX questions resolved before G2 ships:

**First-run calibration:** Position B — on first turn she reads project log + memory + personality profile + available surfaces, shows 1-2 visible loading steps ("Reading project log…" / "Catching up on memory…"), then opens with something specifically grounded in what she found. Templated greetings are out. If B doesn't land in real use, fall back to C (no intro, just waits). G2 ships B as default.

**Coaching mode card UX:**
- Card actions: `[Save]` (creates in F2a, doesn't run) | `[Save & Run]` (creates AND fires F2b) | `[Cancel]`. Plus inline editing always available before any action.
- After save, card collapses to a single chat line: `✓ Saved agent "X" → view in /agents`. Operator can expand. One click links to the builders surface.
- Missing-tool autonomy: dependency chain proposal (§Coaching V1 above), one confirmation card for the whole chain.
- The builders pages (`/agents`, `/workflows`, etc.) live-update on `builders.*.created` WS events. Operator's UI reflects her creations immediately.

**Session lifecycle:** Position C — one persistent session per operator, page-aware. The Node app has the plumbing (`getCurrentPageContext`); G2 makes it actually actionable:
- Operator on signal #42 asks "what's the score?" → answers about #42, doesn't ask "which one?"
- Operator on agent-builder for `bug-hunter` asks "what tools does it use?" → answers about `bug-hunter`.
- Operator navigates mid-conversation → she follows: *"OK now you're looking at candidate #7…"*

Never auto-close. **"Start fresh"** button archives the current session (recoverable later in a session-history view we'll build) and starts a new one. Important — without this, context windows balloon. **"Start fresh" is not delete; it's archive + reset.**

## 13b-pre. Proactive behaviors — what she does on her own (Jon, 2026-05-17)

**Core posture: draft, never send. Until trust is built, she proposes; the operator confirms.** Every "send" / "schedule" / "post" / "create" tool starts at Layer 3. As trust accumulates, individual tools can flip to Layer 2 (she does silently, surfaces after) via per-tool config.

Specific proactive behaviors Jon wants in the personal-assistant context (Phase J integrations + later proactive mode G3):

### Slack — drafts, never auto-replies
- Watches for missed messages + @-mentions
- Drafts a context-aware response (queries memory for the relevant thread / project / person)
- Surfaces as a card: *"Maria asked about the brand voice update. Drafted a reply — check it before I send?"* `[Send]` `[Edit]` `[Discard]`
- Never sends without an explicit click in V1

### Daily contractor check-in — automation with varied greetings
- Runs on a schedule (workflow template + cron)
- The "varied greeting" problem maps onto the voice corpus mechanism — extend with a **greetings corpus**:
  - Generic greetings (good morning / hey / quick check-in)
  - Per-recipient flavor (some people prefer formal; some casual)
  - Per-context (Monday morning ≠ Friday afternoon)
- Same weighted-sampling pattern as her own voice corpus — don't repeat last week's
- The check-in message is drafted; operator approves; sends on click

### Unprompted ticket follow-ups
- Watches Jira tickets; notices when one's been quiet for N days
- Surfaces *"David's been on TICKET-1234 for 5 days without an update. Want me to ping him?"*
- Draft + send card pattern

### Post-meeting summary + action items (the primary meeting flow)
- Granola transcript drops → workflow fires
- She summarizes + extracts action items
- Cross-references against memory (who's mentioned, what project, what assets)
- Surfaces a checklist where each action item has a **proposed next move**:
  - *"Schedule follow-up with Sarah → proposed: Tuesday 2pm — best slot in both calendars."* `[Send invite]` `[Pick different]` `[Skip]`
  - *"Draft email to vendor about timeline → propose draft?"* `[Draft]` `[Skip]`
  - *"Create Jira ticket for the bug Sarah mentioned → propose ticket?"* `[Create]` `[Edit]` `[Skip]`
- **Pre-meeting prep is de-emphasized** — the Operations tab's meeting surface flips post-meeting-primary, pre-meeting-subtle (pre-meeting is a small "context for your 2pm" widget, not a featured surface). UX flip lands in Phase K3 restyle of the Operations tab.

### Meeting scheduling — autonomous compute, single confirmation
- Reading calendars + finding best slot = Layer 1+2 (silent)
- Sending the invite = Layer 3 (confirmation)
- She pre-computes the answer; the card shows the recommendation + one decision moment

### Drafting contextual content (e.g., "I need an emotion photography guide")
- She queries memory for context (prior similar work, writing rules, related content_assets)
- If she has enough context: drafts in Writing Studio, surfaces for iteration
- If she doesn't: proposes a direction OR asks clarifying questions (sharper than guessing per the personality profile)
- Once direction's locked: drafts in Writing Studio; on approval, can push to Google Docs (Phase J Google integration)

### Email management for COO (Jon's COO use case — future)
**Not designed yet.** Different shape — inbox triage + lost-email recovery + sorting + maybe auto-archive. Needs a dedicated design conversation when we get to it. Probably "Mail Triage" workflow + dedicated inbox UI. **NOT V1 G.**

### Comms channel for proactive surfaces
Jon doesn't want Telegram (too much spam/bots). **V1 path: Slack-self.** Create a `#artemis` channel just for her communications with the operator. Persistent history, threading, rich formatting — zero new infrastructure. For Phase L personal variant: iMessage as a stretch (Mac-only via AppleScript). Custom messaging app deferred unless real-use friction demands it.

### Workflow templates these enable (ship in Phase J integration slices)
- `post_meeting_review` — Granola transcript → summary → action items → proposed follow-ups
- `daily_contractor_checkin` — cron → draft varied greeting → propose send
- `stale_ticket_followup` — daily scan of Jira → identify stalled tickets → draft check-ins
- `slack_mention_draft` — webhook on @-mention → context query → draft reply
- `schedule_meeting` — operator intent → calendar lookup → best-slot recommendation → propose invite

Each is a workflow row in `workflows` table (Phase F2a). She runs them via F2b. Operator gets the confirmation cards in the floating panel.

## 13b. UI design discipline (applies to all G2 work)

Per Jon's permanent design language (saved to memory 2026-05-17): **fluidity, simplicity, purposefulness, naturalness, spacious, open.** Concrete rules for G2:

1. **One thing visible by default.** Chat is default. Sidebar (active runs, blocked approvals), memory inspector, voice-debug indicator — all collapsed.
2. **No stacking pending interactions.** A pending Layer-3/4 confirmation blocks proposal of more actions until resolved.
3. **Auto-collapse stale cards.** If 3 coaching cards in one conversation, older two collapse to one-liners automatically.
4. **Visual hierarchy by importance.** Streaming response > pending operator action > recent cards > old cards > debug indicators.
5. **Resist density.** Every button must earn its space. Phase K design tokens commit to spacious whitespace.

This is the floor (UI not busy) AND the ceiling (spacious feels natural). Both required.

## 14. Open questions for Jon — RESOLVED

(Original 6 questions, with current state)

1. **Default model:** ✅ Sonnet 4.6 with `ARTEMIS_FLOATING_MODEL` env override.
2. **Confirmation UX:** ✅ Four-layer authority model (§5).
3. **Persona / voice:** ✅ Personality profile is canonical; voice corpus mechanism prevents repetition (§6).
4. **Memory privacy:** ✅ Her observations are readable in other agents' retrieval unions (recommendation accepted).
5. **DB row vs hardcoded:** ✅ Hybrid — DB row for system prompt (editable per operator); custom tool registry (code-controlled for safety).
6. **File access scope:** ✅ Repo root only for V1.

All six are settled. Ready to ship G1.

## 15. What "done" looks like for G1+G2

- Open the app in a browser. Floating panel appears in the bottom-right with FAB.
- Click → panel opens with chat tab. Type a message. Streaming response.
- Ask *"what's running?"* — observability intent fires, returns the active-runs sidebar populated.
- Ask *"build me an agent that summarizes my Jira tickets each morning"* (when Phase J Jira lands) — she drafts the agent definition, surfaces as a card. Click Run → agent is created via F2a CRUD.
- Refresh the page. Panel rehydrates the conversation from persistent session.
- Quote a voice sample — she varies the phrasing, doesn't quote it back verbatim.
- Suite passes: ≥60 backend tests (G1) + smoke tests (G2).

After G1+G2 land, deploy. Use it for two weeks. Tune the voice corpus, the authority layer thresholds, the proactive triggers — then G3.

---

## 16. Dependencies on other phases

- ✅ F1 agent loop (on main)
- ✅ F2a builders CRUD (on main)
- ✅ F2b execution (on main)
- ✅ B1-B4 keystone (on main)
- ✅ E2 WebSocket relay (on main)
- 🟡 Phase J integrations (Slack/Cal/Jira/Gmail) — gated by `/api/_status`; G works without them but her tool surface is narrower
- 🟡 Phase K UI polish — G2 ships with the existing CSS; gets restyled in K when design tokens land

---

**This design is locked. Spawn G1 backend brief next.**
