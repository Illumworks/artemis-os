# Artemis as the Team Hub — connective tissue + sole interrupt authority

**Status:** DESIGN (2026-06-16, with Jon; conversation ongoing). How Artemis sits across all named agents
(Ares, Callie, Kai, future) and connects their work to Jon's world. Companion: `docs/ares-plan.md`.

## Core idea
Agents **do not talk to each other** (no loops). Each works in its own space, talks to Jon there, and
**respects notification silence**. Artemis sits on top: she sees everything (all-scope + shared memory),
operationalizes it, follows through, and is the **only** agent allowed to break Jon's silence — on behalf of
whichever agent needs him, when it's genuinely worth it.

> The agent that needs you and the agent that can interrupt you are not the same. Only Artemis interrupts.

## The loop-proof rule (gives the "team" visual without the chaos)
Jon wants it to *look* like the agents respond to each other in Slack, but never loop. Rule:
- **No agent ever replies to another agent.** A bot post NEVER triggers another bot post.
- **Only Artemis** comments *about* another agent's work, and only on a trigger: (a) a **timeout** (Jon hasn't
  responded in ~1 day), (b) a concrete operational **so-what** (she turned it into a ticket / commitment /
  calendar item), or (c) Jon **@mentions** her.
- Artemis's comment is **terminal** — when she says "Ares, I've got this," Ares does NOT respond. Every chain
  ends at a human (Jon). Two bots can never ping-pong.
- Net: visually reads like a team responding to each other; mechanically cannot spiral.

## Worked example (Jon's)
1. **Ares** in #forge: "@Jon prototype built, I have implementation questions."
2. ~1 day, no reply.
3. **Artemis** in #forge: "Ares, I'll take this — escalating to Jon." + puts it in Jon's queue + pings Jon
   directly (Slack @mention / her DM / iMessage later). Ares stays quiet.

## Artemis's three jobs
1. **Operationalize** — turn agent outputs into tracked items: commitments, calendar holds, OKRs, Jira tickets.
2. **Follow through** — watch for stalls (Jon hasn't answered an agent in a day; a build's blocked; a deadline
   nears).
3. **Interrupt (her alone)** — the sole authority to pierce Jon's notification silence, above the urgency bar.

## Timing + interrupt bar (Jon, 2026-06-16 — CONFIRMED)
- **Wait window:** an agent waits **~1 day** for Jon; then Artemis steps in.
- **Silence-bypass bar:** external deadline / a real person waiting on Jon / production breaking / a commitment
  Jon made about to slip. Everything softer waits for the morning brief.
- **Single interrupt authority:** ONLY Artemis bypasses notification silence (DND / quiet hours); all other
  agents queue/respect it. Escalation channels: Slack @mention + her direct DM; **future iMessage = the
  "break-glass" hard-interrupt** (pierces a silenced laptop in a way Slack can't).

## Jira / ticket autonomy (Jon, 2026-06-16 — CONFIRMED)
- **Other people's tickets** → NOTIFY only; modify/update/change ONLY when Jon directs.
- **Jon's & Ares's project work, not yet in Jira** → **FULL AUTONOMY to create tickets** (management visibility
  without Jon's effort — the core win).
- **Work that may belong to an existing ticket** → **CHECK FIRST**: maps to a real ticket → PROPOSE adding info
  to it (no silent duplicate); no match → create a new one.
- **Confidence rule (CONFIRMED):** confident match → propose adding; unsure → ask, don't guess. **Jon confirms
  CONVERSATIONALLY** — by replying in plain language ("yeah" / "no, new one"), NOT buttons (he dislikes
  buttons). This applies to ALL Artemis proposals, not just tickets.
- Maps to autonomy levels: create-own-tracking = L1 (act); touch others' / existing canonical tickets = L2
  (propose/ask). **Needs a Jira WRITE integration** (she currently only reads/notifies).

## Notification routing (Jon, 2026-06-16 — CONFIRMED)
No separate "Needs You" surface. Route by **who's asking**, three tiers:
- **Artemis → DMs Jon directly.** Her DM is the live voice + the escalation/interrupt path (she's the sole
  silence-bypass authority).
- **Ares → his own pings** (his 1:1 DM + #forge). The priority build partner has a direct line; he still
  respects silence, and Artemis escalates *for* him after the ~1-day wait if needed.
- **Callie / Kai / everyone else → grouped into ONE notification in the morning brief.** Batched,
  non-interrupting.

## Confirmation style (Jon, 2026-06-16 — CONFIRMED)
Jon confirms by **writing back to Artemis in plain language**, NOT clicking buttons. Lean away from buttons
anywhere Artemis asks Jon something. (See [[preference-conversational-confirmation]].)

## Status
Design settled (2026-06-16). Open questions resolved: notification routing decided above; ticket-match =
propose-when-confident + conversational confirm. Ready to build when Jon gives the word.

## Generalizes
Same hub pattern across ALL agents (Callie, Kai, Ares) — Artemis as the genuine connective tissue of the team.
(Jon: "loveeee the idea.")
