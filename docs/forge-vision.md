# Forge / Ares — Product Vision (living discussion doc)

> Status: DISCUSSION, started 2026-06-19. Not a spec yet. This captures the
> direction Jon and Claude landed on while re-examining whether to keep building
> on the old dev_projects ("reskinned Claudeck") shell or do something
> purpose-built. Companion to `docs/ares-architecture.md` (Brief 1-3 mechanics)
> and `docs/ares-plan.md`.

## The one-line

**Forge = a browser-based, always-on build control room for an execution engine
that lives on the Mac mini.** Fire off a build from any device's browser, it runs
server-side on the mini (which has the local project folders), survives you
closing the tab, and you reconnect to watch/approve from anywhere. Durable
project memory ("one brain"), OS integration, and cost-routing to cheaper
providers come for free because execution runs inside Jon's system.

## Why this, and not the claude.ai Code tab

Jon uses the claude.ai Code tab exclusively today and likes it. We are NOT trying
to beat it as a code editor. What it structurally cannot do, and what justifies
this build:

1. **Portability / multi-device.** Claude Code is tied to one machine; close the
   laptop and work stops. Forge is browser-based against an always-on server, so
   phone / iPad / any computer reach the same projects, memory, and in-flight
   builds.
2. **Long unattended builds.** The mini runs a 3-hour build; Jon walks away and
   checks from his phone later. The tab can't do that.
3. **Durable private project memory** (the "one brain" — no re-briefing).
4. **OS integration** — Ares wired into Jon's memory, Slack, calendar, agents.
5. **Cost** — route bulk work to Codex / local; only pays off if execution runs
   in Jon's system.

The differentiator is the always-on engine + memory + multi-device access +
orchestration, NOT the editing UX. Build the engine and a good
watch/review/approve surface; do not sink months into a code-editor UI Jon would
rarely type in (the agents type, not Jon — his own principle).

## The destination (Jon, 2026-06-19): the full Claude Code app, inside Artemis

The end goal is ambitious and explicit: **a full Claude Code experience living
inside Artemis** — main build pane AND the right-rail features (file tree, diff
viewer, plan/todo, etc.) on desktop. Used for everything: Jon's everyday projects
AND eventually maintaining Artemis itself. Not a stripped-down toy.

Encouraging reality: this is NOT from zero. The old dev_projects "shell" already
gives us UI scaffolding (file rail, session model, project files) — its engine
was the stub, not its UI. We extend that shell + add the real engine + the
review panels, rather than inventing Claude Code from scratch.

## Dual-track = phased ON-RAMP, not a permanent split

- **Near-term (on-ramp):** Artemis OS itself stays maintained via a claude.ai
  Claude Code session; the Forge engine proves itself on Jon's OTHER projects
  first. The engine does NOT have to be good enough to build Artemis on day one.
- **Destination:** Forge does everything, including maintaining Artemis. The
  dual-track is how we de-risk getting there, not where we stop.

## Why now (Jon, 2026-06-19)

Forge kept losing to other priorities NOT because it's low value — because the
rest of Artemis needed to stabilize first. That stabilization has happened, so
this is the right time to invest. (Correct sequencing, not neglect.)

## Parked thread — different use case + different person

Jon sees a separate use case for the execution engine, for a different person.
Documented here to revisit, not sequenced. See
[[project-parked-strategic-plans]]. (Mirrors the earlier parked directions:
standalone growth-credibility app + OS multi-team expansion.)

## Product shape (from Jon, 2026-06-19)

- **Full Claude Code experience on desktop — INCLUDING the right rail** (file
  tree, diff viewer, plan/todo panels). Jon wants the real thing, not a stripped
  toy. (Updated 2026-06-19: earlier note about dropping the right rail is
  superseded — that only applies to a later pared-down MOBILE view.)
- **Desktop browser first.** Artemis is not mobile-optimized today, so we do NOT
  build mobile-first out of the gate; a pared-down mobile view (which is where
  right-rail features get trimmed) comes later, only if/when beneficial.

## Decisions / requirements captured

- **Durable run model is the technical core:** a build is a server-side job that
  keeps running independent of the browser, with status/logs reconnectable from
  any device. (Reuse the app's existing run/job machinery — pipelines, agent_runs
  — don't reinvent.)
- **Server + tunnel hardening is IN SCOPE** (Jon confirmed). Always-on access is
  the whole promise; the mini's freeze bug + Cloudflare tunnel flakiness must be
  fixed first or alongside. See [[project-instability-freeze-trap]],
  [[project-dev-server-launchd-tunnel-stack]].
- **Isolation is the safety story:** long autonomous edits on real local folders
  require bulletproof git worktree isolation + agency gate (no push/merge without
  Jon's nod) before Ares gets edit powers.
- **Autonomy (confirmed default):** Ares reads/edits/runs/tests/commits on his own
  isolated branch autonomously; gate only push / merge-to-main / deploy / prod /
  spend.

## Proposed re-sequence (supersedes original Brief 2 scope where they conflict)

This vision is bigger than `briefs/ares-2-forge-code-core.md` as written; treat
it as its own initiative.

0. **Server + tunnel hardening** — make the mini genuinely always-reachable.
1. **Durable run model** — builds survive disconnect; reconnectable from any device.
2. **Ares drives the full build loop in Forge** — agent loop + coding tools +
   streaming the steps (the "experience").
3. **Worktree isolation + agency gate** — safe autonomous edits.
4. **One-brain hydrate/capture** — DONE: data layer shipped 2026-06-19
   (`ProjectWorkspaceMemory`, migration 0101). Wire hydrate-on-start /
   capture-after-turn into the build loop.
5. **Mobile pared-down view** — later, if beneficial.

Discipline (Jon flagged Forge keeps losing to other priorities): build the
THINNEST end-to-end magic slice first — fire a real build from a browser, it runs
on the mini, get a result + approve — before investing in breadth. If even that
slice keeps getting deprioritized, that is the honest signal about its priority.

## Open questions

- "Full experience minus right rail": confirm this means skip/defer right-rail
  extras even on desktop for v1, vs only on mobile.
- Does hardening go strictly first, or in parallel with the durable-run slice?
- Which "other project" is the first real test build for the engine?
