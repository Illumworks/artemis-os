# AGENTS.md — Artemis OS (Python rebuild)

Guidance for Codex / Codex / the floating Artemis when working in this repo.

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
- Anthropic Python SDK for Codex calls (prompt caching from day one)

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
