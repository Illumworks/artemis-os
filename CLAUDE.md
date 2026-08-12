# CLAUDE.md — Artemis OS (Python rebuild)

Guidance for Claude Code / Codex / the floating Artemis when working in this repo.

## What this repo is

**Artemis OS** — Jon Fila's marketing-intelligence + campaign-workflow system at Amira Learning. Python rebuild of the Node prototype at `../claudeck-artemis/`.

This is **his** app, not a fork of Claudeck. The previous repo carried inherited DNA; this one starts clean.

## Reference implementation

The previous Node app at `/Users/artemis/Desktop/Artemis/claudeck-artemis/` is **frozen as a reference**, not a build target. Read it when you need to understand behavior the new app should match. Do not edit it. Do not run it concurrently.

Key reference paths in the Node app:
- `db/sqlite.js` memory keystone sections — schema and write paths
- `server/memory-*.js` — store, retrieval, consolidation, embeddings, graph extractor
- `server/signal-qualifier.js` — pure deterministic scoring
- `server/scout-intake.js`, `server/routes/scouts.js` — scout harness
- `server/campaign-brief-assembler.js`, `server/writing-studio-*.js`
- `docs/PLAN-memory-keystone.md` and `docs/PLAN-memory-keystone-p3.md` — the design docs
- `marketing-ops-v1/` — the 42-file build spec (markdown, design only)

Coordination + decision docs that govern both repos live in the Node repo:
- `../claudeck-artemis/COORDINATION.md` — real-time Lead / Worker visibility
- `../claudeck-artemis/PROJECT_LOG.md` — historical decisions
- `../claudeck-artemis/decisions/artemis-python-rebuild.md` — why this repo exists
- `../claudeck-artemis/decisions/rebuild-phased-plan.md` — the build plan
- `../claudeck-artemis/CLAUDE_CODE_PLANNING_HANDOFF.md` — authoritative context

Read these before doing anything substantive.

## Stack

- Python 3.11+
- FastAPI for HTTP
- Postgres 15+ with pgvector for embeddings
- SQLAlchemy 2.x async + asyncpg
- Pydantic 2.x for schemas
- Alembic for migrations
- pytest + httpx async client for tests
- ruff (lint + format) + mypy strict for code quality
- uv for dependency management
- Anthropic Python SDK for Claude calls (prompt caching from day one)

## Operating rules

1. **Local-only git.** Never push to remote. All branches and commits stay local. The "conversation moment" artifact is the commit message + a `COORDINATION.md` entry in the Node repo, not a GitHub PR.

2. **Autonomy.** Operate without per-change approval. Surface to Jon only for: big architectural forks, Creative Director judgment (UX / naming / visual / brand), cutover moments, anything touching OKR Studio rows or Writing Studio rules, pattern-of-failures / spec-flaw moments.

3. **Lossless memory rule.** Drawers and evidence are never deleted. Observations are removed from active retrieval only via supersession (`superseded_by`), never via DELETE. There is no public `delete_drawer` or `delete_observation` API.

4. **Dependencies.** Never add or upgrade a dependency to a version released less than 7 days ago. Exception: direct response to a known CVE, documented at the point of upgrade. Applies to all dependency types — Python, Docker base images, GitHub Actions if we ever add them. The lockfile (`uv.lock`) must reflect the same constraint when regenerated.

5. **Tests are not optional.** Same discipline as the Node reference: >85% backend coverage, 100% on keystone-class modules. Run `./scripts/check.sh` before opening any branch for review.

## Local dev quickstart

**Prerequisites (one-time, brew-native).** This Mac mini is set up with Postgres running natively, not in Docker:

```bash
brew install uv postgresql@17 pgvector
brew services start postgresql@17
createuser -s artemis
createdb -O artemis artemis_os
psql -d postgres -c "ALTER USER artemis WITH PASSWORD 'artemis';"
psql -d artemis_os -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

(`docker-compose.yml` is kept as an optional alternative for contributors who prefer containers, but the brew path is the supported one on this machine.)

**Dev loop.**

```bash
# 1. Install Python deps (creates .venv automatically)
uv sync

# 2. Run migrations
uv run alembic upgrade head

# 3. Run the app
uv run uvicorn artemis.main:app --reload

# 4. Run tests
uv run pytest

# 5. Run full checks (lint + type + test)
./scripts/check.sh
```

## Operational visibility — READ THIS BEFORE DIAGNOSING ANYTHING

**Start here, always:**

```bash
uv run python -m artemis.ops
```

One consolidated health report: service/process state, per-agent activity, the
marketing funnel, in-flight pipeline runs, and derived findings. Read-only, safe
against prod, and works even when the app is down (it only needs Postgres).

**Why this exists — the trap it prevents.** Agent activity is written to **six
unrelated stores**, and reading any single one gives a confidently wrong answer:

| Store | Records |
|---|---|
| `floating_artemis_messages` | conversational turns only |
| `morning_brief_deliveries` | scheduled briefs / OKR check-ins |
| `memory_observations` (`category='callie_signal_push'`) | Callie's autonomous signal cards |
| `agent_traces` | any provider call |
| `slack_inbound_messages` | keyword-mention triage **only** — not DMs |
| `pipeline_runs` | pipeline executions |

On 2026-08-10 a session read the first store, found nothing for Artemis since
2026-07-21, and reported that she had been down for 20 days — while she was
delivering the morning brief every single weekday and Callie was pushing signal
cards daily through two other stores. **An agent is alive if ANY path is recent.
Never judge liveness from one table.**

**Logging.** `artemis/logging_setup.py` wires `settings.log_level` into Python's
logging module from `main.lifespan`. Before it existed, that setting was defined
and set in `.env` but consumed by nothing, so every `logger.info`/`logger.debug`
in the codebase was silently discarded in production and the app emitted ~6 log
lines a day. Two rules:

- Do not remove the `configure_logging()` call in `main.lifespan`.
- Keep it **additive** — it must never strip root's handlers (`dictConfig` does),
  or pytest's `caplog` goes blind in the 27 test modules that rely on it.

To trace a Slack message end to end (every arrival and every drop decision logs
at INFO):

```bash
grep "slack event" ~/Library/Logs/artemisos/app.err.log | tail -20
```

**A wedged pipeline run is silent.** A run left in `awaiting_approval` /
`running` blocks every future scheduled run of that pipeline — no error, no
alert, the pipeline just stops. `marketing.main` sat wedged from 2026-06-06 for
two months without anyone noticing. The health report flags these as `!!`.

## Module layout

```
artemis/
├── __init__.py
├── config.py            # pydantic-settings; single source of env truth
├── db.py                # async engine + session + Base
├── main.py              # FastAPI app entrypoint
├── routes/              # HTTP endpoints
│   └── health.py
├── memory/              # keystone — populated in Phase B
│   └── __init__.py
└── (more modules land per phase)
```

## Where in the plan are we?

See `../claudeck-artemis/decisions/rebuild-phased-plan.md` for the current phase. As of the initial scaffold:

- **Phase A — Scaffolding:** in progress (this commit).
- Phase B Slice 1 (memory storage + write path): briefed for Worker pickup once Phase A lands.

## Branch convention

- `lead/<scope>-<short-desc>` — Lead branches.
- `worker/<scope>-<short-desc>` — Worker branches.
- `main` — local integration. Lead merges; Worker proposes via diff.

## Passing tests are not evidence the thing works (lesson, 2026-08-12)

Four bugs reached production on the crisis-content pipeline in two days. **Every one had
passing tests.** The tests verified the code did what the brief said, against data the test
itself invented. Nothing checked that the invented data resembled production.

| Bug | What the test did instead |
|---|---|
| **Nobody could approve anything.** Identity resolved from `directory_people`, where all four real approvers have `slack_user_id = NULL`. Every lookup missed, authorization failed closed, every click was refused. | Seeded `directory_people` **with** Slack ids. |
| **A rejected click destroyed a live post.** `_post_ephemeral` POSTed to `response_url` without `replace_original: false`; Slack replaced the whole message and the copy was lost from the channel. | Asserted the endpoint's HTTP response body. The damage happened in a separate outbound POST no test observed. |
| **A thread reply was silently dropped.** Thread→card mapping needs `message_ts`; rows created before that column existed have NULL. | Always seeded `message_ts` populated. |
| **A re-fired card's buttons were dead** — it told the approver to re-review, then answered "Already decided". | Tested transitions and click-handling separately, never the click-on-a-refired-card path. |

Two rules follow.

**1. Seed the shape production actually has.** Before writing a test for a DB read, query the
live table and check what is actually in it. Nullable columns added by a migration are NULL on
every pre-existing row, forever — `crisis_content_notifications` still has rows with NULL
`channel_id`/`message_ts` today. A fixture that populates every column tests a database you do
not have.

**2. A mocked side effect proves the call was made, not that it does what you think.** For any
outbound API whose semantics you have not read: go read them. `replace_original` defaulting
the wrong way cost a post's copy in front of an external vendor, and Slack's docs do not even
pin that default. Where an assumption cannot be verified from documentation, say so at the call
site.

Corollary worth its own line: **a fail-closed path that cannot distinguish "not permitted" from
"I could not look you up" reports a permissions problem for what is a data problem.** The
approval bug above sent everyone hunting the allowlist. Fail closed, but say which.

This is a brief-writing failure more than a coding one — specify the production data shape, not
just the intended behaviour.

## Multi-Agent Handoff Protocol

### Commit Discipline

Run `git diff --staged` before every commit that touches file renames or moves, and confirm the staged hunks match what you intended. On 2026-05-18 a migration renumber landed as two commits where `git mv` recorded the rename but the corresponding `Edit` changes to the file's `revision`/`down_revision` strings were never staged — HEAD ended up with three migration files all claiming `revision="0017"`, a broken alembic chain that worked locally only because the unstaged working-tree content was correct. `git diff --staged` would have caught it in two seconds. Apply the same reflex to any commit that mixes a rename with a content edit (renamed module + import-path fixup, moved file + path-string update, etc.).
