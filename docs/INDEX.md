# Artemis OS — Docs + Briefs Index

**Living doc. Last updated:** 2026-05-30 LATE
**Purpose:** single catalog so future LLMs (or humans) know which doc to read first + what's in `briefs/` without grepping 120 files.

---

# 🎯 If you are picking up this codebase cold, read in this order

| Order | Doc | What you learn |
|---|---|---|
| 1 | `docs/PLATFORM-MAP.md` | What's wired, what's hollow, what's deprecated. Every UI surface + every background pipe. **The single most important onboarding doc.** |
| 2 | `docs/LEAD-SESSION-LOG.md` (top section) | Recent decisions + current branch state + rollover context |
| 3 | `docs/ROADMAP-2026-05-30.md` | Forward plan, brief sequences, active vs banked work |
| 4 | `docs/ARTEMIS-OS-MASTER-PLAN.md` | Philosophical anchor + the 8 locked D-decisions |
| 5 | `docs/SITE-MAP.md` | UI navigation specifics (left rail structure) |

After those: skim the **Design docs (locked)** section below to know which architectural decisions are committed.

---

# Living docs (continuously updated)

These should always reflect current state. Update protocols live inside each.

| Doc | What | Last revised |
|---|---|---|
| `docs/PLATFORM-MAP.md` | Comprehensive platform state — UI surfaces, background piping, memory architecture, module health, Locked Decisions Ledger | 2026-05-30 |
| `docs/LEAD-SESSION-LOG.md` | Decision trail across sessions. Rollover-safety doc. Top section = current snapshot. | 2026-05-30 |
| `docs/ROADMAP-2026-05-30.md` | Forward plan: ready-to-fire briefs, in-flight streams, banked work, strategic horizons | 2026-05-30 |
| `docs/ARTEMIS-OS-MASTER-PLAN.md` | Build philosophy + locked D-decisions (D1-D7, D6.1) + invariants | 2026-05-26 |
| `docs/SITE-MAP.md` | UI navigation reference. Has staleness risk — verify per-surface entries against PLATFORM-MAP. | 2026-05-22 (needs refresh) |
| `docs/HANDOFF.md` | Per-session handoff snapshot | Check before assuming current |
| `docs/INDEX.md` | This file | 2026-05-30 |

---

# Design docs (locked architectural decisions)

These document committed architectural shapes. The decisions inside them are LOCKED — don't relitigate without explicit reason. Reference them when drafting new briefs.

| Doc | Stream | Status |
|---|---|---|
| `docs/memory-shell-vision-2026-05-29.md` | Memory Carryover (MC) + Memory Wings (MW) UI | 13 D-decisions locked. MC1-MC5 done. MW1 done. MW2-MW4 deferred until ~4 weeks of data. |
| `docs/platform-stewardship-design-2026-05-30.md` | Stewardship (SH1-SH5) — hybrid local+cloud health checker | Locked design. Build deferred 6-8 weeks (needs ~4 weeks accumulated memory). |
| `docs/signal-playbook-design.md` | Signal Playbook UI for Josh's spec | Locked 2026-05-26. SP brief drafted at `briefs/sp-signal-playbook-combined.md` (combined SP1+SP2 per session-end decision). |
| `docs/campaign-initiation-and-district-design.md` | Campaign Initiation step + District first-class entity (NCES tier classification, soft-flag D4) | **Fully locked 2026-05-31.** Stream 1 brief DIST1 drafted + firing. Subsumes old locked D4. Current top priority. |
| `docs/builder-responsiveness-design.md` | Builder + FA + Pipeline AI Panel responsiveness | Phase 1 (B+A): streaming + --continue at adapter layer. Phase 2 (C): persistent subprocess. Deferred until daily-use latency friction. |
| `docs/pipeline-authoring-principles.md` | P1-P8 principles for grounding the AI Pipeline Builder | Locked principles. Bake into AI Pipeline Builder system prompt when that brief fires. |
| `docs/claude-code-mcp-tool-execution.md` | Subscription-path tool execution design (CC1+CC2 origin) | Locked. CC1+CC2+CC19 all live. |
| `docs/tool-execution-architecture.md` | F4 — tool execution at the agent layer | Locked. Implemented across CC10-CC20. |

---

# Audits (point-in-time findings)

These captured platform state at a specific moment. They guided the work that followed. Re-read when investigating their topic, but don't expect their per-section claims to be current — verify against PLATFORM-MAP.

## This session (2026-05-29 / 2026-05-30) — drove the work that just landed

| Doc | What it found | Status |
|---|---|---|
| `docs/hollowness-audit-2026-05-29.md` | 3rd hollowness layer + classification framework. Found definition_proposals=0 after CC10-CC18. | Drove CC19, CC20, the whole anti-hallucination stream |
| `docs/memory-audit-2026-05-29.md` | Memory keystone P4 unstarted: 11 tables, 1 row. | Drove M1-M6 + MC1-MC5 + MW1 |
| `docs/hallucination-audit-2026-05-29.md` | 10 LLM call sites enumerated; 3 HIGH risk + 5 MEDIUM | Drove H1-H4 (H5 in flight as of session-end) |
| `docs/self-improvement-consumer-side.md` | Consumer-side gaps after CC10-CC17 producer side | Drove CC18 + Proposals Inbox + (eventually) CC22 |
| `docs/marketing-flow-audit-2026-05-30.md` | 5-surface audit: pipeline IS alive (202 signals, 64 briefed, 13 pending). **New hollowness**: Campaigns tab renders hardcoded `CAMPAIGNS` array (CMP1 fix). Dashboard mock-fallback (MD1). content_assets=0 (CMP4 seed). | Drives CMP1+MD1+CMP4 post-CC12 |

## Pre-session audits

| Doc | What it found | Status |
|---|---|---|
| `docs/agent-audit-2026-05-28.md` | Self-improvement loop dead-on-arrival (0/236 summaries) | Drove CC10-CC18 stream |
| `docs/blueprint-audit-2026-05-26.md` | 3-layer hollowness: data + runtime + tool execution | Drove F1-F6 + P1-P4 + CC1-CC9 |
| `docs/writing-studio-audit-2026-05-28.md` | Studio surface alive, content-agent handoff broken | CC12 (queued) will close the handoff |

---

# Reference docs (specific topics)

Topic-specific, durable references. Read when you need to dig into that topic.

| Doc | What |
|---|---|
| `docs/integrations/legiscan.md` | LegiScan API integration + operating contract (query limits, hashes, CC BY 4.0, key setup). First entry in `docs/integrations/` — pattern for future connectors. |
| `docs/MARKETING-PIPELINE-CANONICAL.md` | Canonical marketing pipeline structure |
| `docs/MEMORY-DURABILITY.md` | Memory durability invariants + backup/restore design |
| `docs/marketing-slab-grounding.md` | Marketing-domain grounding for the agent architecture |
| `docs/STREAMS-2026-05-26.md` | Operational coordination doc (older — use ROADMAP for current) |
| `docs/macos-tcc-and-launchd.md` | macOS-specific deployment notes |

---

# External-facing docs

| Doc | Audience |
|---|---|
| `docs/artemis-os-overview.md` | COO / leadership-facing overview (drafted this session, handed to another instance for finalization) |

---

# Briefs catalog

`briefs/CONVENTIONS.md` defines the brief format. ~120 brief files total.

## Active (queued or in-flight as of 2026-05-30 LATE)

| Brief | Status |
|---|---|
| `briefs/dist1-district-entity-classifier.md` | ✅ Merged (2360245) — districts + tier bands + NCES loader, mig 0054, 7/7 tests |
| `briefs/pipe6-followup-frontend-prune.md` | ✅ Merged (e3935a1) — pruned dead Workflows/Automations frontend, -703 LOC |
| **Campaign-initiation stream (queued, sequential — NOTE Codex isolation rule in CONVENTIONS):** | |
| `briefs/dist2-tier-band-editor.md` | ✅ Merged (e7b7930) — band editor + District Sizing UI |
| `briefs/dist3-classifier-agent-signal-link.md` | ✅ Merged (7fc46a2) — classifier agent + resolved_district_id FK, mig 0055 |
| `briefs/dist4-qualifier-soft-flag-gate1.md` | Qualifier soft-flag + Gate 1 card (`mini`/medium) — after DIST3 |
| `briefs/ci1-initiation-substrate.md` | Initiation columns + deliverable registry (`mini`/low) — after Stream 1 |
| `briefs/ci2-initiation-step-pydantic.md` | Initiation step + Pydantic + brief_assembler (`gpt-5.4`/medium) — after CI1 |
| `briefs/ci3-initiation-ui.md` | Initiation UI form (`mini`/medium) — after CI2 |
| `briefs/inbox-ui-placement-fix.md` | 🟡 Deferred to UI pass (f6ab956 in worktree) |
| `briefs/h5-daily-brief-pipeline-ai-pydantic.md` | ✅ Merged (67719fd + c32b6ca prompt.py follow-up) |
| `briefs/sp-signal-playbook-combined.md` | ✅ Merged (69876bc) |
| `briefs/pipe6-workflows-automations-sunset.md` | ✅ Merged (c25eb4e) |
| `briefs/cc12-content-agent-tools.md` | ✅ Merged (e7ea758) |

## Recently completed this session (merged on `lead/j6a-granola-integration`)

Memory keystone stream:
- `m1-trajectory-summary-to-memory-observation.md` ✅ (commit b0bfefd)
- `m2-builder-reads-agent-memory.md` ✅ (commit b4eea5a)
- `m3m4-floating-artemis-memory.md` ✅ (commit 26b1f15)
- `m5-marketing-signal-to-memory.md` ✅ (commit acf3926)
- `m6-memory-shell-ui.md` ✅ (commit d7fc20c)
- `mw1-multiscope-schema.md` ✅ (commit 0a39ba6)
- `mc1-definition-proposal-approval-to-memory.md` ✅ (commit 5134bd6)
- `mc2-mc5-carryover-bundle.md` ✅ (commit 3cb8245)
- `cc29-rejection-memory-carryover.md` ✅ (commit f0266c5)

Anti-hallucination + Builder + grounding stream:
- `cc18-wire-target-id-from-agent-profile.md` ✅
- `cc19-builder-mcp-tool-execution.md` ✅
- `cc20-builder-grounding-tools.md` ✅
- `h1-self-teaching-tool-errors.md` ✅
- `h2-scout-intake-pydantic.md` ✅
- `h3-trajectory-summarizer-pydantic.md` ✅
- `h4-meeting-summarizer-pydantic.md` ✅

Substrate + cleanup bundles:
- `bundle-a-substrate-refinement.md` (CC27+CC28) ✅ (commit 13dcbcc)
- `bundle-b-observability-ux.md` (CC21+CC22) ✅ (commit 0bcb816)
- `cleanup-batch-cc23-26.md` (CC23+CC24+CC25+CC26) ✅ (commit d879d44)

Self-improvement loop foundation (earlier in session):
- `cc10-trajectory-summarizer-gc-fix.md` through `cc17-mcp-tool-invocation-log.md` ✅
- `proposals-inbox.md` ✅

## Banked / not-yet-fired (active future work)

Per `docs/ROADMAP-2026-05-30.md` priority order:

| Brief | Why queued |
|---|---|
| (none yet drafted: PIPE6 implementation, CC12 Writing Studio handoff, MW2-MW4 Memory Wings UI, SH1-SH5 Stewardship) | Drafts come when each fires per ROADMAP |

## Historical (work that landed in prior phases — reference only)

The bulk of the briefs/ directory. Examples:
- `pipe1-pipeline-data-model.md` through `pipe5-marketing-pipeline-seed.md` — Pipelines substrate (Phases PIPE1-PIPE5)
- `m1-reason-code-registry.md` through `m7-writing-studio-overview.md` — Marketing modules
- `f1-josh-spec-parser.md` through `f6-agent-invocation-task.md` — F-stream (Josh spec → runtime)
- `p1-scout-blueprint-rebuild.md` through `p4-qualifier-content-blueprint-rebuild.md` — P-stream (blueprint rebuild)
- `o1-agent-builder-and-self-improvement.md` through `o5-builder-breadcrumb-and-nav-polish.md` — O-stream (Agent Builder foundation)
- `cc1-artemis-mcp-server.md` through `cc9-dedup-fallback.md` — early CC stream
- `j6c-meetings-rebuild.md`, `j7-daily-brief-port.md`, `j8-slack-signals-port.md`, `j9-slack-triage.md` — J-stream (Jon's personal workspace ports)
- `cleanup-*.md` — various cleanup batches
- `op1-automations-port.md` — Automations port (now subject to PIPE6 sunset per D6 lock)
- `patch-*.md` — historical patches

**Convention:** if you find a brief named for a stream you're working on, treat it as historical context unless you can verify it's the active brief via git log on the relevant branch.

---

# When to re-read this index

- Starting a new session → read PLATFORM-MAP, LEAD-SESSION-LOG, then this index
- Considering a "new" plan → check the Locked Decisions Ledger in PLATFORM-MAP first; check Design docs in this index second; only then draft new plan
- Wondering "is there a doc about X?" → check this index sections
- Onboarding a new LLM session → start with the "Read in this order" section above

---

# Update protocol for this index

- Doc created → add to relevant section
- Doc archived / deprecated → move to a `_archive` subsection (don't delete — lossless invariant on docs too)
- Brief completed → optionally move to "Recently completed" if it's significant
- Locked decision shipped → note in PLATFORM-MAP's Locked Decisions Ledger, not here

This file is meant to be a quick lookup. Resist the urge to duplicate content from the docs themselves — link instead.
