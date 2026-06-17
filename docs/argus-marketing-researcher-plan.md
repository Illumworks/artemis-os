# Argus — Marketing Research Agent (plan)

**Status:** PLANNING (2026-06-16, with Jon). Planning only — NOT built. Companions: `docs/ares-plan.md`
(shares the *framework*, separate agent), `docs/artemis-hub-plan.md`, `docs/named-agent-build-playbook.md`.

## What Argus is
A **dedicated, named marketing research agent** that digs deep on a specific district/signal and returns a
decision-ready dossier. Named for **Argus Panoptes**, the hundred-eyed giant who saw everything — nothing
about a district escapes him.

- **Headless to start** (no Slack bot, no profile pic). He works in the background; **Callie is the face**:
  *"Absolutely — I'll have Argus pull more on this and ping you when it lands."* Avoids multi-bot-in-channel
  noise. A Slack presence can come later if wanted.
- **Specialized + learns over time.** Marketing/district research is his lane; he accrues **skills + learned
  patterns** (which sources are reliable, what research actually moved campaigns) via the live skill-distiller
  (P5). He compounds — a generic tool wouldn't.
- **Marketing-scoped** (not owner-private like Ares): his dossiers live in marketing-shared scope so Callie +
  the marketing surface can use them.

## Relationship to the others
- **Callie dispatches Argus** and surfaces his results to Jon. Argus reports up to Callie.
- **Framework-shared with Ares, but SEPARATE.** Argus uses the same *dispatch/research primitive* Ares will
  use (the planned P4 delegate primitive), but he is NOT a piece of Ares. Ares = technical/build research,
  owner-private, not built. Argus = marketing/district-facing. Kept deliberately separate.

## Triggers (no auto-fire)
1. **"Dig deeper" affordance on top-tier qualified signals** — a button/notification on high-value qualified
   signal cards (marketing signals surface) that fires Argus for that signal's district. (This is an *action
   trigger*, distinct from the no-buttons rule, which is about *confirming* proposals — no conflict.)
2. **Callie summons him on demand** — "Callie, dig into #910" → Callie calls Argus. Initially a direct research
   tool Callie owns; graduates to true dispatched delegation as P4 firms up.

## What Argus researches
Beyond the basics (news, board minutes, competitor presence, funding, decision-makers):
- **Current vendor / curriculum** — what they just adopted (the complement-vs-replacement angle).
- **Procurement & fiscal timing** — RFP cycles, budget-approval calendar — *when's* the buying window.
- **District profile** — enrollment size, Title I / literacy-grant eligibility, reading-performance data
  (directly relevant to Amira's literacy pitch).
- **Decision-makers / org** — superintendent, curriculum director, the new appointees named in the signal.
- **Prior Amira relationship** — have we touched this district before? Don't cold-pitch a warm account.
- **A "so-what" synthesis** — not just facts: a recommended angle (à la Callie's "position as complement"),
  so the dossier is decision-ready.

## Dedup against existing data (the key constraint — Jon's call)
We already pull a lot (scouts, the signal, prior signals for the district). Argus must NOT re-store what we
have. Flow:
1. **Read first:** the triggering signal + its scout-captured fields, the existing **district drawer**, and
   related signals for that district.
2. **Research only the gaps** (what's unknown or stale) — cheaper, faster, no re-treading known ground.
3. **Write findings through the existing memory pipeline** — which already does **dedup + semantic conflict
   detection** (M1/M1b live, `apply_consolidation` is the live write path — NOT the dead
   `write_observation_with_conflict_check`; see [[feedback-verify-actual-call-path]]). So duplication is
   prevented *structurally* by the memory layer; Argus just writes observations with provenance (source + date)
   into the district drawer. The drawer becomes a **living, deduped district profile** that grows over time.

## Storage — the district memory drawer
A per-district memory drawer (keyed by district id/name), **marketing-shared scope**, built on the existing
M3 scoped-memory + drawer model (qualified signals already write to memory drawers, M5). Holds the researched
dimensions above, each with provenance + timestamp, plus the synthesized recommended angle. Linked back to the
signal that triggered the dig. Reusable across every future signal/touch for that district.

## How it shows up in a campaign
The **campaign/brief assembler already pulls the signal + its context** — extend it to also read the district
drawer. So when Callie or Jon initiates a campaign from that signal, the brief **automatically** carries the
competitor intel, decision-makers, procurement timing, and the recommended angle. **Research once → it flows
into every campaign for that district, no re-research.** That's the payoff.

## Build plan (DEFERRED — not now)
Follows `docs/named-agent-build-playbook.md` but **headless** (skip the Slack app/row steps):
1. **District-drawer schema** (the foundation — what a district profile holds; reuse memory infra).
2. **Argus's tool set** — read-existing (signal/scout/drawer) + web/deep research + write-to-drawer through the
   memory pipeline. Scope-gate to marketing.
3. **Dispatch hook** — a Callie tool (`dispatch_research(signal_or_district)`); the true delegate primitive (P4)
   when ready. Callie reports "Argus is on it / it landed."
4. **"Dig deeper" UI affordance** on top-tier qualified signals → fires Argus.
5. **Assembler integration** — brief/campaign assembler reads the district drawer.
6. **Learning** — register Argus with the skill-distiller so his research patterns compound.

**Dependencies:** the dispatch/delegate primitive (P4, shared with Ares) for true async delegation — but step
2–3 can start as a synchronous research tool Callie calls, before P4. Persona: TBD (a short profile like the
others; he's headless so voice matters less, but Callie references him by name).

## Open / to-decide later
- Persona depth for a headless agent (probably light).
- "Top-tier" threshold that surfaces the dig-deeper affordance.
- Whether/when Argus gets his own Slack presence (deferred; Callie-as-face for now).
