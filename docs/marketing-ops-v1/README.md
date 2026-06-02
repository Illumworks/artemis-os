# Artemis OS — v1 Build Spec

This folder contains the v1 specification for **Artemis OS**, Amira Learning's internal marketing intelligence and campaign workflow system. It is the build target for the first round of implementation.

## What Artemis OS does in v1

Artemis OS automates the workflow from **public-record signal detection** through **brief-and-asset preparation for Writing Studio**. It does not send outreach in v1. Final draft generation lives in Writing Studio (an existing production system owned by Marketing).

## Pipeline at a glance

```
[Scout team]          [Qualifier team]                [Human Gate 1]         [Content team]            [External]
 9 scouts        →    3 phases + brief composer  →    Signals Inbox     →   3 components      →     Writing Studio
 Detect signals       Score + route + draft brief     Josh / Angela          Brief + assets           Drafts + approval
                                                       approve
```

Full pipeline detail: see [`PIPELINE.md`](./PIPELINE.md).

## How to navigate this folder (for Codex)

Start here, then read in this order:

1. **`PIPELINE.md`** — end-to-end flow and handoff contracts between teams.
2. **`DB_SETUP.md`** — PostgreSQL schema, table DDL, migrations. Stand this up before building anything else.
3. **`schemas/`** — shared data contracts every agent reads or writes. Stable; read these before any agent file.
4. **`services/`** — shared infrastructure (signal queue, memory layer, ruleset storage, contact DB stub, territory config, PDF extractor). Build these next; agents depend on them.
5. **`agents/`** — one file per agent, grouped by team. Each file is self-contained: purpose, I/O, tools, prompts, failure modes, DB tables touched, implementation notes.
6. **`rulesets/`** — seed ruleset YAML for OBC, biliteracy, dyslexia.
7. **`gates/`** — human review surfaces (Gate 1 inside Artemis; Gate 2 reference only — owned by Writing Studio).

When generating code for any single agent, load: that agent's file + the schemas it references + the services it calls. Do not load the whole folder.

## Scope decisions for v1 (read these before building)

These are explicit choices that shape the build. Override at your own risk.

### Out of scope for v1

- **Contact team / contact enrichment.** Dropped per PM direction — too many open DB cleanup questions. Replaced with a stub service that returns `True` for any priority district (see `services/contact-db-stub.md`). The full Contact team design exists in the broader Artemis OS canvas and will land in a later version.
- **Compliance team.** Dropped for v1. Brand voice enforcement happens inside Writing Studio (which owns trained brand voice memory). Artemis OS does **input validation** on the Campaign Brief before sending it to Writing Studio — this is deterministic hygiene, not compliance.
- **Outreach / send orchestration.** v1 ends at Writing Studio approval. There is no "send" step inside Artemis. Approved deliverables sit in Writing Studio's queue for humans to use.
- **Track / Learn loop.** No performance feedback loop in v1. Add later.
- **LinkedIn Observer Mode A (Follower Digest).** Mode A's output went to the Contact team's enrichment queue, which doesn't exist in v1. Mode A is disabled for v1; Mode B (Leader Monitor) is the only active mode of agent 1.3.

### In scope for v1

- Scout team (all 9 agents): 1.1 Starbridge Researcher, 1.2 Regional News Scout, 1.3 LinkedIn Observer (Mode B only), 1.4 Legislative Scout, 1.5 Federal Funding Scout, 1.6 State DoE Scout, 1.7 Procurement Scout, 1.8 Board Minutes Scout, 1.9 Leadership Transition Scout.
- Qualifier team: 2.1 Cross-Reference Agent (3 phases), 2.2 Ruleset Manager Agent, 2.3 Ruleset Compiler, 2.4 Brief Composer Agent.
- Gate 1: Signals Inbox (Josh / Angela approve / reject / snooze / ask).
- Content team: 5.1 Campaign Brief Assembler, 5.2 Asset Selector Agent, 5.3 Writing Studio Adapter.
- Three rulesets seeded: OBC, biliteracy, dyslexia.

## Design principles for the build

1. **Deterministic where possible.** LLM calls are expensive and non-reproducible. Components labeled "deterministic" in their files use database queries, scoring math, and rule evaluation — no LLM. Reserve LLM calls for the points where they add real value (qualitative rubric evaluation, signal-to-brief translation, prompt scaffolding inside scout agents).
2. **One contract per artifact.** Every artifact passed between teams has a versioned schema (in `schemas/`). Agents read and write to those contracts; they do not invent fields.
3. **Append-only storage for rulesets and signals.** Never overwrite. Versioning preserves auditability and supports rollback.
4. **Fail loud, not silent.** When a scraper breaks, an API rate-limits, or a PDF extraction fails — surface to the user, do not silently skip. Missed signals have real revenue cost.
5. **Codex builds; humans review.** This spec is the build contract. Where something is a judgment call (e.g. exact dedupe threshold), it's marked with `// JUDGMENT CALL:` so a human can tune.
6. **Mark unconfirmed details, never invent.** Where I couldn't read content cleanly from the canvas screenshots, files contain `// TODO: confirm from canvas` markers. Do not generate code against TODO values — surface them for human confirmation first.

## Tech stack (specified for Codex)

- **Language:** Python 3.11+
- **Database:** PostgreSQL 15+ with JSON / JSONB columns
- **LLM:** Anthropic Claude (claude-sonnet-4-20250514 for agents; claude-haiku-4-5 for high-volume classification)
- **Queue:** PostgreSQL-backed (table `signal_queue` with `status` column). Do not introduce Redis / RabbitMQ in v1.
- **Scheduling:** Cron or APScheduler. Each scout has a cadence defined in its agent file.
- **HTTP:** `httpx` for API calls. `playwright` for scraping where APIs are unavailable.
- **PDF extraction:** `pypdfium2` + `pytesseract` for OCR fallback.
- **Embeddings:** OpenAI `text-embedding-3-small` for dedupe similarity checks. (Cheap and good enough; no need for higher-tier embeddings.)
- **Secrets:** `.env` file, never committed. Required keys listed per-agent in implementation notes.

## Building order (suggested)

1. `DB_SETUP.md` — provision the database.
2. `services/` — all of them. They are dependencies.
3. `schemas/` — define types / Pydantic models matching every schema.
4. `agents/scout/1.4-legislative-scout.md` — easiest scout, validates the pattern end-to-end.
5. `agents/scout/1.5-federal-funding-scout.md` — second scout, validates that the pattern scales.
6. **STOP and validate end-to-end with two scouts before building more.** Run Cross-Reference Agent against real signals from these two scouts before continuing.
7. Build remaining scouts.
8. Build Qualifier team (2.1, 2.2, 2.3, 2.4).
9. Build Signals Inbox (Gate 1 UI).
10. Build Content team (5.1, 5.2, 5.3).
11. Wire Writing Studio Adapter to the real Writing Studio API.

## What is NOT in this folder

- UI mockups for Signals Inbox or any internal surface. Build the data model; UI is a separate workstream.
- Writing Studio internals. Writing Studio is an external system. We document the API contract (POST /drafts) and what we send / expect back. The rest is Angela's domain.
- Deployment infrastructure (Docker, CI/CD). Out of scope for build spec.
