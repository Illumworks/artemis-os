# Cleanup — Dead Endpoint 404s (console noise consolidation)

**Owner:** Codex (paste-ready, mechanical)
**Branch:** `codex/cleanup-dead-endpoints`
**LOC budget:** ~180 (honest overrun OK to ~240)
**Brief author:** Lead (Opus 4.7)
**Depends on:** nothing. Independent of PIPE3.

## Why this brief exists

The browser console has been showing the same 404s every page load all session. They're cosmetic (UI doesn't break) but constant noise. Each is a frontend polling endpoint the backend never implemented. After this cleanup, the console is clean enough that real errors stand out.

## The four dead endpoints

| Path | Caller | Current behavior |
|---|---|---|
| `GET /api/version` | bootstrap (`(index):596` or `(index):34`) | 404 — no route exists |
| `GET /api/notifications/unread-count` | notifications poller (probably `notifications.js` or `api.js`) | 404 — no route exists |
| `GET /api/stats/alerts` | stats panel | 404 — no route exists |
| `GET /api/memory/embeddings/status` | memory panel | 404 — no route exists |

## Scope — pick the right fix per endpoint

### `/api/version`

Implement a minimal stub returning the running version:

```python
# artemis/routes/health.py (or wherever /healthz lives)
@router.get("/api/version")
async def version():
    return {"version": __version__, "git_sha": <short sha if available>}
```

Use `artemis.__version__` if exposed; otherwise hardcode for now. Frontend uses it for display ("Artemis v…") in About menu or similar.

### `/api/notifications/unread-count`

The Codex stub pattern: implement returning `{"count": 0}`. The notifications system isn't wired yet (notifications table may exist from J3c stubs); this is a placeholder that lets the poller succeed without 404.

```python
# artemis/routes/notifications.py
@router.get("/api/notifications/unread-count")
async def unread_count():
    return {"count": 0}
```

If `artemis/routes/notifications.py` already exists with other routes, add this one alongside. If it doesn't exist, create it and mount in `main.py`.

### `/api/stats/alerts`

Same shape as the existing `/api/stats/agent-metrics` stub (task #13 from earlier this session). Implement returning empty:

```python
@router.get("/api/stats/alerts")
async def stats_alerts():
    return {"alerts": [], "count": 0}
```

Add to `artemis/routes/stats.py` (file should exist from the agent-metrics work).

### `/api/memory/embeddings/status`

Memory M1 ships `raw_inputs` with embeddings; this status endpoint probably reports embedding job state. Implement a minimal stub:

```python
@router.get("/api/memory/embeddings/status")
async def embeddings_status():
    return {
        "queued": 0,
        "processing": 0,
        "completed_today": 0,
        "last_error": None,
    }
```

Add to the existing `artemis/routes/memory.py` (lives there per Mem-M2).

### Frontend side

Survey: do any of these endpoints have **specific callers** in the frontend that crash when 404? Most likely they just log and move on (the UI keeps working). If a caller crashes on 404, that's a frontend bug worth fixing separately — flag for Lead, don't expand scope here.

The cleanup is **backend-side only** — implement stubs so the polls succeed. Frontend code is unchanged.

## Out of scope

- Implementing actual functionality (real notification count, real alert system, real embedding job tracking). These are placeholder stubs.
- Refactoring the routes file structure.
- Removing the frontend polls. Frontend continues calling these; the stubs make them succeed.
- Caching/throttling the polls. If frontend polls aggressively, that's a frontend concern.

## Invariants

1. **Stubs return correct shape but empty data.** No fake numbers, no mock notifications. Just `{count: 0}` / `{alerts: []}` / etc.
2. **No new dependencies, no new tables.** Pure route additions.
3. **All four routes return 200** within 50ms (synchronous stubs, no DB call needed for the empty case).
4. **Existing routes untouched.** Don't refactor neighbors.

## Files expected

| File | LOC |
|---|---|
| `artemis/routes/health.py` (or wherever health lives) | ~10 delta (add /api/version) |
| `artemis/routes/notifications.py` (existing or new) | ~10 delta or ~20 new |
| `artemis/routes/stats.py` | ~10 delta (add /api/stats/alerts) |
| `artemis/routes/memory.py` | ~15 delta (add embeddings/status) |
| `artemis/main.py` | ~3 delta (if notifications.py is new, mount it) |
| `tests/test_cleanup_dead_endpoints.py` (new) | ~60 (4 endpoints × ~15 LOC test each) |

**Total: ~120 LOC honest.** Cap 240.

## Test plan

1. **Each of the 4 endpoints returns 200 with expected shape.** One test per endpoint.
2. **Response is JSON-parseable** (no HTML 404 pages leaking through).
3. **Existing /api/stats/agent-metrics still works** (regression check for stats.py edit).
4. **Existing /api/memory/conflicts still works** (regression check for memory.py edit).
5. **Browser smoke:** load any page, console should show no 404s for these four paths. (Cloudflare Access manifest CORS errors are separate noise; ignore.)

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit

## What "done" looks like

1. Four endpoints return 200 with stub data.
2. Browser console no longer shows these specific 404s.
3. Tests pass.
4. `check.sh` passes within exempt set.

## Report Codex submits

1. `git diff --stat` output.
2. Response shape for each of the 4 endpoints (paste).
3. Browser console before/after (rough count of red errors).
4. Test pass count.
5. Branch.

---

**Lead notes (not for Codex):**
- These stubs are placeholders — real functionality lands when the actual notification system / alert system / embedding job tracker ships. For now, just stopping the noise.
- After this lands, the only remaining console noise is Cloudflare Access CORS (infrastructure, not in our control) and the dangling `apple-mobile-web-app-capable` deprecation warning (one-line `<meta>` fix, separate brief).
