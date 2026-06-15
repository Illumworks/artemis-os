# Brief: App intermittent unresponsiveness — asyncpg connect TimeoutError (for terminal / connector-hardening lane)

**Owner:** terminal (this sits in the scout/scheduler/connector-reliability lane already in progress).
**Filed by:** Opus Lead, 2026-06-15, after it blocked the Google connect + calendar work all session.

## Symptom
The web app (port 8000) intermittently stops responding for several seconds at a time. `GET /healthz` flaps: ~half the requests return 200 in ~1ms, the other half hang to full timeout (8s+) with no response. Worst immediately after a restart; partially settles to ~7/10 healthy but does NOT reach steady 10/10. User-visible fallout this session: Cloudflare 502s on the Google OAuth callback, the Calendar tab showing "not connected" / slow-load-then-works, and `sync_personal_google_integrations` silently failing to persist (left a stale token in the gcal integration blob).

## Evidence gathered (so you don't have to re-derive)
- **Event loop is NOT CPU-bound.** `sample <pid>` repeatedly shows the main thread idle in `uvloop run_forever → uv__io_poll → kevent`. No runaway Python frame, no hot loop.
- **The failure is asyncpg connection ESTABLISHMENT.** Tracebacks (web app AND the OAuth callback) bottom out at `asyncpg/connection.py:2442 async with compat.timeout(timeout)` → `TimeoutError`, i.e. opening a new PG connection times out. Same `TimeoutError` appears in `start_pipeline_scheduler._load_and_register` and `start_automation_scheduler._load_and_register` at boot, and in scout subprocess runs.
- **Postgres itself is healthy.** `psql -h 127.0.0.1` connect+query is consistently ~10ms. `pg_stat_activity` shows only ~20 connections (max_connections now 300). No idle-in-transaction, no long queries.
- **It reproduces from a FRESH standalone process**, not just the busy web loop: `uv run python` one-shot scripts that just open one `SessionLocal()` intermittently (~40-50%) hit the same asyncpg connect `TimeoutError`, while psql from the same shell never does. So it is specific to the asyncpg connect path, not event-loop starvation and not Postgres load.
- **Not memory** (LLM was ejected mid-session; `memory_pressure` ~50% free, Postgres RSS ~96MB) and **not scout-on-the-loop** (scout runs as an isolated subprocess by design — `scout_cli.py`).
- **Worst at startup:** 7 in-process schedulers (memory, automations, pipelines, scout, proactivity, token_refresh, meetings) all run `_load_and_register` concurrently at boot via `AsyncIOScheduler`, each opening its own DB connection(s) at once. The flapping is most severe in the first ~1-2 min after a restart, consistent with a connect storm.

## Leading hypotheses (unconfirmed — for the owner to pin)
1. **Scheduler-startup connect storm:** the 7 schedulers' concurrent `_load_and_register` overwhelm asyncpg connection establishment at boot. Fix candidate: stagger/serialize scheduler startup, or gate them behind a warmed shared pool.
2. **asyncpg connect timeout too aggressive / TCP setup intermittently slow** on this host. Fix candidate: explicit `connect_args={"timeout": 30, ...}` + TCP keepalive on the engine in `artemis/db.py`; keep the pool warm (raise `pool_size`, lengthen `pool_recycle`) so the app rarely establishes NEW connections during request handling (reuse is instant; only fresh connects are flaky).
3. A residual blocking call the 5-15s `sample` windows didn't catch. Fix candidate: install `py-spy` and `py-spy dump` during an actual freeze window.

## Notes / constraints
- **Stop restarting to "fix" it** — each restart re-triggers the startup storm and makes it look worse. Minimize restarts while diagnosing.
- The DB pool was tuned this session (prod 50 via `.env`, `pool_pre_ping=True`, `pool_recycle=1800`, `max_connections=300`) — this was NOT the cause (app was snappy right after that change) but `pool_recycle=1800` does force periodic reconnects, which land on the flaky connect path; worth revisiting alongside hypothesis 2.
- `artemis/db.py` is the safe place for the connect-path tuning (hypothesis 2) and is already Lead-owned this session; the scheduler-startup change (hypothesis 1) is in the scheduler wiring (`main.py` + the per-domain `scheduler.py` files) — your lane.
