# J7 — Daily Brief backend port (Node → Python)

**Owner:** Worker (Sonnet)
**Scope:** ~450 LOC backend. Estimated: half-day.
**Depends on:** J1 Slack integration (DONE — `slack_inbound_messages` exists), J2 GCal (DONE), J5 Jira (DONE), OKR (DONE), M1 memory (DONE — `raw_inputs`). J8 (Slack signals) runs in parallel; **fall back to `None` if not yet shipped, don't block on it.**
**Why Jon wants this:** Focus rail's hero card. Frontend already calls `GET /api/daily-brief` + `POST /api/daily-brief/generate` (`public/js/core/api.js:480-489`); both currently 404. The Node app had a full implementation that never crossed the rebuild line. This brief ports it.

## Reference implementation (read first, do not edit)

- `/Users/artemis/Desktop/Artemis/claudeck-artemis/server/brief-generator.js` (272 LOC)
- `/Users/artemis/Desktop/Artemis/claudeck-artemis/server/routes/daily-brief.js` (46 LOC)

The Node version pipeline:
```
gather 7 sources → build ~800-token context string → Haiku completion → parse JSON → persist snapshot
```

## What you're building

### 1. Migration `alembic/versions/00XX_brief_snapshots.py`

(Pick the next free revision — check `alembic heads`. As of writing it's `0019`, so you'll likely be `0020`.)

```sql
CREATE TABLE brief_snapshots (
  id              BIGSERIAL PRIMARY KEY,
  brief_json      JSONB NOT NULL,                -- the parsed brief object (headline/priorities/etc.)
  sources_json    JSONB NOT NULL,                -- {sources: [...], contextTokens: int}
  model           TEXT NOT NULL,                 -- e.g. 'claude-haiku-4-5-20251001'
  tokens_input    INTEGER,
  tokens_output   INTEGER,
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_brief_snapshots_generated_at ON brief_snapshots(generated_at DESC);
```

Reversible. Verify with `alembic downgrade -1 && alembic upgrade head`.

### 2. Module `artemis/brief/`

```
artemis/brief/
  __init__.py
  models.py         # ORM: BriefSnapshot
  repository.py     # save_brief_snapshot, get_latest_brief_snapshot, list_brief_snapshots(limit=7)
  sources.py        # async _gather_sources() — uses existing overview functions
  prompt.py         # _build_context_string + _build_prompt (mirror Node logic)
  generator.py      # generate_brief() + get_latest_brief()
```

Keep modules small. `sources.py` should be the **thin** glue layer that calls existing overview helpers — DO NOT re-implement Jira/GCal/OKR overview logic. Reuse `artemis.routes.meetings.get_meetings_overview`-style aggregators that already exist.

### 3. Sources to gather (port the Node `_gatherSources` shape)

| Source | Python entry point | Fallback if unavailable |
|---|---|---|
| Jira | `artemis.routes.jira.get_jira_overview` (or its underlying helper) | `None` |
| Calendar | `artemis.routes.calendar.get_calendar_overview` | `None` |
| Slack signals | `GET /api/slack/signals` if J8 shipped, else `None` | `None` |
| OKR | `artemis.routes.okr.get_okr_overview` | `None` |
| Recent sessions | `artemis.routes.sessions.list_sessions(limit=8)` (Node had `listSessions(8)`) | `[]` |
| Memory hits | M1 — `artemis.memory.observations.search(query="work priorities focus today", limit=6)` if that API exists, else `[]` | `[]` |
| Previous brief | `repository.get_latest_brief_snapshot()` | `None` |

Use `asyncio.gather(*, return_exceptions=True)` so one source failing doesn't tank the whole gather (mirror Node's `Promise.allSettled`).

### 4. Context builder (`prompt.py`)

Port `_buildContextString` from the Node file verbatim in structure — same section headers (`## Yesterday's brief`, `## Recently worked on`, `## Jira`, `## Calendar today`, `## Slack`, `## OKR status`, `## Relevant memory / context`). Same truncation limits (6 sessions, 4 Jira in-prog, 3 review, 2 blocked, 5 calendar events, 2 objectives × 3 KRs, 4 memory items × 120 chars).

### 5. Prompt + LLM call

Port `_buildPrompt` verbatim — system+user+JSON spec exactly as in the Node file. Output JSON shape (the contract the frontend expects, do NOT change):

```json
{
  "headline": "...",
  "priorities": [{"rank": 1, "title": "...", "why": "...", "ticket": "MT-123 or null"}, ...3 items],
  "continuity": "... or null",
  "context": "...",
  "defer": "...",
  "slackUrgency": "low|medium|high",
  "calendarNote": "... or null"
}
```

After parse, attach metadata:
- `generatedAt` (ISO string)
- `sourcesUsed` (list of which sources had non-null data)

Use **`resolve_adapter()`** chain (claude-code → codex → lm-studio → anthropic) — Jon's free-by-default via Claude Code CLI subscription. Model: prefer Haiku if available (`claude-haiku-4-5-20251001`); fall back to whatever the resolved adapter supplies. Mirror the J6c `/ask` route's adapter resolution pattern at `artemis/routes/meetings.py` (look for "claude-code" string).

If the adapter returns markdown-fenced JSON, extract with a regex: `r'\{[\s\S]*\}'` then `json.loads`. If no JSON → raise a `BriefGenerationError`.

### 6. Routes — `artemis/routes/daily_brief.py`

```python
@router.get("/")
async def get_brief(session: AsyncSession = Depends(db.get_session)) -> dict:
    """Return latest persisted snapshot. Instant, no LLM call."""
    snapshot = await repository.get_latest_brief_snapshot(session)
    if snapshot is None:
        return {"brief": None, "exists": False}
    return {"brief": _hydrate_brief_from_snapshot(snapshot), "exists": True}

@router.post("/generate")
async def generate_brief_endpoint(session: AsyncSession = Depends(db.get_session)) -> dict:
    """Trigger a new generation. Returns the new brief."""
    try:
        brief = await generator.generate_brief(session)
        return {"brief": brief, "generated": True}
    except BriefGenerationError as exc:
        raise HTTPException(status_code=502, detail={"error": "brief_generation_failed", "detail": str(exc)}) from exc

@router.get("/history")
async def get_history(session: AsyncSession = Depends(db.get_session)) -> dict:
    """Metadata for last 7 snapshots — no full content."""
    rows = await repository.list_brief_snapshots(session, limit=7)
    return {"history": [{"id": r.id, "generated_at": r.generated_at.isoformat(), "model": r.model, "tokens_input": r.tokens_input, "tokens_output": r.tokens_output} for r in rows]}
```

Mount with `prefix="/api/daily-brief"`, `tags=["daily-brief"]`, `dependencies=[Depends(require_token)]`.

Register in `artemis/main.py`:
```python
from artemis.routes import daily_brief as daily_brief_routes
...
app.include_router(daily_brief_routes.router)
```

Add `"daily-brief"` to `_AVAILABLE_SURFACES` in `artemis/routes/status.py`.

### 7. Tests `tests/test_j7_daily_brief.py`

Minimum cases (mirror brief's quality protocol):
- `test_get_brief_when_none_exists` → `{brief: None, exists: False}`, status 200
- `test_get_brief_returns_latest` → seed one snapshot, GET returns it with `_snapshotId` and `_generatedAt`
- `test_generate_persists_snapshot` → mock the adapter to return a known JSON; assert one new `brief_snapshots` row + correct shape returned
- `test_generate_handles_markdown_fenced_json` → adapter returns ```json\n{...}\n``` ; extraction should succeed
- `test_generate_raises_on_no_json` → adapter returns prose only; returns 502 with `brief_generation_failed` code
- `test_history_returns_last_7_metadata_only` → seed 10 snapshots, GET history returns 7 newest, no `brief_json` content
- `test_gather_sources_is_resilient` → mock one source to raise; assert other sources still populate the context
- `test_persisted_brief_round_trips` → write → read back → contents match

## Quality acceptance — tick before reporting done

- [ ] `./scripts/check.sh` passes (or doc the same pre-existing failures J6c/J6d flagged: `test_j1b_credential_entry::test_get_config_before_any_save`, `test_j1b_credential_entry::test_delete_clears_config`, `test_j5b_jira_team_members::test_get_team_members_no_project_key_returns_empty_all`). Anything else failing IS new and you own it.
- [ ] Manual smoke against live app on port 8000 (Jon currently has it running via `nohup`). Pasted in report:
  - `curl http://localhost:8000/api/daily-brief` returns `{brief: null, exists: false}` first time
  - `curl -X POST http://localhost:8000/api/daily-brief/generate` returns a freshly-generated brief
  - Second GET returns the persisted brief
  - `curl http://localhost:8000/api/daily-brief/history` returns ≥1 metadata entry
- [ ] Migration round-trip: `alembic downgrade -1 && alembic upgrade head` clean
- [ ] Diff re-read twice, no TODO / no stubs / no mock data shipped in production code
- [ ] Coverage on `artemis/brief/`: aim ≥85%

## Critical guardrails — DO NOT VIOLATE

1. **Lossless memory rule (CLAUDE.md §3):** brief_snapshots is append-only. No `delete_brief` API. Supersession via newer rows.
2. **Local-only git** — no push.
3. **Dependencies (org policy):** no new package added or upgraded that's < 7 days old. You shouldn't need any new deps — `anthropic`, `httpx`, `sqlalchemy` all available.
4. **artemis/__init__.py invariant:** BOTH `load_dotenv` calls must use `override=False`. Do not touch that file. Regressing it = OKR data wipe.
5. **No raw SQL bypass of repositories.** If you must, document why in the diff.
6. **Free-by-default LLM** — use `resolve_adapter()` chain. Anthropic API key is fallback, not default.

## Out of scope (separate briefs)

- Slack signals backend (J8 — parallel Worker)
- Frontend changes (the UI exists and works once backend is live)
- Brief history UI (the route is built but no UI consumes it yet)
- Multi-user
- Rich diff visualization between brief snapshots

## Where to start

1. Read this brief twice
2. Read `/Users/artemis/Desktop/Artemis/claudeck-artemis/server/brief-generator.js` end-to-end
3. Read `artemis/routes/meetings.py` (around the `/ask` route) to see the `resolve_adapter()` pattern
4. Migration first, then repository, then sources/prompt, then generator, then routes, then tests
5. Manual smoke last, paste output verbatim

Be terse but thorough. No emojis. No comments in code unless WHY is non-obvious.
