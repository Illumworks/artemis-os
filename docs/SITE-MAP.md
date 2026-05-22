# Artemis OS — Site Map / Information Architecture

**Status:** living document. Update when navigation structure changes.
**Last updated:** 2026-05-22

This document captures the app's information architecture so future sessions (Claude, Codex, new operators) know where things are without guessing. The left rail is the primary navigation; the account popup and top-bar host secondary surfaces.

---

## Left Rail (primary navigation)

The left rail is collapsible and visible on every page. Items are grouped by category.

### Personal Workspace
*Daily operator surface — your inbox + scheduling.*

| Item | What it does | Key data |
|---|---|---|
| **Focus** | Daily brief + Slack signals + calendar/meetings/OKR pulls. Single-page summary for "what should I focus on?" | `daily_briefs`, `slack_signals`, `meetings`, `okr` |
| **Calendar** | Day/week/month calendar view backed by Google Calendar OAuth. | `calendar_events` (synced from GCal) |
| **Meetings** | Granola integration. Auto-summaries, action items, attendee lookup. | `meetings` + Granola API |
| **Jira Board** | Read-only Jira board view. Shows assigned tickets, recent activity. | Jira REST API |
| **OKR Studio** | Quarterly OKR composition + tracking. | `okr_objectives`, `okr_key_results` |

### Operations
*Infrastructure tier — agents, skills, pipelines, memory. Where you BUILD the system that does the work.*

| Item | What it does | Key data |
|---|---|---|
| **Automations** | Legacy registry of scheduled triggers. **Sunsetting in PIPE6** — automations become Pipelines with trigger nodes. | `automations`, `automation_runs` (OP1) |
| **Skills** | User-authored capability bundles (markdown + tool list + kind). Attachable to agents. | `skills`, `agent_skills` join |
| **Pipelines** | The unified orchestration primitive (D6). Visual canvas + AI assistant panel. Replaces Workflows/Chains/DAGs/Automations long-term. | `pipelines`, `pipeline_runs` (PIPE1+PIPE2+PIPE3+PIPE5) |
| **Agents** | Roster of LLM agents. Tree view (Slug / Custom) + Operating Blueprint + Persona + Linked Connectors. | `agents`, `agent_runs`, `agent_skills`, `agent_connectors` |
| **Workflows** | Legacy sequential recipes. **Sunsetting in PIPE6** — workflows become Pipelines with sequential edges. | `workflows`, `workflow_runs` |
| **Memory** | Memory layer surface (raw_inputs, observations, conflicts). UI HTTP routes partial; full surface is queued. | `raw_inputs`, `observations`, `entities`, `memory_conflicts` (Memory-M2) |

### Marketing
*Domain tier — consumer of the Operations primitives. Marketing-specific surfaces driven by the marketing pipeline.*

| Item | What it does | Key data |
|---|---|---|
| **Dashboard** | Marketing overview. Tiles for active campaigns, pending signals, approval queue depth. | Aggregates from below |
| **Writing Studio** | Draft composition + ruleset editing. Externally team-owned (Angela / Olivia / Julie). | `writing_studio_drafts`, `writing_rules` |
| **Campaigns** | Active + historical campaign workspaces. | `campaign_workspaces`, `campaign_deliverables` (M3 state machine) |
| **Signals Inbox** | Gate 1 — Josh/Angela review qualified signals + brief composer output. Tree view (5 grouping modes) per OPS-UI-3. | `signal_queue`, `signal_briefs` |
| **Approval Queue** | Gate 2 — review of finished drafts before Writing Studio releases. | `approvals` (kind=content_draft) |

### Dev Projects
*Per-project workspace for development tasks.*

- **Select a project** dropdown (current: `vanilla-portal`, etc.)
- Each project surface has chat sessions, files, agent integration

### Bottom of left rail
- **User profile** (avatar + name + role badge) — clickable → opens account popup

---

## Account Popup (top of profile click)

Accessible from the user profile button at the bottom-left of the rail.

| Section | What's here |
|---|---|
| **User identity** | Name, email, role badge (e.g., "PERSONAL · PRO") |
| **Connections** ← THIS IS WHERE INTEGRATIONS LIVE | OAuth integrations (Slack, Google Calendar, Granola, Notion, etc.) + API Connectors (Starbridge, OpenAI, Anthropic, Gemini, Tavily — credentials per source kind) per Connectors brief |
| **Settings** | App preferences (theme, view defaults, etc.) |
| **About** | Version info, links to docs |
| **Sign out** | Session termination |

**Why "Connections" not "Integrations under Operations":** integrations are user/account-scoped credentials (e.g., Jon's Slack OAuth, Jon's API keys). They belong with the user identity, not with the orchestration tier. Operations primitives REFERENCE connections via Agent's `Linked Connectors` section.

---

## Top Bar (secondary navigation)

| Element | What it does |
|---|---|
| **Theme toggle** | Light / Dark / system preference |
| **Floating Artemis (right side)** | Persistent AI assistant for the WHOLE workspace (not pipeline-scoped). Different surface from the Pipeline AI Assistant Panel. |

---

## Right-Side Floating Surfaces (varies per page)

| Page | Right-side surface |
|---|---|
| **Pipelines / Marketing Pipeline canvas** | Pipeline AI Assistant Panel (PIPE3+) — proposes node/edge changes for THIS pipeline |
| **Agents** | Agent detail panel — persona, blueprint, linked connectors, reason codes, recent runs |
| **Other surfaces** | None or context-specific |

---

## What's NOT in the rail (cross-cutting surfaces)

These surfaces don't get their own rail item; they appear via:

- **Floating Artemis chat** — persistent AI; click the floating button (top-right or similar)
- **Notifications** — bell icon (when implemented)
- **Search** — keyboard shortcut (when implemented)
- **Help / Docs** — possibly in About in account popup

---

## Where data flows

Useful mental model when debugging:

```
Personal Workspace surfaces ← Read from: meetings, daily_briefs, slack_signals, etc.
                              Write via: Floating Artemis, manual UI

Operations surfaces ← Read from: agents, pipelines, skills, memory tables
                       Write via: Agent Card editor, Pipeline canvas, Pipeline AI panel

Marketing surfaces ← Read from: signal_queue, campaign_workspaces, etc.
                      Write via: Pipeline runs (scout agents fire signals; qualifier qualifies; gates approve)

Connections (account popup) ← Read from: connectors (encrypted credentials)
                               Used by: any agent invocation needing external API access
```

---

## Where things are NOT (common misunderstandings to flag)

- **Integrations / Connectors are NOT under Operations.** They're in the account popup → Connections.
- **Approval Queue and Signals Inbox are different gates.** Signals Inbox = Gate 1 (signal-level approval). Approval Queue = Gate 2 (draft-level approval).
- **The Pipeline AI Assistant is NOT the Floating Artemis.** AI Assistant is pipeline-scoped (sidebar inside canvas). Floating Artemis is workspace-scoped (persistent across pages).
- **Pipelines REPLACE Workflows/Automations long-term.** Both still appear in left rail during transition; PIPE6 sunsets them.
- **The "Builder" surface (Agent Builder) is for AGENTS only.** Pipelines have their own inline AI Assistant (different mental model).

---

## How to update this doc

When the left rail / account popup / right-side surfaces change:

1. Add or remove the row from the appropriate table
2. Note which brief / commit introduced the change
3. Update "Where data flows" if backend data sources change
4. Update "Where things are NOT" if a common misunderstanding got resolved

Living doc. Treat it as authoritative; correct it when reality changes.
