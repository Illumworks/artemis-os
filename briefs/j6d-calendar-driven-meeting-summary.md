# J6d — Calendar-driven post-meeting auto-summary into memory

**Owner:** Worker (Sonnet)
**Scope:** ~300 LOC backend + scheduler infra. Estimated: half-day.
**Depends on:** J6a (Granola integration — DONE), M1 (raw_inputs memory foundation — DONE), GCal integration (DONE).
**Why Jon wants this:** "I want post-action summary to fire automatically after a meeting was completed so Artemis has immediate knowledge context." Polling Granola wastes tokens; webhooks aren't supported; the calendar already tells us when meetings end.

## Mechanism — three pieces

### 1. Scheduler

- [ ] Use `APScheduler` (async, in-process). Add to `pyproject.toml` if not present. Single recurring job: every **2 minutes**, scan for meetings whose GCal end_time is in the past 30 minutes AND we haven't summarized yet.
- [ ] Start the scheduler in `artemis/main.py`'s startup event, stop on shutdown.
- [ ] Document the cadence in `decisions/auto-meeting-summary.md` so it doesn't become a mystery.

### 2. End-detection + match

- [ ] New module `artemis/meetings/summarizer.py`:
  - `find_recently_ended_meetings(window_minutes=30) -> list[CalendarEvent]` — query GCal for events ending in `[now - window_minutes, now]` for the connected user
  - `find_granola_match(event: CalendarEvent) -> str | None` — best-effort title-match against `client.list_meetings(time_range="last_7_days")`. Exact-title first, then case-insensitive substring, then date-proximity tiebreak. Return Granola meeting_id or None.
  - The title-match function MUST log misses with `(gcal_title, gcal_end, best_granola_candidate)` to a new `meeting_match_log` table so Jon can debug naming drift. Don't silently fail.

### 3. Idempotent ingestion

- [ ] New table `meeting_summaries`:
  ```sql
  CREATE TABLE meeting_summaries (
    id              SERIAL PRIMARY KEY,
    granola_id      TEXT NOT NULL UNIQUE,
    gcal_event_id   TEXT,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    action_items    JSONB,        -- structured if Granola provides; else extracted
    raw_input_id    INTEGER,      -- FK into raw_inputs (M1 memory)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  ```
  UNIQUE on `granola_id` means re-running the scheduler won't double-summarize.
- [ ] When a match is found:
  1. Check `meeting_summaries.granola_id` — skip if already exists
  2. Fetch full transcript via `GranolaClient.get_meeting(meeting_id)`
  3. Call `resolve_adapter()` with a prompt that produces:
     - 3-5 bullet summary
     - structured action items: `[{text, owner?, due?}]`
  4. Write a `raw_inputs` row (M1) with `source="meeting_summary"`, `scope="user:jon"`, content = the structured JSON
  5. Write the `meeting_summaries` row, linked to the raw_input
  6. Log success in `meeting_match_log`

### 4. Surfaces

- [ ] New route `GET /api/meetings/{granola_id}/summary` — returns the stored summary if present, 404 otherwise. The J6c Meetings UI's Actions tab reads from here first (instant) and only falls back to live extraction if missing.
- [ ] Floating Artemis gets auto-context: any time the user opens chat within 4 hours of a meeting ending, the Floating Artemis system prompt should include a one-liner: "You just finished <Meeting Title>. Summary: <3 bullets>." Add this in `artemis/floating_artemis/chat.py:_build_system_prompt` — pull the latest `meeting_summaries` row created within the last 4 hours.

## Acceptance — what done looks like

- [ ] Sit a meeting through to completion (or fake-end one by editing the GCal event end_time). Within 4 minutes (worst case 2× the cadence), the summary appears in:
  - `meeting_summaries` table
  - `raw_inputs` (M1) with the right `source` + `scope`
  - `GET /api/meetings/{id}/summary`
- [ ] Floating Artemis chat opened within 4 hours of that meeting includes the summary in the system prompt (verify by inspecting the sent prompt in logs, or asking the chat "what did I just talk about?" — it should know without being told)
- [ ] Re-running the scheduler manually does NOT create duplicate `meeting_summaries` or duplicate `raw_inputs`
- [ ] If Granola has no transcript yet (meeting just ended, Granola hasn't processed), the scheduler logs to `meeting_match_log` and retries on the next tick. Once the transcript appears, it summarizes.
- [ ] Title-match misses are logged. Test: rename a GCal event so it doesn't match any Granola meeting → log shows the miss with the best candidate considered.
- [ ] App restart with a meeting summary mid-flight does not duplicate work — UNIQUE constraint catches it.

## Cost note

- Cadence 2 minutes × 30-minute window × 1 user = scheduler does ~720 GCal list calls/day. GCal API quota is generous, fine.
- Each NEW summary = 1 LLM call. Worst case = ~6 meetings/day × 1 call = 6 calls/day, all via `resolve_adapter()` so free-by-default (Claude Code CLI subscription).

## Quality acceptance — tick before reporting done

- [ ] Scheduler starts on `uvicorn` boot, stops on shutdown, logs both events
- [ ] Manual end-to-end test paste in report: GCal event ended 5 min ago → `meeting_summaries` row created → Floating Artemis system prompt includes it
- [ ] `alembic downgrade -1 && alembic upgrade head` reproduces the schema
- [ ] No polling against Granola when no recently-ended meetings exist (cheap idle state)
- [ ] Tests: scheduler tick, title-match (3 cases: exact / fuzzy / miss), idempotency, summary persisted on M1 raw_input
- [ ] `ruff check` + `mypy` clean
- [ ] No mock data / no stubbed responses in shipped code

## Out of scope (separate briefs)

- Multi-user. Single Jon user for now.
- Webhook ingestion from Granola if/when they add it — replaces the polling path.
- Memory consolidation (Haiku M2 work) — that's M2's job, not this brief's.

## Where to start

1. Read this brief twice
2. Read `artemis/integrations/granola/client.py`, `artemis/memory/...` (M1 raw_inputs API), `artemis/floating_artemis/chat.py:_build_system_prompt`
3. Backend first: migration → summarizer module → scheduler wiring → routes
4. Test by hand: edit a recent GCal event end_time to be 5 min ago, wait for the next tick, verify
