# Artemis agent architecture + governance (design — for an AI-maintained app)

Captured 2026-06-06 (Jon). Four asks that cohere into ONE model: **Artemis as conductor → a fleet of
specialized agents → a shared context substrate → governance baked into the flow.** This is the foundation
for "AI maintains this app" sustainably. Roadmap (build after the WS/Campaigns MVP core); design carefully.

## The model
- **Artemis = orchestrator + interface.** She delegates to specialized agents, synthesizes their output, and
  talks to Jon. She should NOT personally do health-checks + bug-hunts + triage + drafting all at once
  (what the Slack episode showed). She conducts.
- **Specialized agents = the workers**, each with one job, reporting UP to Artemis: health agent, scout,
  content/writer, reviewer, etc.
- **Shared context substrate** (MCP) that every agent can query — so decisions are grounded, not blind.
- **Governance baked into the flow**, not left to any agent's discretion.

## 1. Governance baked into Slack approvals (so she can't go off the rails)
Enforce at the TOOL/FLOW level, not via prose Artemis "should" follow:
- Artemis's Slack tools **PROPOSE** (write a brief / open a branch), they do NOT apply code to main.
- A code-fix "approve" in Slack = **greenlight the work** → it becomes a reviewable artifact that flows
  through **review → verify → merge** (a context-aware reviewer + the test gate), never straight to disk.
- **Side-effecting actions** (send campaign, spend, external posts) require explicit human approval — she
  already does this well; keep it structural.
- **Match depth to stakes:** trivial/isolated/reversible = light; load-bearing/shared = full review.
- The reviewer gets full context from the MCP substrate (§4) — so "approve from Slack" is safe because the
  *landing* is gated by a reviewer who can see the design intent + prior decisions + principles.

## 2. Split the marketing Slack channels (now: everything in #marketing-campaigns = messy)
- **`#marketing-signals`** — signal triage + Gate-1 (the inbox/qualify/snooze/reject workflow).
- **`#marketing-campaigns`** (or `#marketing-content`) — campaign drafts + Gate-2 review/approve.
- **`#artemis-ops`** — system errors, bug findings, fix proposals, pipeline/health alerts + health-audit
  reports.
- **DMs = the PERSONAL workspace only** (Focus/Calendar/Meetings/OKR/daily brief) — needs the personal
  section fleshed out to be useful, its own effort.

## 3. A dedicated HEALTH agent → reports to Artemis (Jon: don't make her do all of it)
A scheduled specialized agent whose only job is system health: audit pipeline runs, errors, staleness
(model registry, stale data), latent bugs, broken invariants → **report findings to Artemis** → she surfaces
them + proposes fixes to `#artemis-ops` for greenlight → review → merge. This is the first of the
specialized-agent fleet and the concrete form of the **health + propose-upgrade schedule**. Separates
concerns: Artemis orchestrates, the health agent monitors.

## 4. Context MCP server — the keystone for AI-maintained governance (Jon's "robust/concrete" ask)
A **knowledge MCP server** serving the project's durable context, queryable by any agent:
- `docs/` (roadmap index, design docs, working principles, this file), `briefs/`, the decisions /
  PROJECT_LOG / COORDINATION, the memory files, and a codebase map.
- **Why it matters:** the reviewer/advisor reviewing a proposal (and Artemis proposing one) can consult the
  **design intent, prior decisions, and principles** — not just the diff. That's what makes AI-maintained
  governance robust instead of context-blind. We already HAVE this knowledge in the repo; the MCP just
  serves it queryably to the fleet.
- **"Artemis consults the advisor"** = she routes a proposal to a **context-aware reviewer agent** (Opus-
  class) that pulls full context from this MCP, OR queries it herself before proposing. The ephemeral
  reviewer is interchangeable; the durable CONTEXT is the asset.
- #1 (governance) + #4 (context) reinforce each other: the gate is only as good as the context the
  reviewer has.

## Sequencing
Roadmap — build after the WS/Campaigns MVP core is solid (this is sustainability infrastructure, not
MVP-blocking). Likely order: (a) channel split (cheap, immediate relief), (b) governance baked into
Artemis's Slack tools (propose-not-apply), (c) context MCP server (the keystone), (d) the health agent
(first specialized agent, reports to Artemis, uses the context MCP). Quick bugs from the Slack episode
(`docs/artemis-slack-findings-and-routing.md`) fixed independently, behind Codex's current WS phases.
