# J10e — Proactive OAuth token refresh (Granola + Slack + GCal)

**Owner:** Worker (Sonnet)
**Scope:** ~250 LOC backend + scheduler wiring. Estimated: half-day.
**Depends on:** Nothing — leaf. J6d's APScheduler infrastructure (`artemis/meetings/scheduler.py`) is reused as a reference pattern; this brief adds a sibling scheduler, it does not modify J6d.
**Blocks:** Nothing — proactive polish. Connectors UI "needs_reauth" affordance is tracked separately (J10d).

> **Relative paths only.** Do not paste `Repo: /Users/...` headers or absolute paths into the brief's file scope sections. The Worker is spawned with `isolation: "worktree"`. See `briefs/CONVENTIONS.md` for the CWD-trap reminder — after this Worker completes, run `pwd` from the harness before trusting any `git log` output.

## Why

Today (2026-05-19), both Granola and Slack OAuth credentials silently went dead mid-session:

- **Granola** — `integrations.status='active'` but `Meetings` panel rendered "disconnected" because `_ensure_fresh_token` in `artemis/integrations/granola/client.py` only fires on the next MCP call, and the call path swallowed the failure.
- **Slack** — `users.info` returned `invalid_auth`, the name-cache tables (`slack_users`, `slack_channels`) silently stayed empty, and the UI rendered raw IDs like `U0AMNKUGXLP` instead of resolved names.

Both required a manual disconnect/reconnect in the Connectors modal. That is unacceptable for a daily-driver OS — Jon should never have to think about token expiry. Lazy refresh is too late: by the time a user notices the broken surface, the credentials are already cold and the failure has surfaced as UI breakage.

We want **proactive background refresh** — a scheduler tick that sweeps every active integration and refreshes any token whose `expires_at` falls inside the next 30 minutes, well before any user-facing call needs it.

## Architecture

Extend the APScheduler pattern already proven by J6d (`artemis/meetings/scheduler.py`). One new in-process job, **cadence 15 minutes**, scans every `integrations` row with `status='active'`, dispatches to a per-provider refresher, persists re-encrypted credentials.

```
APScheduler tick (every 15 min)
   ↓
artemis/integrations/token_refresh/scheduler.py::run_refresh_tick()
   ↓
   for each active integration:
       creds = decrypt(integration.encrypted_credentials)
       if creds has no refresh_token or no expires_at:  skip (e.g. bot-only Slack xoxb)
       if now < expires_at - 30 min:                    skip (still healthy)
       if now < last_refresh_attempt_at + cooldown:     skip (idempotency guard)
       refresher = REFRESHERS[integration.provider]
       result = await refresher.refresh(creds)
       persist(result)   # re-encrypt, update row, bump last_refresh_attempt_at
```

Per-provider adapters live in `artemis/integrations/token_refresh/providers/`. Granola's existing `_refresh_token_exchange` logic in `artemis/integrations/granola/client.py:157` is the model — port the transport call into a standalone `GranolaTokenRefresher` so both the scheduler and the existing lazy-refresh path can use it.

The existing lazy-refresh paths in `granola/client.py::_ensure_fresh_token` and `gcal/provider.py::_refresh_access_token` stay as backstops; they do not move. They become the rare slow path; the scheduler is the common fast path.

## Scope — slices

### Slice A — Refresher protocol + per-provider implementations (~80 LOC)

- [ ] New package `artemis/integrations/token_refresh/`:
  ```
  artemis/integrations/token_refresh/
    __init__.py
    base.py        # TokenRefresher protocol + RefreshResult dataclass
    providers/
      __init__.py  # REFRESHERS = {"granola": GranolaTokenRefresher(), ...}
      granola.py
      slack.py
      gcal.py
  ```
- [ ] `base.py` defines:
  ```python
  class RefreshOutcome(Enum):
      REFRESHED = "refreshed"           # got new tokens, persist them
      STILL_VALID = "still_valid"       # no refresh needed (caller skipped early)
      NO_REFRESH_TOKEN = "no_refresh_token"   # provider gives non-expiring token (Slack xoxb)
      REFRESH_TOKEN_EXPIRED = "refresh_token_expired"  # mark needs_reauth
      TRANSIENT_FAILURE = "transient_failure"          # log + retry next tick

  @dataclass
  class RefreshResult:
      outcome: RefreshOutcome
      new_creds: dict[str, object] | None  # only set when outcome == REFRESHED
      error: str | None = None

  class TokenRefresher(Protocol):
      provider: str
      async def refresh(self, creds: dict[str, object]) -> RefreshResult: ...
  ```
- [ ] `providers/granola.py` — port `_refresh_token_exchange` to a class-based refresher hitting `GRANOLA_TOKEN_ENDPOINT`. Distinguish `400 invalid_grant` (→ `REFRESH_TOKEN_EXPIRED`) from network errors (→ `TRANSIENT_FAILURE`).
- [ ] `providers/gcal.py` — same shape, hitting `_GOOGLE_TOKEN_URL`. Reuse the body parsing currently in `artemis/integrations/gcal/provider.py::_refresh_access_token`.
- [ ] `providers/slack.py` — Slack token rotation is opt-in (`token_rotation_enabled` apps issue `xoxe-` access tokens with `xoxe-1-…` refresh tokens via `oauth.v2.access` with `grant_type=refresh_token`). If the stored creds dict lacks a `refresh_token` (i.e. the workspace is using non-rotating `xoxb-` bot tokens), return `NO_REFRESH_TOKEN` and the scheduler skips it forever — that's correct behavior, those tokens don't expire. Otherwise POST to `_SLACK_OAUTH_URL` with `grant_type=refresh_token`. **Important:** today's symptom (Slack `invalid_auth`) is likely a *different* failure mode (revoked install or scope drift), not expiry — that case will surface as `REFRESH_TOKEN_EXPIRED` here and route through the same `needs_reauth` flow.

### Slice B — Scheduler job (~50 LOC)

- [ ] New module `artemis/integrations/token_refresh/scheduler.py` mirroring `artemis/meetings/scheduler.py` exactly:
  - `get_token_refresh_scheduler() -> AsyncIOScheduler`
  - `start_token_refresh_scheduler()` / `stop_token_refresh_scheduler()`
  - `CADENCE_MINUTES = 15`
  - `REFRESH_LEEWAY_MINUTES = 30` (refresh anything expiring within this window)
  - `COOLDOWN_MINUTES = 10` (skip rows we tried in the last N minutes)
- [ ] Wire `start_token_refresh_scheduler()` and `stop_token_refresh_scheduler()` into `artemis/main.py`'s existing `lifespan` (lines around 64–68 — alongside `start_meeting_scheduler()`).
- [ ] Job entrypoint `run_refresh_tick()`:
  - Open a fresh async DB session via the same session factory the rest of the app uses.
  - `await repository.list_active(session)` — iterate.
  - For each integration: decrypt → decide skip vs. refresh per the logic above → dispatch to `REFRESHERS[integration.provider]` (skip if no refresher registered).
  - On `REFRESHED`: persist re-encrypted creds (Slice C).
  - On `REFRESH_TOKEN_EXPIRED`: mark row `status='needs_reauth'` and do NOT touch the creds blob.
  - On `TRANSIENT_FAILURE`: log + bump `last_refresh_attempt_at`. Don't change status.
  - Wrap the per-integration block in `try/except Exception` so one bad row never poisons the tick.

### Slice C — Persistence (~40 LOC)

- [ ] Migration `alembic/versions/00XX_integration_refresh_metadata.py` (pick next free revision; J6d's was `0019`, so check `alembic heads`):
  ```sql
  ALTER TABLE integrations
    ADD COLUMN last_refresh_attempt_at TIMESTAMPTZ;
  ```
  Reversible. Verify with `alembic downgrade -1 && alembic upgrade head`.
- [ ] Extend `artemis/integrations/models.py::Integration` with the new column.
- [ ] Extend the existing `'needs_reauth'` string into the `status` column. The column is already free-form `Text` (see `models.py:34`), no enum change needed — but add a module-level constant `STATUS_NEEDS_REAUTH = "needs_reauth"` next to where `'active'` is referenced.
- [ ] Add to `artemis/integrations/repository.py`:
  ```python
  async def persist_refreshed_credentials(
      session, *, integration_id: int, new_creds: dict[str, object]
  ) -> None: ...    # re-encrypt + UPDATE encrypted_credentials, last_verified_at, last_refresh_attempt_at

  async def mark_needs_reauth(session, integration_id: int) -> None: ...
      # status='needs_reauth' + last_refresh_attempt_at=now()

  async def mark_refresh_attempted(session, integration_id: int) -> None: ...
      # last_refresh_attempt_at=now()  (used on transient failure)
  ```
  All three commit via the caller's session — match the existing repository convention.

### Slice D — Manual refresh endpoint (optional, ~30 LOC)

- [ ] `POST /api/integrations/{id}/refresh` — triggers `run_refresh_tick()`'s per-row logic for a single integration. Returns `{outcome: "...", new_expires_at: <ts>|null}`. No body required.
- [ ] Mount in `artemis/routes/integrations.py` (or wherever the existing connectors routes live).
- [ ] Useful for debugging from the Connectors modal and for the future J10d "Refresh now" button. Do NOT build any UI in this brief.

## Things to address explicitly

### Refresh-token expiry → needs_reauth, not deletion

When the refresh token itself is expired/revoked, the provider responds with `invalid_grant` (or Slack's `invalid_refresh_token`). The refresher returns `REFRESH_TOKEN_EXPIRED`. The scheduler then:

1. Sets `integrations.status = 'needs_reauth'`.
2. Leaves the encrypted creds blob untouched (the user may want to inspect, and the Connectors UI may want to display the workspace name / scopes from `metadata`).
3. Logs a structured event (see Logging below).

The Connectors UI surfacing of `needs_reauth` is **out of scope for this brief** — it's J10d. For now the row just shows `status=needs_reauth` in the DB and `/api/integrations` responses.

### Idempotency

Two scheduler ticks must not both try to refresh the same row. The cooldown is **`last_refresh_attempt_at`-based**, not a row lock — simpler, survives restarts, no async-with-postgres-advisory-lock complexity. The check in `run_refresh_tick()`:

```python
if integration.last_refresh_attempt_at is not None:
    age = now - integration.last_refresh_attempt_at
    if age < timedelta(minutes=COOLDOWN_MINUTES):
        continue
```

The 10-minute cooldown is < the 15-minute cadence, so normal operation isn't throttled; the cooldown only matters when the harness restarts mid-tick or when a transient failure trips back-to-back ticks.

### Concurrency with lazy refresh

The existing lazy paths (`granola/client.py::_ensure_fresh_token`, `gcal/provider.py::_refresh_access_token`) still run on user-driven requests. They can race the scheduler. Mitigations, in priority order:

1. The scheduler's 30-min leeway window means the scheduler always refreshes *before* the lazy path's <60s leeway would fire. In practice the lazy path becomes a never-taken backstop.
2. If they do race, both refresh requests succeed independently; the second persisted write wins. Granola/Google return a fresh `access_token` for every successful refresh-grant, so a "lost" write is functionally harmless — the token in memory of the lazy-refresh caller still works for its in-flight request.
3. We **do not** attempt to coordinate the two paths with locks. Note this as accepted risk in the report.

### Logging — audit channel

Every refresh attempt logs to the structured audit channel used by M1 — the same `logger = logging.getLogger(__name__)` pattern J6d uses, with `extra={…}` carrying `integration_id`, `provider`, `outcome`, `new_expires_at`. **Do not** write to `raw_inputs` for this — that table is for user-meaningful memory content, not infra telemetry. Standard structured logger output is enough.

```python
logger.info(
    "token_refresh_tick",
    extra={
        "integration_id": integration.id,
        "provider": integration.provider,
        "outcome": result.outcome.value,
        "new_expires_at": new_expires_at,
    },
)
```

## Acceptance — tick before reporting done

- [ ] Scheduler starts on `uvicorn` boot, stops on shutdown, logs both events (mirror J6d's log lines exactly)
- [ ] On boot, the first tick fires within 15 minutes and visibly logs one line per active integration
- [ ] Manual test: edit a Granola integration's `expires_at` in creds to `now + 10 minutes`, restart the app, wait one tick, verify the row's `encrypted_credentials` blob changed and the new decrypted `expires_at` is ~1 hour in the future
- [ ] Manual test: edit a Granola refresh_token to a known-bad value, wait one tick, verify `status='needs_reauth'` and the creds blob is unchanged
- [ ] Existing lazy-refresh path in `granola/client.py` continues to work when invoked directly (regression-test it)
- [ ] `POST /api/integrations/{id}/refresh` (if Slice D shipped) returns the right outcome JSON for each refresher state
- [ ] Two ticks back-to-back (run `run_refresh_tick()` twice in a unit test) make only one refresh HTTP call per row, thanks to the cooldown
- [ ] App restart mid-refresh does not corrupt the creds blob — the failed write simply rolls back and the next tick retries

## Quality acceptance — tick before reporting done

- [ ] `git diff --staged` re-read twice before commit
- [ ] `ruff check` + `mypy` clean
- [ ] Tests cover: each refresher's three outcomes (REFRESHED / REFRESH_TOKEN_EXPIRED / TRANSIENT_FAILURE), the scheduler dispatch (skip-healthy / skip-cooldown / dispatch-correct-refresher / handle-unknown-provider), and the persistence repository functions
- [ ] No mock data / no stubbed responses in shipped code; mocks live only in tests
- [ ] `alembic downgrade -1 && alembic upgrade head` reproduces the schema
- [ ] Manual smoke pasted verbatim in the report — the two manual tests above with timestamps and log excerpts

## Out of scope (separate briefs)

- **Connectors UI changes** to render `needs_reauth` distinctly with a "Reconnect" affordance — J10d.
- **Provider webhooks** announcing token revocation (Slack supports `tokens_revoked` events; Granola does not) — separate brief if/when we wire Slack events deeper.
- **Multi-account per provider** — current model is one row per (provider, workspace_id); proactive refresh doesn't change that.
- **Encrypting `last_refresh_attempt_at`** — it's a timestamp, not a secret; plaintext column is fine.

## Where to start

1. Read this brief twice
2. Read `artemis/integrations/granola/client.py:145-204` (existing refresh logic), `artemis/integrations/gcal/provider.py:106-125` (GCal refresh), `artemis/integrations/repository.py` (row CRUD), `artemis/meetings/scheduler.py` (APScheduler pattern to mirror), `artemis/main.py:60-85` (lifespan wiring)
3. Slice A first (refresher classes + protocol — pure, no DB), then Slice C (migration + repo), then Slice B (scheduler wiring), then Slice D last (optional endpoint)
4. Manual test by editing `expires_at` to the near future and watching the next tick
