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

## Callie credits Argus (conversational attribution — Jon, 2026-06-16)
When Argus's research lands, Callie surfaces it **in her voice, crediting him by name** ("Here's what Argus dug
up…", "I had Argus look into this and…") — makes the team feel human. Two pieces:
1. **Light addition to Callie's persona** (`callie-personality-profile.md`): establish Argus as *her
   researcher*, and the habit of naming him when she relays his findings. Voice/relationship layer.
2. **Dossier provenance:** each finding carries `source: "Argus"` so her attribution is grounded in truth, not
   flavor. The turn that surfaces the dossier includes that provenance so she can say "Argus found X" accurately.
Both land WITH the build (no point crediting him before he exists).

## Build plan + sequencing (foundation starting NOW; integration deferred for terminal)
Follows `docs/named-agent-build-playbook.md` but **headless** (skip the Slack app/row steps).

⚠️ **Collision reality:** terminal is *actively* in the marketing/signals/Callie lane right now (get_signal,
district resolver, signal backfill). Argus's INTEGRATION points live in that same territory → don't build those
concurrently. So:

**START NOW (isolated — new module, memory-based, NO migration, no terminal-file overlap):**
1. **District research drawer** — a per-district drawer over the EXISTING memory infra (marketing-shared scope,
   keyed by district id/name). NO new table → NO migration (dodges terminal's concurrent migrations). Define
   the observation shape (the researched dimensions + provenance + the synthesized angle) + read/write helpers.
2. **Argus module skeleton** (`artemis/argus/`) — the core flow: read-existing (signal/scout/drawer) → identify
   gaps → [research step] → write findings **through the memory pipeline** (dedup/conflict handled for us).
   Web/deep-research tool wired here.

**DEFER until terminal clears the marketing lane:**
3. **Dispatch hook** — a Callie tool (`dispatch_research(signal_or_district)`); true delegate primitive (P4)
   later, synchronous tool first. Touches Callie's tools (terminal's file).
4. **"Dig deeper" UI affordance** on top-tier qualified signals.
5. **Assembler integration** — brief/campaign assembler reads the district drawer (terminal's file).
6. **Callie persona credit** — the attribution addition above (needs Argus live to be meaningful).
7. **Learning** — register Argus with the skill-distiller so his research patterns compound.

**Dependencies:** P4 (shared with Ares) for true async delegation — steps 3 can start synchronous before P4.
Argus's drawer keys off the district record terminal's resolver produces (they compose, not collide).

## Open / to-decide later
- Persona depth for a headless agent (probably light).
- "Top-tier" threshold that surfaces the dig-deeper affordance.
- Whether/when Argus gets his own Slack presence (deferred; Callie-as-face for now).
