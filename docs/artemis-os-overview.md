# Artemis OS — Overview

**Author:** Jon Fila
**Date:** May 2026
**Audience:** Amira Learning leadership

---

## What Artemis OS is

Artemis OS is a general-purpose agent operations platform built inside Amira Learning. Marketing intelligence is its first proving ground — the first vertical where we've demonstrated that specialized AI agents can do real work, learn from their own runs, and propose their own improvements to a human reviewer. The underlying substrate is not marketing-specific. The same machinery runs any team's workflow.

Today the platform produces qualified marketing signals — board minutes, federal funding opportunities, state DoE activity, legislative movement, leadership transitions, procurement, regional news, LinkedIn observations, Starbridge research, cross-referenced contacts, and composed briefs — through a fleet of purpose-built agents. Tomorrow the same platform powers Sales-side surfaces (Salesforce, ChurnZero, Gong integration), company-wide OKR tracking, and per-employee personal workspaces.

The core idea is simple. **Agents do the work. Agents learn from their work. Humans approve the changes agents propose to themselves.** Operators shift from doing the work to reviewing what should be done next.

---

## How it works (executive view)

Three things make Artemis OS work as a platform, not just a collection of scripts.

### 1. A pipeline of specialized agents

Work moves through tiers of agents, each with a defined role and a small, well-bounded toolkit.

- **Scouts** look for signals in their domain. One scout watches board minutes; another watches federal funding announcements; another reads regional news; another monitors LinkedIn for leadership moves at target districts. Each scout has a persona, a purpose, voice notes, and explicit success and failure modes — not generic prompts.
- **Qualifiers** take the raw signals scouts produce and score them against a single, editable specification of what makes a signal worth pursuing. This spec lives in one place; every qualifier picks it up at runtime. Change the criteria once; the entire fleet adapts.
- **Content composers** turn qualified signals into outreach drafts — briefs, summaries, comparison material.

The pipeline is durable. Runs are locked so two copies of the same pipeline can't collide. Signals are deduplicated. Tool calls are logged. Every signal carries provenance back to the agent run that produced it.

### 2. A memory and provenance layer

Every agent run produces a trajectory summary — a diagnostic record of what the agent tried, what worked, what stalled, what was missing. Every tool call the agent made is logged. Every signal carries a chain of evidence back to the run it came from. Nothing is invisible.

This isn't logging for debugging. It's the raw material the agents themselves read to improve.

### 3. A self-improvement loop

This is the part that distinguishes Artemis OS from a typical agent stack.

When an operator opens the Agent Builder for a specific agent, the Builder reads that agent's recent trajectory summaries, notices patterns ("the federal funding scout keeps missing district matches when district names contain abbreviations"), and proposes specific changes to the agent's definition — a tool addition, a prompt refinement, a new sub-skill. The proposal lands in a review queue with citations back to the runs that motivated it. The operator approves, and the change is applied to the live agent. The operator rejects, and the rejection is captured as signal for the next round.

**Agents propose. Humans dispose.** The system is explicitly designed so that no agent can modify itself without human approval. Self-improvement is fast — but it is gated.

---

## Where Artemis OS stands today (MVP)

The MVP is operational. Eleven marketing agents are running in production, organized into the three tiers above. The Builder surface for reviewing and editing those agents works. The Proposals Inbox — a cross-agent discovery surface where operators see "here's what needs your attention" — landed this week.

What the MVP demonstrates:

- The pipeline produces real qualified signals end-to-end, on real claude-code subscription authentication. No per-token API cost.
- The self-improvement loop fires end-to-end: agents read their summaries, propose changes, operators approve through a UI.
- The substrate is domain-agnostic. The Builder doesn't know it's working on marketing agents. The same flow works for any agent in the system.

This is a working foundation, not a finished product. The current Marketing-team focus is deliberate — proving the loop on a single team before generalizing.

---

## Where we're going

### Near-term (closing the marketing vertical)

- **Signal Playbook UI** — a dedicated surface for editing Josh's qualification criteria directly, with the same approve/reject review treatment as agent proposals. The Marketing team gets a tool to evolve the criteria themselves without engineering involvement.
- **Writing Studio handoff** — the content-composer agents output approved briefs into the Writing Studio surface where final outbound work happens. The pipeline-to-Studio handoff is the remaining missing piece.
- **Responsiveness** — every AI-driven surface (Builder, Floating Artemis, Pipeline AI Panel) made as snappy as Claude Code itself. Engineering investment is planned and documented; fires when daily-use volume makes the current latency a friction.

### Mid-term (expanding past Marketing)

- **Sales integration.** Salesforce, ChurnZero, and Gong connectors that let agents bridge the marketing-to-sales handoff. Qualified signals become tracked accounts. Account activity flows back as memory. The Sales and Marketing teams operate against a shared, agent-maintained view of each opportunity.
- **Personal workspaces for the rest of the company.** Floating Artemis, Operations, and Dev Projects packaged as a personal-instance distribution any Amira employee can run on their own machine. Each instance is the employee's own agent fleet, their own memory, their own loop. The platform stops being "the marketing app" and becomes "everyone's agent OS."
- **OKR expansion.** The OKR Studio (currently personal-scope) widens to Marketing-team scope, then company scope. Agents help track, surface blockers, and propose adjustments.

### Long-term (the sky-is-the-limit layer)

The vision is that any business process inside Amira that benefits from "an agent that watches a thing, qualifies what it sees against a spec, and proposes a next step" can be modeled as a new tier in Artemis OS.

Concrete examples on the table:

- **Contact research.** Agents that build dossiers on identified contacts, freshen them on a cadence, and propose engagement strategies.
- **Document generation through to placement.** Agents that don't just *draft* briefs and social posts but *insert them into the right templates* for final human approval — closing the loop from idea to ready-to-publish artifact.
- **SEO and AI-search positioning.** Agents that monitor how Amira surfaces in traditional search and in AI-driven answer surfaces, and propose content adjustments.
- **Competitor monitoring.** Agents tracking competitor product announcements, hiring patterns, and content output, with weekly digests and proposed responses.
- **Website-side work.** Content updates, A/B test proposals, structured-data improvements — all flowing through the same propose-and-approve loop.

The constraint is not what kinds of agents can be built. The constraint is operator review bandwidth — which is exactly the constraint the self-improvement loop is designed to scale around. As agents get better at the work, the operator's role narrows to higher-judgment review.

---

## Why this approach (architectural principles worth stating)

Three principles are worth making explicit because they shape every decision in the platform.

**1. Subscription-only, no per-token API cost.** Every agent runs through the Claude Code subscription path. There is no Anthropic API key burning per-token cost in the background. The platform's marginal cost per run is bounded, not metered.

**2. Lossless memory.** Nothing is permanently deleted. Old observations are superseded, never destroyed. The trail back from any current decision to the evidence that produced it is preserved. This is what makes the self-improvement loop trustworthy — every proposed change has a verifiable evidentiary chain.

**3. Humans gate every meaningful change.** Agents never modify themselves, the spec, or external systems without an operator approving the change through a UI. Speed comes from agents *proposing* faster, not from removing the human.

---

## What I'd ask leadership for

To take Artemis OS from MVP to the platform described above, the highest-leverage moves over the next quarter are:

1. **A short, structured review window from Sales leadership** on the Salesforce / ChurnZero / Gong integration scope — to make sure the agent surface we build actually fits how Sales operates.
2. **A go-decision on personal-instance distribution** — packaging Floating Artemis + Operations + Dev Projects for one or two pilot employees outside Marketing to validate the personal-workspace shape.
3. **Recognition that the Marketing-team vertical is the MVP, not the destination.** Decisions about staffing, integration priorities, and roadmap weight should reflect that the substrate is intended to serve every team.

The work behind this is real, durable, and operating today. The opportunity is to point it at more of the business.

---

*Prepared for internal leadership review. Technical detail available on request — Lead and the engineering team can walk through any layer at the depth required.*
