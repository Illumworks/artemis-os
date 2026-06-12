# Artemis Personal Assistant — Build Plan & Beat-Hermes Strategy

**Status:** DESIGN / STRATEGY (Jon-aligned 2026-06-10). Grounded in two studies: an audit of the current
floating-Artemis engine, and a competitive analysis of the reference repos in
`/Users/artemis/Desktop/Artemis/agent-references/` (Hermes Agent, OpenClaw, Hermes Self-Evolution). **Build
when sequenced — no build off this doc yet.** Companion to `docs/agent-slack-architecture.md` (Artemis +
Callie split).

**Reference-use boundary:** the competitor repos are studied for *approach* only. We do NOT copy their code
(IP/license). This plan is our independent design, informed by what they do well.

---

## Bottom line
Beating Hermes/OpenClaw isn't about a smarter chatbot — it's about being **integrated into Jon's actual
work + life with memory, safety, and initiative.** The engine is **~70% built**, and Artemis's *foundation*
is already stronger than both competitors. The "assistant feel" gap is **three things**: proactivity
(commitments + scheduling), Slack as a two-way channel, and agency-writes. Close those and Artemis wins for
its target user; add skill-capture + bounded delegation to win decisively.

---

## The competition (what we're beating)
- **Hermes Agent** (Nous) — self-hosted self-improving agent; ~30 messaging channels via one gateway; 40+
  tools; natural-language cron; **auto-creates skills from successful tasks** (a closed learning loop) + a
  curator that ages stale skills; `delegate_task` + a Kanban board for multi-agent. Very mature (v0.16,
  ~1,382 test files). *Standout: the learning loop.*
- **OpenClaw** — privacy-first self-hosted assistant; ~24 channels incl. iMessage/WhatsApp; voice; a live
  agent-driven **Canvas**; and the headline — a **Commitments engine** that *infers* the user's deadlines and
  its own promises from conversation and **follows up unprompted** (classified by sensitivity, with
  dedupe/snooze). Very mature. *Standout: proactive follow-up.*
- **Hermes Self-Evolution** — an *offline* optimizer (DSPy + GEPA) that improves an agent's prompts/skills
  from real session traces and opens a PR for human review. Early-stage. *Standout: gated self-improvement.*

## Where Artemis ALREADY wins (the foundation)
1. **Structured, domain-grounded memory** — the keystone (store / retrieve / **consolidate / graph**, lossless
   supersession) is more principled than Hermes's flat `MEMORY.md`/`USER.md` + FTS5 and deeper than
   OpenClaw's single-slot memory. The graph + consolidation is a real differentiator if surfaced.
2. **Native propose→confirm safety** — built in, not bolted on. For a non-technical owner acting on live
   business systems (OKRs, Slack, sends), confirm-before-write is the right default; competitors lean on
   sandboxing/DM-pairing.
3. **Deep vertical connectors** — Jira, Granola meetings, OKR Studio, the marketing pipeline, Calendar/Gmail.
   Domain-aware, not generic shell/browser tools. Artemis already knows *the work*.
4. **In-app floating UI tied to the workspace** — the competitors live in a terminal/chat client detached
   from the work; Artemis is embedded where the work happens.
5. **Curated, tested first-party connectors** — higher-trust for one serious user than the competitors'
   plugin-hub sprawl.

## The gap (the "assistant feel")
1. **Proactivity that closes loops** — today she only speaks when spoken to. OpenClaw's Commitments idea is
   the bar: infer Jon's open-loops/deadlines + her own promises (from chat, Granola meetings, email),
   classify by sensitivity, and follow up within a due window. *This is the single feature that turns
   "answers when asked" into "personal assistant."*
2. **Scheduling/cron with delivery** — durable (not turn-bound) jobs with catchup, hard interrupts, per-job
   overrides, chaining, and delivery into Slack/in-app. The substrate for #1.
3. **Slack as a two-way channel** — she only *sends* today; promote to a real inbound surface (gateway + DM
   pairing/allowlist + session continuity) so Jon chats with her where he already works.
4. **Agency-writes** — Gmail/Drive/Calendar are read-only; add compose/create/schedule behind the gate.
5. **Online learning loop / skill capture** — turn a successful multi-step procedure into a reusable named
   capability (Hermes-style), grounded in the keystone/graph rather than flat files.
6. **Bounded sub-agent orchestration** — a `delegate`-style primitive (parallel batch, role/depth caps,
   isolated context, summary-back) → the substrate for Callie reporting up.

---

## Strategy: how the combination wins
Foundation (memory-graph + safety + domain connectors + embedded UI) **×** the three closed gaps
(proactivity, Slack-channel, agency-writes) = an assistant that *remembers, acts in your real tools, comes to
you, and you can trust* — integrated into the business, not a generic bot. Then skill-capture + delegation
make it compound. None of that is copyable by a horizontal competitor.

## Phased build plan (grounded in the audit)

### Status as of 2026-06-12 (reality vs. the linear plan)
The plan was drawn P1→P6 linearly, but build order pulled the hard middle pieces FORWARD — so the remaining
line is shorter than it reads. Current state:
- **P1 — Slack two-way:** ✅ DONE + live (Artemis DM hardened: bot-self filter, owner allowlist, identity).
- **P2 — Proactivity engine:** 🔵 ~HALF. Morning brief ✅ (cleaned + Artemis-voice). **OKR Friday check-in ✅
  live-verified** (proactive check-in → grounded opener digest → word-dump reconcile → propose → "go" → real
  KR write + activity log + done_bullets, all approval-gated). **Remaining = the resume-after-Writing-Studio
  work:** P2b commitments (extract promises/open-loops, follow up in-window) + P2c follow-ups + Callie nudges.
- **P3 — Agency-writes:** 🟡 SUBSTRATE DONE (pulled forward). The OKR KR write IS the first real agency-write-
  behind-the-gate. Building it forced the reusable gated-write plumbing: DB-backed staging (the OKR check-in
  breadcrumb pattern, migration 0080-0082), a **deterministic** confirm classifier (no Anthropic API key on the
  Claude Code subscription — see the subscription-path note below), and server-side apply that crosses the MCP
  subprocess boundary. Remaining P3 = applying this same substrate to Gmail/Calendar/Jira/doc writes.
- **P4 — Orchestration → Callie:** 🟢 SUBSTANTIALLY BUILT AHEAD. Callie is live (C1-C3: multi-bot routing,
  dedicated `/events/callie`, marketing-scoped tools, reports only to Artemis). Remaining glue = "Ready for
  review → Callie pings Angela," which lives inside the **Writing Studio backlog** (next chunk) — so WS does
  double duty: closes the Chapter-1 content system AND lands this P4 orchestration bridge.
- **P5 — Learning loop / skill capture:** ⚪ Not started (trace-capture foundations being seeded).
- **P6 — Self-evolution (capstone):** ⚪ Committed, deliberately last; foundations laid from P1.

**Subscription-path reality (load-bearing):** Artemis runs on the Claude Code subscription with NO Anthropic
API key. Tools are served by a separate `mcp_server` subprocess that strips layer-3/4 (confirm-required) tools,
and the in-memory `confirmation_store` can't cross that process boundary. So gated actions on this surface must
stage to the DB and apply in the main process, and any "cheap LLM" helper (classifiers) must be deterministic
or use the subscription adapter — never `AnthropicAdapter`. This is the pattern for ALL future agency-writes.

**Near-term order (Jon's call 2026-06-12):** finish **Writing Studio backlog** (`docs/writing-studio-backlog.md`)
→ then **resume P2 proactivity** (commitments + follow-ups). After that the genuinely-remaining arc is P5 → P6.

### Original phased plan (as drafted 2026-06-10)
- **P1 — Slack two-way channel.** Promote Slack from send-only to inbound: events listener + DM pairing,
  bridge a Slack DM to a floating-Artemis session, continuity. *Jon's explicit want ("chat with her in
  Slack"); most self-contained; prerequisite for proactive delivery + Callie.* **Jon creates the Slack app/
  tokens; we build the bridge.**
- **P2 — Proactivity engine (the differentiator).** Durable scheduler/cron + a **commitments** layer on the
  memory keystone (extract promises + open-loops/deadlines from chat/meetings/email, classify by sensitivity,
  follow up within a window, dedupe/snooze), delivered to Slack/in-app — all through the propose→confirm gate.
  Starts with a scheduled **morning brief** (the generator already exists) and grows into true follow-ups.
- **P3 — Agency-writes.** Gmail compose, Calendar write, Jira create, doc/brief creation — each behind the
  confirm gate; reuse the Writing Studio rules for content.
- **P4 — Bounded orchestration → Callie.** Formalize delegate (track spawned agents + completion callbacks),
  then wire Callie as the first domain agent reporting up (per `agent-slack-architecture.md`).
- **P5 — Learning loop / skill capture.** Turn repeated successful procedures into named, usage-tracked,
  non-destructively-aged capabilities, grounded in the keystone.
- **P6 — Self-evolution (committed capstone).** A GEPA-style offline loop that improves an agent's own
  prompts/skills/connector descriptions from its real session traces, gated by the full test suite + an LLM
  judge + **human PR approval (never auto-commit)**. A committed part of the roadmap (the self-improving
  moat), built LAST because it optimizes an already-running agent — it needs trace history + a stable agent
  to learn from.
  - **Bake the foundations in from P1 onward** (so P6 isn't a bolt-on): capture execution traces, structure
    each Named agent's prompts/skills as discrete *evolvable units*, and stand up the test-gate + PR-review
    harness early. By P6 the substrate already exists.
  - Makes "self-improving" a property of the **Named Agent Standard**, not a one-off.

## Constraints
Do NOT copy competitor code (reference only). Keep the propose→confirm gate for anything side-effectful.
Lossless memory invariant holds. Reuse the existing engine (loop, memory, connectors, floating UI) — extend,
don't rebuild. Schedulers already exist for meetings/pipelines/marketing — extend that infra to Artemis, not
a new stack.

---

*Prepared by Artemis (Opus Lead), 2026-06-10. Living doc. (Note: the reference set's third repo is
`hermes-agent-self-evolution-main`, not a separate `hermes-automation` — flagging the folder-name mismatch.)*
