# Agent + Slack Architecture — Artemis & Callie

**Status:** DESIGN ONLY (Jon-aligned 2026-06-10). Build when the timing's right — *after* the current
composer/campaign polish lands and the marketing system is stable enough to hand to a sub-agent. No build off
this doc yet.

**Why this exists:** The "Artemis" Slack identity is currently doing two incompatible jobs in one DM —
operational app notifications AND a conversational personal assistant — which reads as a confusing flood.
This doc defines the split that resolves it and lays the foundation for the personal-assistant vision
(the system intended to beat OpenClaw / Hermes Agent on *experience*, not just capability).

Related: `docs/marketing-intelligence-layer-design.md` (the signals/pipeline), and the parked
agent-architecture/governance notes. This doc is the umbrella.

---

## The model: a top-level assistant with domain sub-agents

**Artemis — top-level personal assistant + overseer.**
- Lives in Jon's **1:1 Slack DM**. Jon **chats with her directly there** — no need to open the app.
- Owns: personal content/workspace, app operational issues, upgrade ideas/suggestions, and **supervising the
  domain sub-agents** (Callie first).
- In Jon's **personal build of the app, marketing is hidden entirely** (see App Modes).

**Callie — marketing analyst (sub-agent; reports up to Artemis).**
- Full name **Calliope** (muse of eloquence), day-to-day **Callie**.
- Owns the marketing lane: turns the raw signal pipeline into strategy, drafts, and decisions.
- Is the agent/surface that gets **hidden in the personal build**.

**Tier-1 vs sub-agents.** Artemis and Callie are *characters* — each gets a **personality profile + a profile
image/avatar**, so they read as teammates in Slack. The execution sub-agents (the workers) stay anonymous.
This "named teammates" feel is part of the experience edge.

---

## Callie's purpose: the pipeline is the ticker, Callie is the analyst

The signals pipeline ALREADY auto-posts raw signals to Slack. Callie must NOT re-announce those (no parrot).
She speaks only when she has a **so-what**:

- **Synthesis, not announcements** — "a screen-time-policy cluster is forming (LAUSD + 3 others); here's the
  angle and a starter brief," not "signal #258 arrived."
- **Prioritization with reasoning** — "of this week's 40 signals, these 3 are worth your time, here's why."
- **Conversational + acts** (in the Campaigns channel) — "draft the LAUSD angle" / "what's our position on
  X" → she drafts/suggests/acts.
- **Lifecycle nudges** — "Campaign Y has 3 drafts pending review for 2 days," "this asset needs its proof
  pack before it ships."

**Channel re-scoping (so there's no double-posting):**
- `incoming signals` — the **raw ticker** (pipeline auto-posts; sales-team visibility; hot-only per the
  existing `slack-signal-routing` brief).
- `campaign signals` — **Callie's analyst channel**: synthesized, prioritized, actionable recommendations,
  conversational, with the Approve / View-in-Artemis affordances.
- `Marketing Campaigns` (existing) — document approvals **+** Callie's strategy/suggestions, chat-and-act.

> ⚠️ The queued `briefs/slack-signal-routing.md` (pipeline → channels) is the *ticker* half and is still
> valid — but DON'T build it standalone now; fold it into this architecture so the channel roles match
> Callie's analyst role.

---

## Slack surface map

| Surface | Who | Content |
|---|---|---|
| **Artemis DM** (1:1) | Artemis ↔ Jon | Chat directly; personal content; app ops issues; upgrade ideas. **No unprompted marketing.** Marketing reaches this DM only when Jon explicitly asks, or when Callie escalates a decision that genuinely needs Jon (never an ambient marketing feed/digest). |
| **`incoming signals`** | pipeline (ticker) | Raw hot signals; sales visibility; view-only. |
| **`campaign signals`** | Callie (analyst) | Synthesized recommendations + Approve/View + conversational. |
| **`Marketing Campaigns`** | Callie | Doc approvals + strategy/suggestions + chat-and-act. |

---

## Build sequence

1. **Artemis (personal assistant) first.** The self-contained, highest-value slice: a true conversational
   Artemis in the Slack DM.
2. **Callie (marketing agent) second.** Layer her under Artemis once Artemis's conversational infra exists.

---

## Architectural pieces to design/build

1. **Conversational-in-Slack infrastructure** *(the big new build).* Both agents must RECEIVE messages
   (Slack Events API + interactivity) and respond via an agent loop (read → LLM → act → reply), not just push
   notifications. This is the foundation of "chat with her in Slack." Two Slack apps/bots (Artemis, Callie),
   each with its own token + events endpoint. **Jon creates the Slack apps/tokens; we build the routing +
   loops.**
2. **Reporting / escalation rules.** Jon's rule (2026-06-10): Artemis **does not push marketing into his DM.**
   Her unprompted DM content is personal, app-health, and upgrades/improvements only. Marketing reaches the
   DM only by (a) Jon explicitly asking, or (b) Callie escalating a *decision that needs Jon* — never an
   ambient digest. Ongoing marketing conversation lives with Callie in the marketing channels. Artemis stays
   the orchestrator (she can reach marketing on request), she just doesn't volunteer it. Artemis gets removed
   from the marketing channels once Callie is live there.
3. **App modes (personal vs. full).** A build/role flag that hides the marketing nav, routes, AND Callie in
   the personal version. Personal = Artemis + personal workspace; full = + marketing + Callie.
4. **Action authority.** What each agent does autonomously vs. needs Jon's confirm. The existing approval
   gates (Gate-1 signal→campaign, Gate-2 draft→send) are the seed of this.
5. **Persona + avatar profiles** (see below).

---

## Persona profiles (to be fleshed out)

### Artemis
- **Role:** Top-level personal assistant + overseer. Strategic, calm, proactive, protective of Jon's time.
- **Voice:** Plain-English, decisive, one clear recommendation + worst-case framing (mirrors how Jon likes to
  be spoken to). Never a wall of jargon.
- **Avatar:** TODO (Jon to generate/approve an image).

### Callie (Calliope)
- **Role:** Marketing analyst/strategist. Turns signals into angles, drafts, and decisions. Eloquent,
  opinionated, sharp on positioning.
- **Voice:** Crisp marketing-strategist register; leads with the so-what; proposes angles, not just data.
- **Avatar:** TODO (Jon to generate/approve an image).

---

## Named Agent Standard (the rule)

There are **two tiers** of agent, and they are NOT built the same:

- **Named agents** (tier-1, characterized — Artemis, Callie, and any future ones). These are *teammates*.
  Every Named agent MUST have the full capability set:
  1. **Persona + avatar** — a personality profile and a profile image. They're characters.
  2. **Memory** — read/write to the keystone, scoped to their domain; they remember and learn.
  3. **Proactivity** — a commitments/scheduling tier; they follow up, not just react.
  4. **Agency behind the propose→confirm gate** — they can DO things in their tools, safely.
  5. **A Slack presence** appropriate to their role — reachable + able to notify.
  6. **Orchestration-awareness** — they know their place in the hierarchy (report up to Artemis /
     delegate to workers); bounded.
  7. **A defined domain + authority** — explicit scope, and what they do autonomously vs. needs confirm.
  8. **Self-improving (designed-in).** Built so their prompts/skills are discrete *evolvable units* with
     execution traces captured, so a gated self-evolution loop (GEPA-style: test-suite + LLM-judge + human-PR
     approval, never auto-commit) can tune them over time. The loop ships LAST (capstone — needs a running
     agent + trace history), but the foundations (trace capture, evolvable structure) are laid from the
     start. See `docs/artemis-pa-build-plan.md` P6.
- **Sub-agents / workers** (the execution agents). Ephemeral, faceless, single-task. NO persona, memory,
  proactivity, or Slack presence — they just execute and return.

**The agent builder must enforce this.** When the builder creates a NEW Named agent it should scaffold the
full standard (persona stub, memory scope, the capability set, the safety gate, Slack hookup), so Named
agents are consistent and don't have to be hand-assembled. Creating a worker stays lightweight. This rule is
what keeps the "named teammates feel" coherent as we add more characters.

## Open questions / decisions still to lock
- Escalation thresholds: exactly what Callie escalates to Artemis (and what Artemis brings to Jon's DM).
- Personal/full mode mechanics: feature flag vs. separate deployment vs. per-user role.
- Conversational loop runtime: where the agent loops run (reuse the existing app runtime vs. a dedicated
  service), and how they stay always-on (ties to the always-on Mac mini setup).
- Avatar images for Artemis + Callie.

---

*Prepared by Artemis (Opus Lead), 2026-06-10, from the design conversation with Jon. Living doc — update as
decisions lock. Build only when sequenced in.*
