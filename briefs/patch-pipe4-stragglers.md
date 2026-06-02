# Patch — PIPE4 Stragglers (Stranded Queued + CLI Timeout + Toast Text)

**Owner:** Codex (paste-ready)
**Branch:** `codex/patch-pipe4-stragglers`
**LOC budget:** ~120 (cap 180)
**Depends on:** PIPE4 + provider-cascade wire-up merged.

## Why

Three small bugs from PIPE4 smoke that don't fit cleanly elsewhere — bundling as one patch.

## Fixes

### Fix 1 — Stranded queued runs

**Symptom:** Pipeline_run `8401bed9` created at 17:41:47 with `status=queued`, `started_at=null`. Never picked up. The asyncio.create_task at /run endpoint died silently before setting started_at.

**Fix:** wrap the fire-and-forget task with error handling that updates the row on failure:

```python
# In artemis/pipelines/routes.py /run endpoint
async def _execute_run(run_id):
    try:
        executor = PipelineExecutor(run_id)
        await executor.run()
    except Exception as e:
        # Mark stranded row as failed
        async with get_db_session() as session:
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .where(PipelineRun.status == "queued")  # only if still queued
                .values(status="failed", error_message=f"Executor crashed: {e}", completed_at=now())
            )
        raise

task = asyncio.create_task(_execute_run(run.id))
# Don't await; fire-and-forget. But task has error handling.
```

Also: add a periodic sweeper (or one-shot on startup) that marks orphaned queued runs as failed:

```python
# In scheduler / startup hook:
async def sweep_orphaned_queued_runs(threshold_minutes=5):
    cutoff = now() - timedelta(minutes=threshold_minutes)
    rows = await session.execute(
        update(PipelineRun)
        .where(PipelineRun.status == "queued")
        .where(PipelineRun.created_at < cutoff)
        .values(status="failed", error_message="Orphaned queued run (executor never started)")
    )
```

### Fix 2 — Claude CLI timeout handling

**Symptom:** `ProviderAPIError 408: claude CLI timed out after 120 s` at executor.py:217. Cascade should fall through but anthropic has no API key → run fails.

**Fix two ways:**

1. **Increase CLI timeout** — 120s is too short for qualifier rubric calls (multi-rubric LLM evaluations). Bump to 300s.
2. **Cascade-aware timeout fallback** — when CLI times out, surface a clearer error noting next adapter in cascade:
   - If `anthropic` is configured (ANTHROPIC_API_KEY env or connector): fall through to it
   - If NOT: fail with `ProviderTimeoutWithNoFallback("Claude CLI timed out after 300s and no API key configured for fallback")`

```python
# In artemis/providers/claude_code.py (or wherever the CLI adapter is)
TIMEOUT_SECONDS = 300  # was 120

# Wrap the CLI call with explicit timeout handling
try:
    result = await asyncio.wait_for(self._run_cli(...), timeout=TIMEOUT_SECONDS)
except asyncio.TimeoutError:
    raise ClaudeCodeTimeoutError(f"Claude CLI did not respond within {TIMEOUT_SECONDS}s")
```

In `artemis/providers/resolver.py`, ensure `ClaudeCodeTimeoutError` is in the "cascade-recoverable" exception set so resolver tries fallback.

### Fix 3 — Stale toast text

**Symptom:** "Run queued — execution engine arrives in PIPE4" / "Run queued — execution wired in PIPE4." appear in UI. PIPE4 has shipped; both messages are misleading.

**Fix:** grep for both strings; replace with:
- "Run #{short_run_id} started. Watch progress on canvas." (when on canvas)
- "Run queued (#{short_run_id}). View in run history." (when on list view)

Sites to update:
- `public/js/features/pipelines.js`
- `public/js/components/pipeline-canvas.js`
- Any other run-trigger toast emitter

## Out of scope

- Re-architecting the fire-and-forget pattern (sufficient with error-handling wrap)
- Per-adapter timeout configuration (one global 300s timeout for CLI is fine v1)
- WebSocket-style live status (live-view brief handles)

## Tests

- Pipeline_run with synthesized executor crash → swept to `failed` with clear error_message
- CLI adapter timeout → `ClaudeCodeTimeoutError` raised → resolver cascades
- Orphan sweeper marks 6+ minute old queued runs as failed
- Toast text strings grep returns updated copies, not the stale ones

## Files

| File | LOC |
|---|---|
| `artemis/pipelines/routes.py` (error-handled fire-and-forget) | ~30 delta |
| `artemis/pipelines/scheduler.py` (orphan sweeper) | ~40 delta |
| `artemis/providers/claude_code.py` (timeout bump + error type) | ~20 delta |
| `artemis/providers/resolver.py` (recognize timeout in cascade fallback) | ~10 delta |
| `public/js/features/pipelines.js` + `pipeline-canvas.js` (toast text) | ~10 delta |
| Tests | ~30 |

**Total: ~140 LOC.** Cap 180.

## Invariants

- node --check on modified JS
- conftest hard-fail on non-test DB
- ./scripts/check.sh passes within exempt set
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, before/after pipeline_run states (stranded scenario simulated), CLI timeout test passing, paste new toast strings, branch.
