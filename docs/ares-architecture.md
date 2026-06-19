# Ares / Forge — Architecture & Decisions (North Star)

> Companion to `docs/ares-plan.md` (the deeper phased plan) and
> `docs/named-agent-build-playbook.md` (the agent-registration recipe).
> This doc records the **firmed decisions** for the build and the **reuse map** so
> we extend existing infrastructure instead of rebuilding it. All file:line refs
> were accurate at writing — **verify at build time**, they may have shifted.

## What Ares is

**Claude Code, cloned inside the Artemis OS (claude.ai-style) app** — a coding /
build partner — **owner-private** (Jon only), living in the **"Forge"** surface
(renamed from "Dev Projects"). It carries over the relevant Artemis-OS upgrades
and can **dispatch cheaper sub-agents (Codex + a local LLM) to save cost**.

The goal is parity with the claude.ai Code experience **plus our extras**, not a
from-scratch build. Much of the substrate already exists (see Reuse Map).

## DECISION 1 — Session model: many sessions, one brain

**Multiple sessions** (task/feature/bug-scoped, like Claude Code), **NOT** one
monolithic chat. Rationale:
- It's how Claude Code works — the model Jon wants cloned.
- Cost (Jon's explicit priority): a single growing chat re-sends its whole history
  every turn → expensive and degrades. Scoped sessions stay lean.
- Parallelism: Ares + his sub-agents can work multiple threads at once.
- The plumbing exists: `dev_sessions` is already a per-session table.

Continuity ("Ares remembers me / the project / past decisions") comes from the
**shared memory layer**, not one transcript — exactly like Claude Code's memory +
CLAUDE.md. So: **many sessions, one brain.** The "brain" = the project-workspace
memory (Brief 2) + the scoped memory observations Ares already inherits.

## DECISION 2 — Cost: plan on Claude, delegate volume to Codex + local LLM

Ares **plans/architects on Claude (Opus)** and **delegates high-volume or cheap
sub-tasks** (bulk codegen-as-text, research summarization, classification) to
**Codex** and a **local LLM** (LM Studio, which Jon hosts locally for testing via
`ARTEMIS_LM_STUDIO_BASE_URL`).

**HARD CONSTRAINT (do not design around this):** `codex` and `lm-studio` are
**text-only — they cannot run tools** (`providers/registry.py` capability table;
lm-studio adapter ignores `request.tools`). So only **tool-less** sub-tasks may be
routed to them: "produce this diff/code as text", "summarize these files",
"classify X". Anything that must *call tools* (read files, run bash, git) stays on
the tool-capable path (claude-code / Ares himself). This matches the standing
provider-cost reality: tool capability is the axis for what can move off Claude.
Also: `lm-studio` must **never** be a `complete_with_fallback` fallback target
(it's a local dead-end — `fallback.py` forbids it).

## DECISION 3 — Autonomy: move freely in the sandbox, confirm at the boundary

Ares is a build *partner* with more autonomy than the other agents — but bounded:
- **Autonomous** inside his work sandbox: read/edit files, run builds/tests, make
  commits **on an isolated git worktree/branch** (never the shared main working
  tree — reuse the worktree-isolation discipline we use for our own workers).
- **Confirm first** (agency gate: propose → Jon confirms) for anything outward or
  hard to reverse: `git push`, merging to main, deploys, prod DB, anything leaving
  the machine. Jon confirms conversationally (no buttons).
- Ares may **ping Jon directly** with Build Reports / blockers (he has his own
  pings), but **only Artemis bypasses notification silence** — Ares does not.

## Our upgrades that carry into Ares ("the extra functions, if relevant")

Carry over: **memory** (the shared brain across sessions); **tool-calling
discipline** (the "acting means calling a tool" rule — Ares must call tools, never
narrate, and never claim a tool is missing); **agency gate** (per Decision 3);
**proactivity / Build Reports** (Ares DMs progress + blockers); **humanization**
(global, automatic); **named-agent loop + relevance gate + event dedup**;
**worktree isolation** for his own code edits; **sub-agent spawn + provider
cascade** (the cost fleet).

Not relevant (leave out): marketing pipeline / signal queue / Callie / Argus,
enablement / Kai, OKR check-ins. Ares is dev-focused.

## Reuse Map (audit findings — verify at build time)

| Need | Status | Where | Note |
|---|---|---|---|
| Forge surface | rename only | `public/js/core/navigation.js:96` | change the **display label** "Dev Projects"→"Forge"; KEEP slug `dev-projects` |
| Owner-only gating (surface) | ready | `artemis/routes/dev_projects.py` (`Depends(require_owner)`), `artemis/marketing/routes/_auth.py` | `/api/dev-projects/*` already owner-gated |
| Session model | ready | `artemis/dev_projects/models.py` (`dev_sessions`, `dev_messages`) | per-project, has provider/model/bypass/pinned/fork_of |
| Code-running loop | ready | `artemis/dev_projects/loop_runner.py` (`_maybe_run_local_tool`) | existing bash + file ops with a permission gate — the "Claude Code clone" core |
| Provider cascade / routing | ready | `artemis/providers/{registry,resolver,fallback}.py`, `resolve_adapter_async` | codex + lm-studio wired; `DEFAULT_CASCADE` |
| One-shot delegation | ready | `artemis/floating_artemis/tools/core.py` `spawn_subagent` | one-shot; named multi-step needs a new primitive (Brief 3) |
| Named-agent registration | recipe | `docs/named-agent-build-playbook.md`; template = Kai | persona/scope/surface-allowlist/tools/Slack/activate |
| Ares persona profile | exists | `ares-personality-profile.md` (repo root) | add `ARES_PERSONA_CORE` inline to `personality.py` `_AGENT_DEFAULTS` |
| Owner-private agent scope | recipe | `artemis/identity/scope_policy.py` (`allowed_scopes_for_agent`) | add `agent:ares` owner-private allowance |
| Surface allowlist | recipe | `artemis/floating_artemis/session_scope.py` (`_AGENT_SURFACE_ALLOWLIST`) | `"ares": {"dev-projects"}` |

## Build sequencing — the first set

Foundation is pulled **forward** (vs ares-plan.md's ordering) so Ares is **live
and de-risked early** — identity/access is the well-trodden, low-risk path (Kai
template), and Jon gets something real to talk to fast.

1. **Brief 1 — Foundation** (`briefs/ares-1-foundation.md`): rename Forge; register
   Ares as an owner-private named agent (persona, scope, surface allowlist, Slack
   DM + #forge). Outcome: Ares exists and talks, owner-only.
2. **Brief 2 — Forge Code core** (`briefs/ares-2-forge-code-core.md`): Ares drives
   the multi-session dev_projects coding loop with a shared **project-workspace
   memory** (the "one brain"). Outcome: the Claude-Code-clone experience under
   Ares, sessions + persistent project memory.
3. **Brief 3 — Sub-agent cost fleet** (`briefs/ares-3-subagent-fleet.md`): Ares
   routes volume/cheap sub-tasks to Codex + local LLM, and gains a named
   multi-step delegate primitive. Outcome: cost-optimized fleet.

## Gotchas (every brief must respect)

- **Org rule:** never add/upgrade a dependency <7 days old; commit lockfiles.
- **Tool-calling discipline:** Ares must CALL tools, never narrate; never claim a
  tool is "not wired." (See the shared "acting means calling a tool" prompt block.)
- **Circular imports:** agent tool modules + package `__init__` must NOT import the
  providers stack at module level — lazy-import inside functions (crashes app boot
  otherwise).
- **Crypto:** use `artemis.integrations.crypto.encrypt_credentials` (bytes) for the
  integrations row, NOT `artemis.connectors.encryption` (str).
- **Persona:** ASCII quotes only (curly quotes break voice-corpus parsing).
- **Slack #forge:** private channels fire `message.groups` (not `message.channels`)
  and need `groups:history` scope.
- **Restart:** `launchctl kickstart -k gui/$(id -u)/me.artemisos.app` then **verify
  the pid changed** + `/healthz` 200. Plain kickstart is a no-op on a running svc.
- **Migrations:** check `alembic heads` at build time; number sequentially; run
  `alembic upgrade head` against the live DB (reads `.env`).
- **Worktrees:** Ares's own code edits + our build workers use isolated git
  worktrees on `worker/<scope>` branches; Lead merges. Never edit shared main tree.
- **Test DB:** `ARTEMIS_TEST_DB_URL` for pytest; `ARTEMIS_DB_URL` for alembic.
- **Live-test cleanup:** if a live test writes real rows (memory/sessions), delete
  them afterward so they don't pollute prod.
