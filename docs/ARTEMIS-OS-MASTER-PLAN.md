# Artemis OS — Master Plan

**Living doc. Updated whenever a strategic decision lands.**
**Last revised:** 2026-05-20 (Jon + Lead — platform-framing clarification + Marketing seed reframe)

---

## What Artemis OS is

Artemis OS is a **platform for building agents and workflows**. It is not a marketing tool. It is not a productivity tool. It is the operating-system layer on top of which any operational pattern (marketing, sales, customer success, research, internal ops) can be built by composing agents, skills, workflows, automations, and memory.

The product is the **Agent-Builder pattern**: you describe what you want via conversation, a Senior-Engineer-class builder agent designs the definition with you, you commit it, the system runs it and watches itself work. Over time, the system proposes improvements to its own definitions based on what it observed.

**The marketing agents are the first seed dataset.** They demonstrate what Artemis can do. They are not features. A user who wants Artemis for a different domain (sales, support, research) builds their own seed via the same Builder pattern.

This framing matters because:
- "Build the Marketing OS" = wrong instinct (treats marketing as a fixed feature)
- "Build Artemis with the marketing agents loaded as seed data" = correct framing (marketing is content; the platform is the product)

---

## Build philosophy

### 1. Conversation > forms

CRUD forms are the wrong creation primitive for meta-objects (agents, skills, workflows, automations). They make users fill in fields without thinking about coherence. The Agent-Builder pattern (chat with a builder agent that helps design the definition) is the canonical path. Form CRUD remains for power users / quick edits, but Builder is the front door.

**Empirical validation:** Jon's kill-criterion test 2026-05-20 on O1's Agent-Builder: *"the form version is too complicated for an average user, the builder version is what we need to focus on."*

### 2. Self-improvement is structural

Every agent we ship — marketing seed or otherwise — must include the **self-improvement loop**: each run produces a trajectory summary (what worked / what stalled / what was missing), summaries accumulate, the Builder reads recent summaries and proposes definition updates with run-id citations. This is part of the blueprint, not an optional extra.

Without this, agents drift from intent and require manual diagnosis. With this, agents tell us when they need to change.

### 3. Memory is lossless by structure

Every memory-write source lands in `raw_inputs` first (verbatim, append-only, hash-chained). Cold archive moves payloads off-row after 90 days; the hash chain stays continuous. Nightly pg_dump backups with weekly drill (restore-to-temp + chain verification).

**This is non-negotiable.** Memory v2 architecture in `decisions/memory-v2-architecture.md` lists six tiers (M1 done; M2-M6 sequenced). The system structurally cannot lose facts.

### 4. Verbatim ports beat descriptive ports

When a source-of-truth implementation exists in `claudeck-artemis/` (Node reference), port the CSS/JS/business logic verbatim. Don't describe-and-guess. The "describe + agent matches" pattern costs 3-cycle iteration to approximate something a 1-cycle copy nails.

Caveat: ports include DOM structure + class names + element wrapping, not just CSS numeric values. A "verbatim" port that copies values but not structure produces visual mismatches we've already burned through in Dev Projects v3.

### 5. Flexibility > hardcoded values

No hardcoded skip lists, district arrays, ruleset overrides, or magic constants in code. Everything that might change with team input lives in a config table (`territory_config`, `district_marketing_flags`, `signal_reason_codes`, `rulesets`, etc.). Code reads from tables. Tables are operator-mutable.

When in doubt: would Anne Marie or Josh ever need to change this without a deploy? If yes → table.

### 6. Invariants over conventions

Where a property is load-bearing, enforce it structurally. Examples:
- Lossless memory: raw_inputs append-only with hash chain (not "be careful in code")
- Ruleset versioning: `campaign_ruleset_versions` append-only table (not "remember to bump version")
- Reason code validation: FK from signal_queue.reason_codes into signal_reason_codes table (not "validate in handler")
- Conftest hard-fail on non-test DB before any TRUNCATE (not "be careful which DB the test points at")

Conventions are what we follow when we remember. Invariants are what the system enforces when we forget.

---

## Where we are

### Personal workspace — DONE end-to-end

All eight surfaces walkable + functional:
- Welcome / Status popover / Integrations modal
- Calendar / Meetings / Jira Board / OKR Studio
- Focus (Daily Brief, Slack signals, calendar/meetings/OKR pulls)
- Dev Projects v3 (Claude Code Desktop chat UI port + Agent-Builder integration)

Polish nits captured in Jon's personal notes; deferred to a single coherent style-redo pass.

### Operations slab — mid-flight

J10-J11 closed the major transport blockers. Recent landings:
- J10 trailing-slash compat (cross-cutting)
- J11 Agents Operations parity (instruction files, supporting files, skills assignment, run aliases)
- J10d Connectors UI needs_reauth (closes the silent OAuth-expiry loop)
- J10e OAuth token refresh scheduler (proactive, 15-min cadence)
- Skills lifecycle port (proposed/approved/archived + assign/unassign + categories)
- Codex CLI -m flag + model catalog
- Workflows latest-run + background run parity (commit `3803592`)
- **O1 Agent-Builder + Self-Improvement** (the jugular — replaces CRUD-form creation for all meta-objects). Empirical kill-criterion passed 2026-05-20.

**Merged 2026-05-20 (all live on `lead/j6a-granola-integration`):**
- O1 Agent-Builder + Self-Improvement (`4ead96a`)
- O1a refresh persistence bugs
- O2+O3 Agent Card detail surface + persona/soul
- O4 streaming SSE for Builder responses
- O5 Builder breadcrumb + nav polish (Codex, state-driven nav pattern)
- M1 reason code registry (17 codes, FK validation in intake) — `7b1f5fa`
- M3 campaign state machine (5 enums, transition(), audit table, soft CHECK) — `5fe78f1` / merge `79b4cf0`
- M5 16 marketing agents seeded as DB fixtures — `215cd0b` / merge `bbf79d5`
- Codex CLI effort/speed knobs — `014f9f4`

**In flight 2026-05-20 (terminal-Lead Sonnet workers, isolated worktrees):**
- M3a state sweep + enum completion + CHECK tighten (~450 LOC cap)
- Memory-M2 validity windows + confidence + conflicts (~500 LOC cap)
- M7 Writing Studio overview aggregator (~250 LOC cap)

**Queued (paste-ready briefs on lead):**
- M4 qualifier rule layer (Josh's §4 hard-skip / suppress / boost, 12 rules) — depends on M3a
- M5b scout execution path (single runner + 9 stub adapters + APScheduler) — depends on M3a + M5
- OP1 Automations registry port from Node (next Operations gap)
- OP-cleanup stale test fixes (Slack permalink + migration test path)
- start-app.sh hardcoded path fix

### Marketing seed (Artemis seed data) — planned, not started

The marketing agents are the first seed content for Artemis. **Canonical build target** as of 2026-05-20: `docs/marketing-ops-v1/` — a complete v1 build spec the team finalized (43 files, ~4,900 lines).

**v1 scope (narrowed from earlier framing):**
- 9 scout agents (1.1 Starbridge Researcher … 1.9 Leadership Transition Scout)
- 4 qualifier-team agents (2.1 Cross-Reference, 2.2 Ruleset Manager, 2.3 Ruleset Compiler, 2.4 Brief Composer)
- 3 content-team agents (5.1 Brief Assembler, 5.2 Asset Selector, 5.3 Writing Studio Adapter)
- **Total: 16 agent definitions to seed**
- 3 rulesets seeded (OBC, biliteracy, dyslexia) — narrowed from Josh's spec §3 which listed 5
- 8 shared schemas (signal, brief, ruleset, asset bundle, etc.)
- 6 services (signal queue, memory layer, ruleset storage, contact DB stub, territory config, PDF extractor)

**v1 explicit out-of-scope:**
- NO outreach / send orchestration (Artemis ends at Writing Studio queue)
- NO Contact team / enrichment (stub returns True for priority districts)
- NO Compliance team (brand voice lives in Writing Studio)
- NO Track/Learn feedback loop (deferred)
- LinkedIn Observer Mode A disabled (Mode B / Leader Monitor only)

**Three regressions Python migration silently dropped** (highest-priority Layer 1 fixes — see `docs/marketing-slab-grounding.md` for full reconciliation):

1. `signal_reason_codes` table — Node had it; Python kept the JSONB column but dropped the registry. Josh's 17-code spec has nowhere to land. Invariant I-10 unenforceable.
2. Qualifier boost/suppress/skip rules — `qualifier.py` is a faithful port of the deterministic scorer, but signal-spec §4's actual qualification *intelligence* has zero implementation.
3. Campaign state machine — `/advance` is decoration. The 15-state machine is documented but not executed.

**M-series brief sequence (revised 2026-05-20 reflecting actual landings):**
- ✅ **M1** reason-code registry — MERGED 2026-05-20 (581 LOC, 17 codes seeded, FK in intake)
- ✅ **M3** campaign state machine — MERGED 2026-05-20 (5 enums, transition(), audit table, soft CHECK)
- 🟡 **M3a** state sweep + enum completion + CHECK tighten — Worker in flight (~450 LOC cap)
- 📋 **M4** qualifier rule layer — paste-ready brief, queued (depends on M3a)
- ✅ **M5** 16-agent DB seed — MERGED 2026-05-20 (Codex, 324 LOC, persona JSONB populated)
- 📋 **M5b** scout execution path — paste-ready brief, queued (depends on M3a + M5)
- 🟡 **M7** Writing Studio overview — Worker in flight (~250 LOC cap)
- 📋 **Memory-M2** validity windows + confidence + conflicts — Worker in flight (~500 LOC cap)

Skipped from earlier sequence: original "M2 Layer 1 seed loader" was rolled into M1 (registry+loader) and M5 (agent fixtures), no longer a standalone brief.

Source data:
- **`docs/marketing-ops-v1/`** — the build spec (per-agent files in `agents/`, schemas in `schemas/`, services in `services/`, ruleset YAMLs in `rulesets/`)
- `decisions/campaign-signal-spec-v1.md` — Josh's seed (17 reason codes, territory config, qualifier rules, per-state nuance) — verbatim, do not edit
- `docs/marketing-slab-grounding.md` — reconciliation between target + signal spec + current Python state
- `claudeck-artemis/docs/MARKETING_WORKFLOW_BUILD_SPEC.md` — historical reference only (frozen)

### Memory v2 — M1 done, M2-M6 sequenced

M1 (lossless foundation: raw_inputs + hash chain + cold archive + nightly backups + weekly drill) is shipped and quietly working. Every interaction lands in raw_inputs hash-chained.

M2 (validity windows + confidence + conflicts table) is the next biggest value. M3-M6 sequenced after.

**Memory HTTP routes** are the prerequisite for visibility into what M1 is collecting. Currently the Memory page shows empty because there's no read surface. The HTTP pass is Lead-led, ~1000 LOC, blocked behind Operations slab stabilization.

---

## Five committed architectural decisions (with reasoning)

These are decisions we've made together that future sessions inherit:

### D1 — Builder-first creation, forms-supplemental

Every meta-object (agent, skill, workflow, automation) gets a Builder. CRUD form stays for quick edits and power users. New surfaces ship Builder-primary.

**Why:** Empirical kill-criterion 2026-05-20. Forms are too complicated for average users; Builder is the access point.

### D2 — Self-improvement integration is mandatory for shipped agents

Every agent definition in the system (marketing seed or user-built) includes:
- Trajectory summary generation on every run completion
- Builder re-open pattern that reads recent summaries and proposes definition updates
- Citation format with `run_ids` array linking proposals to evidence

**Why:** Without it, agents drift from intent silently. With it, agents surface their own gaps.

### D3 — Ruleset versioning is append-only from day 1

`campaign_ruleset_versions` ships as a separate append-only table (port from Node slice 13). `rulesets.version_tag` alone is insufficient — invariant I-21 needs structural enforcement.

**Why:** Jon's 2026-05-20 call. Cheaper to ship right than retrofit.

### D4 — HMH partner flag is operator-mutable

`district_marketing_flags` table where operators flag districts as HMH-partner / skip / etc. Salesforce integration when SF integration ships. Hardcoded lists are fragile.

**Why:** Jon's 2026-05-20 call. Flexibility-first.

### D5 — Scout agent runtime is M5b, not M5

M5 ships the 9 agent definitions (system prompts + tool lists + persona) as DB fixtures. Execution path (workflow runner + tool wiring + monitoring) is M5b — separate brief, after definitions exist.

**Why:** Definitions are demoable + Builder-editable without execution. Execution adds complexity better isolated. Jon's 2026-05-20 call.

### D6 — Pipeline is the unified orchestration primitive

Workflows, Chains, DAGs, and Automations all reduce to directed graphs of operations with optional triggers. We ship a single `Pipeline` concept that subsumes all four. Existing primitives either get auto-migrated to Pipeline rows (PIPE6) or sunset entirely.

**Node types in scope:** Agent invocation, Skill call, Trigger (manual / scheduled / webhook / event), Human Gate (approval pause), Conditional branch, Sub-pipeline call.

**UX requirements (non-negotiable):**
- Pipelines surface MUST show both simple automations (e.g., "email agent checking inbox every hour" = one-node pipeline with trigger) AND complex multi-node pipelines (marketing pipeline) as first-class citizens in the same list view.
- Every pipeline must be enable/disable-able from the list view without entering the editor.
- Every pipeline must be editable from the list view (click → opens visual canvas).
- Visual canvas style: n8n's mental model with Artemis design language (spacious, fluid, purposeful — not n8n's utilitarian density).

**Agent invariant (locked alongside D6):**
- Every Agent MUST have `preferred_provider` AND `fallback_provider` selectable from the provider registry. DB fields already exist via O1; UI must surface them as required fields in Builder + Agent Card.

**Why:** Four separate orchestration primitives means four CRUD-form surfaces, four executors, four mental models — the same anti-pattern D1 (Builder-first) corrected for object creation. Pipeline unifies them. The marketing pipeline becomes the canonical first instance, validating the architecture with a real use case before legacy primitives are migrated.

### D6.1 — Pipelines belong to Operations, not domain tabs

Pipelines are infrastructure primitives, not domain content. They live under Operations → Pipelines alongside Agents, Skills, Memory. Domain pages (Marketing, future Sales, future Support) deep-link INTO Operations → Pipelines; they never own their own pipeline tabs.

A "View Marketing Pipeline" tile on the Marketing Dashboard click-throughs to the pipeline definition in Operations. The pipeline lives in one place; its activity status surfaces in many places.

**Why:** Mixing primitives into domain tabs would be the same conceptual error as putting Agents under Marketing. The orchestration tier and the domain tier stay cleanly separated.

---

## Open architectural decisions (need Jon's input)

These are blocking specific work. Each gets resolved when surfaced; documented here so future sessions know what's outstanding.

### Q1 — District roster canonical source

Status: **deferred for v1.** Scouts emit signals with `district_id = null`. Reconcile when canonical roster exists. Clean import UX is part of M2 / M5 — needs an admin surface that lets Anne Marie upload a CSV or paste a list, with reconciliation against existing signals.

### Q5 — Writing Studio agent shape

Status: **resolved.** The Writing Studio is ONE agent, trained by Angela / Olivia / Julie. It gets called from any workflow under context-specific guidelines (social / email / docs). One central agent to maintain. The "Writing Studio scouts" phrase in earlier docs was confusing language.

### Open from grounding doc (not yet answered)

- **Scout agent runtime architecture**: resolved as D5 above (definitions only in v1; execution path is M5b).
- **The "communicate with Josh" agent**: Jon mentioned that part of the agent we're building could be enabled to communicate with Josh for signal stuff. Open: is this a separate agent ("Josh-liaison"), a tool the Qualifier uses, or a Builder capability that lets Jon configure such an agent via conversation? Lean: ship as a Builder-producible agent in the v1 seed, but Jon decides whether it's pre-defined or build-on-demand.

---

## Three-instance topology

Artemis OS is built across three parallel "instances" working in tandem:

### Instance 1 — Lead (this Claude conversation)

**Role:** Strategic synthesis, brief authoring, architectural decisions, spawning small sub-agents for read-only audits or focused fixes.

**Strengths:** Cross-cutting reasoning, holding full project context, planning sequencing.

**Constraints:** Cannot reliably spawn isolated-worktree Workers from this session (CWD lives in `claudeck-artemis`, spawn would land in wrong repo — the CWD trap). All Worker spawns flow through terminal-Lead.

### Instance 2 — terminal-Lead (other Claude, in artemis-os)

**Role:** Owns the artemis-os repository. Spawns isolated-worktree Workers safely. Drives merges, alembic migrations, uvicorn restarts, browser smoke tests.

**Strengths:** Lives in the target repo. Has the Bash tool aimed at the right place.

**Pattern:** Receives paste-ready prompts from Lead, spawns Workers in `.claude/worktrees/agent-*/`, surfaces consult-pings back to Lead via Jon as messenger.

### Instance 3 — Codex (subscription-based, OpenAI)

**Role:** Mechanical ports from Node reference, well-specified backend work, schema migrations, read-only audits, contract drift fixes.

**Strengths:** Subscription tokens (essentially free), efficient at port-style work, good calibration after early sessions established discipline patterns.

**Pattern:** Receives paste-ready prompts from Lead. Lands commits directly on `lead/j6a-granola-integration` (not isolated worktrees — Codex's pattern works in main).

### Sub-agents (spawned by Lead)

**Role:** Read-only audits, planning reconciliation, brief drafting under specific framing, focused fixes that stay in the main worktree (no isolation needed).

**Pattern:** Lead spawns via `Agent` tool. Limited to ~100-300 LOC tasks. For larger work, briefs go to terminal-Lead's Workers or Codex.

---

## Quality protocol (in effect across all instances)

1. **CWD-trap defensive reflex.** Run `pwd && git branch --show-current` BEFORE every commit, especially after background tools complete. The bash tool's CWD silently follows the harness into worker worktrees.

2. **`git diff --staged` before commits with renames.** `git mv` stages the rename; follow-up `Edit` calls don't auto-stage. Bit us twice (commits `bc13611`, `720e2c8`). Always re-stage explicitly.

3. **Conftests hard-fail on non-test DB.** Six sub-conftests had unsafe fallback to `settings.db_url`; commit `f083ab4` locked them. Don't regress.

4. **dotenv `override=False` is invariant.** Commit `7ad1598` restored after a silent regression. Documented in `artemis/__init__.py` above the `load_dotenv` calls.

5. **Verbatim ports include DOM structure, not just CSS values.** Eyeballing screenshots and copying numeric values produces 3-cycle approximations. Copy the rules AND the element wrapping.

6. **Empirical verification beats analytical.** After committing a visual fix, open the running page — don't just grep the served CSS. Rules in served CSS can be inert if they don't match the actual DOM hierarchy.

7. **LOC self-reporting uses `git diff --stat` insertions.** No estimating, no excluding "boilerplate" or tests. Calibration matters for trust.

8. **Workers commit on their own branch.** Lead audits + merges. No self-merge.

---

## Anti-patterns we've identified (don't repeat)

### A1 — Describe-and-guess polish loops

User reports visual gap → Lead describes gap to sub-agent → sub-agent makes best guess → user reports it's still off → repeat. Three iterations to approximate what one verbatim port would nail.

**Fix:** When a reference exists in claudeck, copy it directly. Don't describe.

### A2 — Audit-driven brief authoring without planning context

Codex's gap reports tell us what's broken in current Python. They don't tell us what the *target* is. Writing briefs from the gap report alone produces "patch the gap" briefs, not "build the right thing" briefs.

**Fix:** Ground every Marketing slab brief in the planning docs (`MARKETING_WORKFLOW_BUILD_SPEC.md`, signal spec, related PLAN docs), not just the audit.

### A3 — Treating audits as ground truth on semantics

Codex's audits check route shape. They don't check qualifier intelligence, state machine completeness, or whether ported business logic matches the source's behavior. Audit says "qualifier route exists and returns 200" — but Josh's §4 rules might be entirely missing inside.

**Fix:** For load-bearing semantics, reconcile against source-of-truth implementation (Node code or planning docs), not audit reports.

### A4 — Empty seed tables look healthy in audits

A route that returns `[]` is technically working. A page that renders an empty state isn't a UI bug. But if the seed is *missing*, the product is inert in a way route inspection doesn't surface.

**Fix:** Mark all seed-required tables explicitly. Status route should distinguish "endpoint live + table populated" from "endpoint live + table empty."

### A5 — Lossy "verbatim ports"

A sub-agent reports "I copied the CSS rules verbatim" — but the numeric values matched while the element wrapping changed. Result: visual mismatch despite matching grep evidence.

**Fix:** Include DOM structure in verbatim ports. When the reference has `<button class="x"><svg /></button>` and we have `<button class="x"><div class="icon-box"><svg /></div></button>`, the visual won't match no matter how identical the CSS is.

---

## Next-session resume prompt

For any future Claude session that boots cold on this project:

> Read `docs/ARTEMIS-OS-MASTER-PLAN.md` first. Then `git log --oneline lead/j6a-granola-integration | head -15` for current branch state. Then `decisions/campaign-signal-spec-v1.md` if working on Marketing seed. Then `decisions/memory-v2-architecture.md` if memory work surfaces. The Master Plan defines what we're building and why; recent merges define what's actually live. Don't make architectural decisions without checking the Master Plan first; if a decision contradicts it, surface the contradiction. Brief stack lives under `briefs/` — paste-ready briefs are the unit of work delegated to Sonnet Workers (via terminal-Lead Agent({isolation:"worktree"})) or Codex (mechanical-only).

### State as of 2026-05-20 session-mid

**Live in lead branch:** M1 (reason code registry), M3 (state machine), M5 (16 agent seed), O1-O5 (Agent-Builder + nav).

**In flight (Sonnet Workers in isolated worktrees via terminal-Lead):** M3a (state sweep + CHECK tighten), Mem-M2 (validity + conflicts), M7 (Writing Studio overview).

**Queued (paste-ready briefs in `briefs/`):** M4 (qualifier rules), M5b (scout execution), OP1 (Automations port), test cleanup, start-app fix.

**Next concrete action when session resumes:** review Worker outputs as they land, FF-merge clean ships, draft follow-on briefs (M4 + M5b spawn prompts) once M3a merges.

---

## Living edits

This doc is updated whenever a strategic decision lands. The Lead instance (this Claude conversation) maintains it. When the conversation closes, the Handoff doc gets refreshed and points new sessions back here.

**To update:** add a new entry under "Five committed architectural decisions" (D6, D7, …), update "Where we are" sections, mark open questions as resolved with the resolution.
