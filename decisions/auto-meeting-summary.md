# Decision: auto-meeting-summary cadence and architecture

**Date:** 2026-05-18
**Brief:** J6d
**Author:** Worker (Sonnet)

## What

An APScheduler `AsyncIOScheduler` job fires every **2 minutes**, scans Google Calendar
for events whose `end_time` falls in the past **30 minutes**, matches them to Granola
transcripts via title-matching, and writes LLM summaries to `meeting_summaries` and
`raw_inputs` (M1 hash chain).

## Why 2-minute cadence

- Granola typically has a transcript available within 1-2 minutes of a meeting ending.
- 2 minutes × 30-minute lookback = worst-case delay of 2 minutes per meeting.
- GCal quota is generous; 720 list calls/day at ~0.001 quota units each is negligible.
- A shorter interval (30 s) would increase noise without meaningfully improving latency;
  a longer one (5 min) would feel stale in the "what did I just talk about?" use case.

## Why in-process scheduler (not Celery / RQ / cron)

- Single-user app running on one Mac mini; external broker is complexity without benefit.
- APScheduler AsyncIOScheduler lives inside the uvicorn process event loop.
- APScheduler 3.x is stable (3.11.2 released 2025-12-22), already a declared dependency.

## Idempotency guarantee

`meeting_summaries.granola_id` has a UNIQUE constraint. `ON CONFLICT DO NOTHING` in the
insert path means re-running the scheduler never creates duplicate rows. M1 `raw_inputs`
is append-only by design; a duplicate tick would attempt a second insert but the
`meeting_summaries` UNIQUE check fires first, preventing the `raw_inputs` write from
ever being reached.

## Title-matching strategy

Priority order:
1. Exact title (case-insensitive strip)
2. Case-insensitive substring (either direction — GCal titles are often shorter than
   Granola's, which inherit the meeting invite title)
3. Date-proximity tiebreak: among tied candidates pick the one with `date_ms` closest
   to `gcal_end_time`, rejecting any match more than 4 hours away

Misses are always logged to `meeting_match_log` with `best_candidate_title` populated
so Jon can diagnose naming drift without guessing.

## No-transcript retry

If `GranolaClient.get_meeting()` returns an empty dict (Granola hasn't processed the
transcript yet), the event is logged as `outcome="no_transcript"` and **not** written
to `meeting_summaries`. The next tick (2 minutes later) will re-detect the ended event
via GCal, re-attempt the match, and succeed once Granola's transcript is ready. The
GCal-end-detection window is 30 minutes, so there are ~15 retry opportunities.

## Floating Artemis injection

`_build_system_prompt` receives `recent_meeting_context` — a one-liner per summary
created in the last 4 hours. Format: `You just finished "<title>". Summary: <bullets>`.
This is read-only, non-blocking, and fails silently (returns `None`) so it never breaks
chat startup.
