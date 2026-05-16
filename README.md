# Artemis OS

Marketing-intelligence + campaign-workflow system for Amira Learning.

Python rebuild of the prototype at `../claudeck-artemis/`. See `CLAUDE.md` for build context, operating rules, and quickstart.

## Quickstart

```bash
docker compose up -d
uv sync
uv run alembic upgrade head
uv run uvicorn artemis.main:app --reload
```

Health check: <http://localhost:8000/healthz>

## Status

Phase A scaffolding. See `../claudeck-artemis/decisions/rebuild-phased-plan.md`.
