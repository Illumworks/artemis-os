# CLAUDE CODE PLANNING HANDOFF — Artemis OS / Marketing OS

**Author:** Jon Fila + Claude (web), May 2026
**Audience:** The Lead Claude Code instance opening this repo for the first time
**Purpose:** Continue an architectural planning conversation in the repo with full code context — *not* a build kickoff.

> **Read this first. Then read the four memory architecture docs in `docs/` (or wherever they live in this repo). Then audit the actual code. Then come back to Jon with a recommendation. Do not start building structural changes until Jon confirms direction.**

---

## (A) THE CONVERSATION ARC — HOW WE GOT HERE

You're joining mid-planning. The web-Claude (me) and Jon had a multi-hour conversation that revised the architectural recommendation three times as more context surfaced. The reasoning is more important than the conclusions — you may push back on any of it after reading the code.

### Where we started

Jon asked me to spec out **Artemis OS / Marketing OS** — an internal marketing intelligence + campaign workflow system at Amira Learning. The output was a 42-file build spec at `artemis-os/` (`README.md`, `PIPELINE.md`, `DB_SETUP.md`, plus `schemas/`, `services/`, `agents/`, `rulesets/`, `gates/`). Read those after this handoff. They describe a 9-scout signal pipeline → 3-phase qualifier → human Gate 1 review → content team → Writing Studio integration. The pipeline design is sound. The implementation language and storage substrate are the open questions.

### How the architecture recommendation evolved

**Round 1 — Polyglot hybrid (Node + Python + shared Postgres).** Based on Jon's initial framing that Jira and OKR Studio were "actively used" surfaces that couldn't be disturbed, I recommended keeping the existing Node/Express app for UI/Jira/Writing Studio/OKR, adding Postgres for Marketing OS pipeline tables, and running Python scout workers as a separate process writing to that Postgres.

**Round 2 — Full Python + Postgres rebuild.** Jon clarified: Jira is just API linking with no stored data, and OKR Studio data could be preserved-then-rebuilt. That dissolved the "don't touch" constraint. I reversed and recommended a full Python/FastAPI/Postgres rebuild with the frontend kept (React/Next.js) — one language, one database, simpler mental model for the AI agent team that maintains this forever.

**Round 3 — Reversed again toward Node + SQLite + sqlite-vec.** Jon then uploaded four memory architecture docs (see Section B). Reading them revealed that **the Artemis memory system is not theoretical**. It's a shipped, working, well-designed two-tier evidence-linked memory store with sqlite-vec embeddings, MiniLM model, fusion retrieval, scope unions, lossless evidence preservation, and 537 passing tests. P0/P0c/P1/P2 are shipped. P3 (graph & structural) is locked and ready. The keystone plan explicitly reserves schema hooks for Marketing OS to integrate as drawers + observations inside the existing store, not alongside it.

That changed the recommendation: **keep Node + SQLite + sqlite-vec, build Marketing OS on top of the memory keystone, don't throw away the most carefully-considered part of the codebase.** Scouts probably become Node modules. P3 ships before scouts begin.

### What's still unresolved (this is where you come in)

Two large open questions remained when this handoff was written:

1. **Is the Python question genuinely settled?** I recommend Node based on reading the keystone docs, but Jon's instinct is that Python might still be worth it for long-term scalability. Neither of us has read the actual code. The decision should be evidence-based, not preference-based. See Section C for the case for both sides and what to look at in the repo to decide.

2. **Does the existing local-first / single-user / single-binary architecture carry the "top flight" bar?** Jon's boss has explicitly stated the goal is **server deployment + multi-user after the marketing workflow is polished.** That's not a maybe — it's a near-term certainty. The keystone plan was designed for local-first / single-tenant V1 with multi-user explicitly deferred to M4+ via reserved schema hooks. Whether those hooks hold up when activated, and whether the deployment-model migration is bounded work or a major restructure, depends on what the code actually looks like.

These are the questions your audit needs to answer. Not for me, not for the keystone plan author — for Jon.

---

## (B) REQUIRED READING — BEFORE FORMING ANY OPINION

Read these in this order. Skim is not enough on the first two.

### 1. The four memory architecture docs

If they're not still in the repo, ask Jon to point you to them:

- **`PLAN-memory-architecture.md`** — 4-layer memory model (Session / Project-Domain / Agent-Workflow / Personal-Organizational), retention policy, retrieval traceability, external/runtime boundary
- **`PLAN-memory-keystone.md`** — *the load-bearing one.* Two-tier storage (Drawers + Observations + Evidence), scope-aware retrieval, fusion ranker, sqlite-vec + MiniLM, build-not-integrate Hindsight, migration strategy, durability/backup/portability. Read §3 (decisions), §4 (architecture), §5 (phased plan), §6 (schema) at minimum.
- **`PLAN-memory-keystone-p3.md`** — graph & structural layer. Entity / relation / mention schema, predicate vocabulary, extraction trigger, graph_proximity reranker. **This is what unblocks Marketing OS — read it.**
- **`MEMORY-RETRIEVAL-QUALITY-VALIDATION.md`** — actual validation run (12/15 PASS, 3/15 PARTIAL on real semantic queries) proving the retrieval works. Confirms this is shipped reality, not paper.

### 2. The 42-file Marketing OS build spec

`artemis-os/README.md` first, then `PIPELINE.md`, then `DB_SETUP.md`, then skim the rest. **This spec was written Python-shaped.** If we keep Node (Section C), the design is unchanged but the implementation files need translation. Don't translate yet — read first.

### 3. The actual repo

The audit. What you'll need to find:

- The memory keystone implementation: `server/memory-store.js`, `server/memory-embeddings.js`, `server/memory-retrieval.js`, `db/sqlite.js`, `config/memory-retrieval.json`
- The schema: `memory_drawers`, `memory_observations`, `memory_evidence`, `memory_embeddings`, `memory_scopes`, plus what P3 adds (`memory_entities`, `memory_relations`, `memory_mentions`) if shipped
- The existing Marketing OS work — Jon says some has been started. Find it. It's in `artemis-os/` or under a similar namespace. Reconcile what exists with the 42-file spec.
- The Node app structure overall: how Jira integration is wired, how OKR Studio is wired, what background-job patterns exist, how the agent / chat loop works

Your output from this reading: a written verdict (Section E checklist) on whether the foundation can carry "top flight" and where the gaps are.

---

## (C) THE PYTHON QUESTION — UNRESOLVED FORK

Jon's instinct: Python *might* be worth a restructure for long-term results, given the server-deploy + multi-user trajectory. My instinct after reading the keystone docs: keep Node, the foundation is too good to throw away. Neither of us has read the code. **You decide based on evidence, then recommend to Jon, who will lead on the call.**

### Case for keeping Node

- The memory keystone is Node + SQLite + sqlite-vec by design. The architectural choices (single-file truth, in-process embeddings, one transaction for drawer+embedding+observation, pluggable behind one module) were made specifically to fit the local-first posture. Throwing it out means throwing out the most carefully-considered architecture in the codebase.
- 537 passing tests across 13 memory suites. That's not migrate-friendly work.
- For Jon's situation (you + AI agents maintaining forever), one language wins. One repo, one virtualenv, one deployment, one mental model.
- Node is genuinely fine for HTTP, scrapers, PDF extraction (via `pdf-parse`, `pdfjs-dist`, etc.), and orchestrating agent loops. The places Python wins (data science, ML training, some scraping niches) aren't load-bearing here — most "ML-ish" work is HTTP calls to Anthropic/OpenAI which are language-agnostic.
- The keystone plan explicitly reserves multi-user hooks (`owner_user_id`, scope hierarchy). Server deployment of a Node app is well-trodden. Multi-user isn't a rewrite — it's activating the reserved hooks plus access control middleware.

### Case for switching to Python

- Python's worker / scheduler / scraper ecosystem is materially more mature. APScheduler, arq, Celery, Scrapy, Playwright (which has Python bindings as good as Node's), pypdfium2 + Tesseract for PDFs, the `httpx` async ecosystem — these are genuinely more polished than Node equivalents.
- The 42-file build spec is Python-shaped because Python is the obvious fit for what scouts do. Translating it to Node is real work, and Node implementations may end up clunkier in spots (especially PDF extraction with OCR fallback).
- **The big one:** if server-deploy + multi-user pushes Artemis past SQLite's ceiling, you're looking at a Postgres + pgvector migration *anyway*. Doing the language migration concurrent with the storage migration is cheaper than doing them sequentially. If we know the destination is multi-user server-deployed Python + Postgres, the question is *when*, not *whether*.
- The keystone plan's "pluggable later" claim is a claim. The pluggability is a written intention; whether it holds in practice depends on how clean the abstractions actually are in code. If they're tight, a future Postgres swap is bounded. If they leak SQLite-specific assumptions, the migration is much more expensive.

### What to look at in the code to decide

This is the heart of the audit. Specifically:

1. **`server/memory-embeddings.js` and surrounding** — is the vector store access really behind one module? Could `sqlite-vec` be swapped for pgvector without touching the rest of the codebase? Look for SQLite-specific assumptions leaking into call sites.
2. **Schema portability** — does the SQL use SQLite-specific features heavily (TRIGGER syntax, `INSERT OR REPLACE`, `WITHOUT ROWID`, virtual tables for FTS/vec)? Or is it mostly portable DDL with one or two SQLite-specific seams?
3. **The existing Marketing OS code** — what's been started? Is it Node-shaped, Python-shaped, or schema-only? How committed is the implementation language already?
4. **Worker / scheduler patterns in the Node app today** — is there anything? If yes, scouts can extend the pattern. If no, scouts are net-new infrastructure regardless of language, and the language choice is less anchored.
5. **How the Node app handles concurrency** — are there places that already wrestle with SQLite write serialization? That's a tell for what scale the system has been pushed to.
6. **Test coverage shape** — is the memory architecture testable in isolation, or does it cross-cut the whole codebase? Affects how surgical a future swap could be.

### What I'd want to see in your recommendation back to Jon

Not "Node" or "Python" alone. Something shaped like:

> Based on the code, I recommend [X]. The keystone abstractions [hold / don't hold] cleanly enough to swap stores later. The Marketing OS work in flight is [implementation-language] and the cost of changing direction is [estimate]. The server-deploy + multi-user migration when it comes will be [bounded migration / major restructure] under my recommendation. Here's the phased path: [phases]. Confirm direction before I proceed.

---

## (D) THE "TOP FLIGHT" BAR — JON'S WORDS

This is the design bar every architectural decision should serve. Lifted from the conversation directly:

> "I want it capable of top flight performance. Right now it's not being used except for Jira and OKR. Once we start using this for marketing I don't want to have to do major structural changes and take it down for a little bit. I want to be able to hit the ground running and be able to scale when needed."

And from his boss, separately:

> "The goal would be to server deploy and have multi users after we polish the marketing workflow."

### What "top flight" means concretely

- **Surface scale:** many agents, many rulesets, many campaign types. Marketing today, Customer Success and Sales potentially later.
- **Throughput scale:** capable of handling 10x growth without architecture being the limiter. Not "must handle 50k signals/day on launch" — but "won't choke when it gets there."
- **Org scale:** SOC2-class data handling if achievable. Multi-builder if the team grows.
- **Server-deploy + multi-user:** explicitly the near-term destination after marketing workflow is polished. Not optional.
- **Zero or minimal downtime for restructure:** once marketing relies on it, the architecture needs to carry forward without taking the system down for major changes.

### What "top flight" does NOT mean

- "Operating at top flight from day one." It means *capable of*, not *currently*.
- "Built for hypothetical future use cases that may never arrive." The use cases above are real and named.
- "Premium tech stack for its own sake." Top-flight is achievable on plenty of substrates; the question is whether the chosen one carries forward without forcing a rebuild.

---

## (E) AUDIT CHECKLIST

Specific things to verify in the actual code before recommending direction. Mark each as confirmed / refuted / unclear-and-why.

### Memory keystone claims to verify

1. **Two-tier storage is implemented as designed.** Drawers immutable. Observations link to drawers via evidence. Supersession instead of deletion.
2. **Embedding abstraction is pluggable.** All vector store access goes through one module. SQLite-specific assumptions don't leak.
3. **Scope union retrieval works.** A query against `[project:P, workspace:default, global:global]` actually unions and ranks correctly.
4. **Fusion reranker is deterministic and tunable from config.** Weights in `config/memory-retrieval.json` actually drive behavior.
5. **Lossless rule holds.** `deleteMemory` doesn't drop drawers. Consolidation preserves evidence chains.
6. **Backup / restore actually works end-to-end.** Not just "the API exists" — actually run an export, drop the DB, restore, verify counts.
7. **P3 status.** Shipped, partially shipped, or unimplemented? Affects whether scout work has its graph foundation ready.

### Marketing OS readiness

8. **What's been built already.** Tables, modules, UI components, agent stubs. Inventory it.
9. **Reconciliation with the 42-file spec.** Where does what's built match the spec? Where does it diverge? Where is the spec ahead?
10. **What language is the in-progress work in.** Anchors the Python question.

### Multi-user / server-deploy readiness

11. **`owner_user_id` columns exist on drawers and observations.** Per §3.6 of the keystone plan.
12. **Scope hierarchy supports per-user scoping cleanly.** Or does it assume single-tenant in places beyond schema?
13. **The Node app can run server-side, not just desktop.** Probably true (it's Express) but verify the assumptions about `~/.artemis/data.db` being a local path, file permissions, etc.
14. **Access control patterns.** Are there any? Or does everything assume "the user is Jon"?

### Scale ceiling check

15. **SQLite write contention with concurrent workers.** If scouts in any language hit this DB hard, how does it behave? Are there existing patterns for write queueing?
16. **Embedding volume realistic ceilings.** At 100K drawers, 1M drawers, what does retrieval p95 look like? sqlite-vec at scale.
17. **Memory archive size and portability under volume.** Backup/restore at 100K rows — minutes? hours? Acceptable?

### Tooling

18. **Test harness shape and CI setup.** What's there to enforce regressions? What needs to be added before scout work begins?
19. **Logging / observability.** Structured? Searchable? Useful for AI debugging of agent runs?
20. **Deployment automation.** What exists for production deploys? Anything?

---

## (F) OPEN QUESTIONS

From the prior handoff, plus new ones surfaced in this conversation.

### Strategic / business

1. Annual Starbridge spend — for bench-test ROI. Jon to ask Kristen / Angela.
2. Confirm priority states list — seeded `FL, IN, MD, MO, MI, IL, TX`.
3. Cross-team Starbridge usage outside Marketing.

### Writing Studio integration

4. `format_rules_id` values for `POST /drafts`.
5. Writing Studio API base URL + auth scheme.
6. Default deliverable types per campaign type.

### Technical

7. Daily emission caps per scout. Suggested default 500/day with config override.
8. LinkedIn scraper service ToS compliance. Legal sign-off before agent 1.3 production.
9. **Postgres yes/no, and if yes, when.** Was open in prior handoff as a hosting question. Now reframed: do we stay on SQLite + sqlite-vec for as long as it carries us (and migrate later), or do we fork to Postgres + pgvector earlier given the known multi-user destination?
10. Dedupe thresholds (`0.92` suppress / `0.70` emit). Tune after first 500 signals.
11. Asset Selector scoring weights. Surface as config, tune after launch.

### Lossless memory layer

12. Embedding model — keep MiniLM, or upgrade to something stronger (Voyage, OpenAI text-embedding-3-large) for Marketing OS retrieval quality?
13. Memory retention / decay policy specifics.
14. Hindsight vs. MemPalace primary inspiration — the keystone plan already settled this (build, not integrate; borrow patterns from both). Re-confirm reads accurately against the code.

### OKR Studio

15. Current data model — audit before any migration discussion.
16. Current UI surfaces — what reuses, what doesn't.

### New from this conversation

17. **Implementation language for scouts** — Node, Python, or hybrid. The fork in Section C.
18. **Server-deploy timing.** When is the marketing workflow "polished enough" to trigger the deployment-model migration? Affects how much we optimize for it now vs. defer.
19. **Multi-user identity model.** Email-based? SSO? Workspace-scoped roles? Affects schema activation of reserved hooks.

---

## (G) HOW TO COME BACK TO JON — FIRST SESSION

First session is **audit + recommendation, not build.**

### Recommended flow

1. Read this handoff. (You're doing it.)
2. Read the four memory architecture docs.
3. Read the 42-file Marketing OS build spec (`artemis-os/`).
4. Audit the actual repo against the audit checklist in Section E.
5. Form your own opinion on the Python question (Section C). Be honest about uncertainty — if the code is ambiguous, say so.
6. **Come back to Jon with a written recommendation** structured like:
   - What you found in the audit (status, gaps, surprises)
   - Your recommendation on the Python question with reasoning grounded in what you actually saw
   - Your recommendation on phased path forward (P3 first if not shipped, then scout MVP, etc.)
   - Open questions you couldn't answer from code that need Jon's input
   - What Jon should approve before you commit to direction
7. Wait for Jon's confirmation. Don't restructure ahead of that.

### Authority model

- **Lead recommends, Jon confirms** on the foundational architectural fork (Python vs Node, when to move to Postgres, server-deploy timing). This is Jon's call after hearing your reasoning.
- **Lead has autonomous authority** on everything downstream of those forks once Jon confirms direction — module layout, dependency choices, test framework, deployment patterns, sub-agent spawning, work coordination. Per Jon's words: *"I just have the goal and vision, how we get there to the best of the ability is up to you."*

### Reminders

- Don't invent values for open questions. Mark with `// TODO: confirm with Jon`.
- Don't skip the audit just because the docs look thorough. Verify against code.
- Don't restructure load-bearing existing code without Jon's sign-off on the direction.
- **Do** push back on the web-Claude's reasoning where the code shows different. I was wrong twice before being right in this conversation. I may still be wrong about something I haven't seen.

---

## (H) PERMISSION TO PUSH BACK

Explicit and important:

The web-Claude (me) recommended polyglot Option B, then full Python rebuild, then Node + SQLite + sqlite-vec — revising as more context surfaced. I was working with incomplete information each time. **The recommendation in this handoff may still be wrong** if the code shows something neither Jon nor I knew about.

When you read the code and form your own view, your job is *not* to confirm what I wrote. Your job is to figure out what's actually true. If the keystone abstractions don't hold, say so. If the Marketing OS work in flight is already Python-shaped and far enough along that switching languages is more expensive than I assumed, say so. If there's a third option neither of us considered, propose it.

Jon's working style trusts the AI agents to think honestly. Don't soften disagreements to seem aligned. If you genuinely think the Python fork is the right call after reading the code, recommend it. If you think Node is the right call, recommend it. Either way, **show the reasoning grounded in what's actually in the repo**, not in what this handoff suggests.

---

## (I) MULTI-AGENT OPERATING MODEL

Once Jon confirms direction (after your audit + recommendation), the agent-team operating model from the prior handoff applies:

- **You as Lead.** Hold architecture, plan phases, coordinate, talk to Jon. Own the project log and the test harness.
- **Build sub-agents** spawned per discrete component with goal / spec / success criteria / out-of-scope / report-back brief.
- **Validation sub-agent** runs after every build sub-agent merge. Executes tests, smoke tests, schema-contract checks. Can re-spawn build sub-agents to fix failures.
- **Memory keeper discipline.** Maintain `PROJECT_LOG.md` at the repo root. Every spawned sub-agent reads it. Every completed task adds an entry.

**The test harness is a Phase 0 mandatory deliverable.** Without it, multi-agent workflow degrades to chaos fast.

Escalate to Jon at: phase boundaries, frontend UX decisions, third-party API decisions (keys, vendor selection), open-question-becomes-blocker moments, and pattern-of-failures-suggests-spec-flaw moments. Don't escalate for permission to do the work he asked you to do.

---

## (K) TWO-SEAT OPERATING MODEL — COORDINATION, NOTIFICATIONS, COMMUNICATION

Jon has two Claude Max seats and a target of completing the MVP in two weeks. This section is the playbook for using both seats without the failure mode Jon explicitly named: *"developed too fast without the right conversations taking place during key moments."*

### K.1 Seat architecture

- **Account 1 — Lead Claude Code.** Architecture, planning, project log, merge authority, conversation with Jon. Always-on across the two weeks.
- **Account 2 — Build/Validate Worker.** Spawned tasks with specific briefs. Works on feature branches. Reports back via PR. Owns the test harness execution once it's built.

Both sessions read the same git repo. Both sessions follow the same coordination protocol. The Lead has merge authority; the Worker proposes via PR.

### K.2 Three-channel coordination

Three artifacts. Each does one job. Both sessions read and write all three.

**`COORDINATION.md`** at repo root — **real-time visibility.**
- Updated continuously through the day
- Sessions read it on startup and before any task that touches shared territory
- Format: dated entries, session author, what's being worked on, when expected complete, any locks ("don't touch `db/sqlite.js` until I finish")
- Append-only within a day; archived to `coordination-archive/` weekly

**Pull Requests** — **key conversation moments.**
- Worker works on feature branches, never commits directly to main
- Every meaningful unit of work lands as a PR with a substantive description: what was done, why this approach, what was considered and rejected, what's not in scope, what questions came up
- Lead reviews PRs as a real engineering review — reads the reasoning, pushes back where something is off, engages in PR comments
- Merge happens after alignment, not by default
- The PR description is the artifact that makes "key conversation moments" actually happen

**`PROJECT_LOG.md`** at repo root — **historical state.**
- Append-only
- Every meaningful decision logged with timestamp, session author, decision, reasoning
- Both sessions reference it for context on startup
- This is what carries continuity across session restarts and account swaps

### K.3 Trigger events — when sessions must stop and converse

These are the moments where the failure mode Jon named tends to happen. At any of these, the session that hits the trigger **stops** and writes a structured proposal to `COORDINATION.md`, pings the other session, and waits for explicit alignment before proceeding.

1. **Schema changes.** Adding tables, adding columns to memory keystone tables, modifying constraints.
2. **Anything touching the lossless evidence rule.** Drawer mutation, evidence-chain modification, consolidation that could orphan provenance.
3. **Cross-cutting refactors.** Anything that affects both backend and frontend, or touches files outside the current task's clear scope.
4. **Direction locks.** Choices that are hard to reverse — language fork, vector store, deployment model, auth scheme.
5. **Assumption mismatches.** One session realizes the other session's assumption (in code or in `PROJECT_LOG.md`) is wrong.
6. **Spec divergence.** Code is being written that doesn't match the 42-file spec or the memory keystone docs, even if for good reason.

Sessions are responsible for *recognizing* these moments. The discipline is: when in doubt, treat it as a trigger and check in. Over-pausing is recoverable; under-pausing is the failure mode.

### K.4 Branch discipline

- `main` is the integration branch. Only the Lead merges to main.
- The Worker works on feature branches: `worker/<scope>-<short-desc>`. Example: `worker/scout-legislative-v1`.
- The Lead works on feature branches for substantive changes: `lead/<scope>-<short-desc>`. The Lead can commit directly to main for `COORDINATION.md` and `PROJECT_LOG.md` updates.
- PRs target main. Both sessions can open PRs; the Lead reviews and merges (including, sometimes, its own PRs after a self-review pass).
- The Worker does not merge to main without Lead approval.

### K.5 Notifications to Jon — push, not pull

Jon does not want to scan `COORDINATION.md` to find questions. Questions get pushed to Slack.

**Channel:** dedicated Slack channel or DM-to-self in a Slack workspace (Amira workspace or a personal workspace — Jon's choice). Set up via incoming webhook.

**Setup (Phase 0 task for Lead):**
1. Create Slack channel `#artemis-build` (or equivalent)
2. Add "Incoming Webhooks" Slack app to the channel
3. Generate webhook URL
4. Store as `JON_NOTIFICATION_WEBHOOK` in `.env`
5. Build `scripts/notify-jon.{js|py}` helper that both sessions can import
6. Function signature: `notify_jon(urgency, plain_english_summary, technical_detail, context_link, proposed_default=None, blocking=False)`

**Three urgency levels, three patterns:**

| Symbol | Level | Behavior |
|---|---|---|
| 🔴 | BLOCKING | Session pauses. Will not proceed until Jon responds. Tags him in Slack. |
| 🟡 | QUESTION | Session can proceed on `proposed_default` if Jon doesn't respond within a stated window. Posted with reasoning and proposed default. |
| 🟢 | FYI | No response needed. Used for end-of-day digests, completion notifications, status updates. Batched where possible. |

**Every Slack post follows this shape:**

```
🟡 [Lead, Account 1] Question — proceed on default in 30 min unless redirected

PLAIN-ENGLISH: Found that the memory system's "swap the database
later" claim doesn't hold as cleanly as the docs said. We can fix
it now (4-6 hours) or accept the rough edge and pay a bigger cost
when we migrate later. I think fixing now is right because we
know server-deploy/multi-user is coming.

PROPOSED DEFAULT: Fix now. Refactor the 14 direct SQLite call sites
into a single MemoryStore abstraction. ~4-6 hours, no schema change.

🔗 Full reasoning: <link to PR or COORDINATION.md entry>

REPLY OPTIONS:
- "OK fix now" → proceed
- "Leave it" → log in PROJECT_LOG.md as accepted debt, proceed with scout work
- "Explain like I'm not a coder" → I'll re-explain
- "Talk it through with me" → I'll switch to Socratic mode
- "Just pick — what would you do?" → I make the call and proceed
```

### K.6 Communication preferences — how questions arrive at Jon

> When you explain a technical decision to me, lead with the consequence, not the mechanism. Use real-world metaphors where you can. Don't worry about precision in the first explanation — I'll ask for it if I need it. I'd rather understand 80% deeply than 100% shallowly. I trust your recommendation; I want to understand what I'm signing off on, not check your math.

Treat this as a permanent communication contract. Five rules that follow from it:

1. **Plain-English always leads.** Never post a technical question without a plain-English summary on top. Technical detail goes below for if Jon wants it.
2. **Lead with consequence, not mechanism.** "We'll have to take the system down for a weekend later" is the consequence. "The connection pool isn't wrapped in a transaction context manager" is the mechanism. The first lands; the second loses Jon.
3. **Use metaphors.** Filing cabinets, libraries, kitchens, sports — whatever fits. Metaphors make abstract trade-offs concrete.
4. **Recommend, don't just present.** Always offer a proposed default with reasoning. Don't put a neutral menu in front of Jon and expect him to choose blind.
5. **Confirm understanding before proceeding.** After Jon decides, restate the decision in plain English: *"OK — confirming I heard you: we're fixing the 14 seams now, accepting the 4-6 hour cost, still on track for two weeks. Logging and proceeding."* If Jon corrects, re-confirm. Don't proceed on a maybe.

### K.7 The three escape hatches — always available to Jon

Recognize these as commands, not failures. When Jon replies with any of them, switch modes accordingly.

- **"Explain like I'm not a coder"** → re-explain in everyday terms. Use the metaphor toolkit. Don't restate the question with simpler vocabulary; re-explain the *situation* in everyday terms.
- **"Talk it through with me"** → stop trying to extract a decision. Switch to Socratic mode. Ask Jon guiding questions. Help him understand the trade-off well enough to decide, without rushing him. This is the same pattern the web-Claude used in the conversation that produced this handoff.
- **"Just pick — what would you do?"** → make the call. Explain reasoning in two sentences. Proceed. Log in `PROJECT_LOG.md` so it's reversible. This is a valid answer; the agent should be ready to take the call when Jon delegates.

These three are non-negotiable. If a session ever responds to one of them with "well I can't decide that for you" or "you really need to weigh in on this" without genuine cause, that's a protocol violation. The escape hatches exist because Jon's job is the goal and vision, not the math.

### K.8 Big decisions — out of Slack into longer conversation

For real architectural forks (language choice, deployment model, schema commitments), Slack is too small. The pattern:

1. Session writes a longer brief in `COORDINATION.md` or a dedicated `decisions/<topic>.md` file
2. Session pings Jon in Slack with: *"Big decision coming. I've written it up. Want to read first, or have me walk you through it in chat?"*
3. If Jon picks "walk me through it," session switches to conversational mode — exactly the pattern of the web-Claude conversation that produced this handoff. Explains, takes questions, restates, proposes, gets sign-off.
4. Decision lands in `PROJECT_LOG.md` with timestamp, decision, reasoning, and "what reversing this would cost" if relevant.

The session does the teaching work so Jon's judgment can apply. Jon never has to learn topics he doesn't want to learn — he has to understand consequences well enough to choose.

### K.9 Daily rhythm

**Start of each session (both Lead and Worker):**
- Read `COORDINATION.md` — what's the other session doing, what's locked
- Skim `PROJECT_LOG.md` recent entries — any decisions made since I was last on
- Skim open PRs
- Check Slack — any pending questions Jon answered overnight

**During work:**
- Update `COORDINATION.md` when entering new territory or locking files
- Open PRs as work completes; substantive descriptions
- Notify Jon via Slack only at trigger events or genuine questions — not for routine progress
- Recognize trigger events; pause and converse when one fires

**End of each session:**
- Update `PROJECT_LOG.md` with the day's decisions and progress
- Close out any temporary locks in `COORDINATION.md`
- Post 🟢 FYI digest to Slack: what got done, what's queued for tomorrow, any open questions
- Hand off cleanly so the next session (same or other account) can pick up

### K.10 Escalation rules — when sessions disagree, when to call Jon

**Sessions disagree on direction:**
1. First exchange in `COORDINATION.md` — Worker proposes, Lead pushes back, Worker responds.
2. Second exchange — if not converged.
3. After two exchanges with no convergence: escalate to Jon. Don't argue in circles. The structure: brief Slack message stating *"Lead and Worker disagree on X. Here's the Lead's view in one sentence. Here's the Worker's view in one sentence. I (Lead) propose we go with [X]. Confirm or override."*

**Pattern across sub-agent failures suggests spec flaw:**
- Don't paper over. Surface the pattern. *"Three sub-agents have hit the same friction with the qualifier schema — I think there's a spec issue. Want me to write up what I'm seeing?"*

**Open question becomes a blocker:**
- One of the 18 open questions in Section F now genuinely prevents progress. Escalate with the question and the smallest possible decision needed to unblock.

**Anything that would touch the lossless rule or memory keystone tables in a way that's structural:**
- Always escalate. These are foundational. Don't refactor them without explicit sign-off.

### K.11 Realistic two-week scope — what's in, what's out

**In scope (the MVP cut):**
- Phase 0: Audit, direction confirmation (Python question), test harness, Slack notification setup, `COORDINATION.md` + `PROJECT_LOG.md` rhythm established
- Phase 1: P3 of the memory keystone (graph & structural) shipped if not already
- Phase 2: One scout end-to-end (Legislative Scout recommended — easiest, validates the pattern)
- Phase 3: Cross-Reference Agent (Qualifier) with one ruleset (OBC) seeded
- Phase 4: Brief Composer Agent + Signals Inbox UI (minimal viable — list view, approve/reject/snooze/ask, structured rejection reasons)
- Phase 5: Real signals flowing into Josh's queue. Validation that the workflow feels right.

**Explicitly out of scope (deferred past two weeks):**
- All 9 scouts (only one in MVP)
- All 4 rulesets (only OBC in MVP)
- Content team (5.1 / 5.2 / 5.3)
- Writing Studio integration
- OKR Studio migration / rebuild
- Server deployment + multi-user activation
- Frontend polish beyond minimal viable Signals Inbox
- Ruleset Manager Agent chat panel (P2.2 — surface the YAML edits manually for now)

If at end of week 1 Phase 0+1 isn't complete, **stop and reassess scope with Jon**. Don't push through and ship something half-built.

### K.12 Session-handoff protocol (for swapping accounts mid-build)

If Jon needs to swap which account is the active Lead (e.g., one account hits weekly limits):

1. Active session writes a comprehensive entry in `PROJECT_LOG.md` titled `## SESSION HANDOFF [timestamp]` covering: current state, what's in flight, what was just decided, what to do next, any open questions
2. Active session updates `COORDINATION.md` to mark the handoff
3. Posts 🟢 FYI to Slack: *"Handing off Lead role to Account 2. State captured in PROJECT_LOG.md handoff entry."*
4. New session reads the handoff entry as its first action, then resumes

The project log is the bridge. Session continuity does not depend on chat history surviving.

---

## (J) CONTEXT FOR THE FLOATING ARTEMIS AGENT

If the floating Artemis agent has its own context surface, carry across:

1. We're mid-planning, not mid-build. The Lead's first job is audit + recommendation, then Jon confirms direction.
2. The memory keystone (`PLAN-memory-keystone.md` etc.) is shipped reality, not paper. Treat it as authoritative for what exists.
3. The "top flight" bar is: capable of scale, server-deploy + multi-user near-term destination, zero major restructure once marketing relies on it.
4. The 42-file build spec at `artemis-os/` is Python-shaped but the implementation language is unresolved.
5. Jon leads on foundational forks (Python vs Node, when to migrate storage, server-deploy timing). Lead has autonomous authority on execution after direction is confirmed.
6. The two-seat operating model in Section K — `COORDINATION.md` + PRs + `PROJECT_LOG.md` + Slack notifications — is the protocol both Claude Code accounts follow. The floating Artemis agent should observe these channels but not write to them unless explicitly invited; its job is to read for context, not coordinate the build.
7. Jon's communication preferences in K.6 apply to the floating agent too. Lead with consequence. Use metaphors. Don't post technical mechanics as the lead message.

---

**End of handoff. Jon — point the Lead at this file first. Then point it at the four memory architecture docs. Then let it audit. Then have the planning conversation that turns audit findings into committed direction. The build starts after that.**
