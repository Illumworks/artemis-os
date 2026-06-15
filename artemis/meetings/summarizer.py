"""Calendar-driven post-meeting auto-summarizer (J6d).

Core logic:
  - find_recently_ended_meetings(): query GCal for events ending in the past
    window_minutes whose summaries don't already exist.
  - find_granola_match(): title-match a GCal event against Granola's last_30_days
    list using exact → fuzzy → proximity tiebreak.
  - run_summarizer_tick(): called by the scheduler every 2 minutes. For each
    unmatched recently-ended meeting: find granola match → fetch transcript →
    call LLM → write raw_input (M1) → write meeting_summaries row.

No polling against Granola when no recently-ended meetings exist (cheap idle).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as _db
from artemis.costs.events import record_cost_event
from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.gcal.auth_dead import handle_gcal_auth_dead
from artemis.integrations.gcal.client import GCalAuthDeadError, GCalClient
from artemis.integrations.gcal.sync import sync_recent_gcal_events_cache
from artemis.integrations.gcal.types import Event
from artemis.integrations.granola.client import GranolaClient, Meeting
from artemis.meetings.models import MeetingMatchLog, MeetingSummary
from artemis.meetings.summary_schemas import MeetingSummary as MeetingSummarySchema
from artemis.memory.raw_inputs import insert_raw_input
from artemis.proactivity.commitments import ingest_meeting_commitments

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# How far back to scan for ended meetings (minutes).
_WINDOW_MINUTES = 30
# Maximum title edit-distance as a fraction for fuzzy matching (substring only).
_PROXIMITY_WINDOW_HOURS = 4  # how close in time (hours) for proximity tiebreak


# ── Client factories ──────────────────────────────────────────────────────────


async def _build_gcal_client(session: AsyncSession) -> GCalClient | None:
    rows = await repo.list_active(session, provider="gcal")
    if not rows:
        return None
    row = rows[0]
    creds = decrypt_credentials(bytes(row.encrypted_credentials))
    integration_id: int = row.id

    async def _on_tokens_refreshed(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        new_creds = dict(creds)
        new_creds["access_token"] = access_token
        new_creds["refresh_token"] = refresh_token
        new_creds["expires_at"] = expires_at
        await repo.persist_refreshed_credentials(
            session,
            integration_id=integration_id,
            new_creds=new_creds,
        )

    return GCalClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
        expires_at=float(str(creds.get("expires_at") or 0)),
        on_tokens_refreshed=_on_tokens_refreshed,
    )


async def _build_granola_client(session: AsyncSession) -> GranolaClient | None:
    rows = await repo.list_active(session, provider="granola")
    if not rows:
        return None
    creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
    return GranolaClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
        expires_at=float(str(creds.get("expires_at") or 0)),
    )


# ── GCal auth-dead handling ───────────────────────────────────────────────────


async def _handle_gcal_auth_dead(session: AsyncSession, integration_id: int) -> None:
    """Mark GCal integration as needs_reauth and send a rate-limited owner DM."""
    await handle_gcal_auth_dead(session, integration_id)


# ── GCal end-detection ────────────────────────────────────────────────────────


def _parse_event_end_dt(event: Event) -> datetime | None:
    """Return the end datetime of the event, UTC-aware, or None if unparseable."""
    raw = event.end.date_time
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


async def find_recently_ended_meetings(
    session: AsyncSession,
    window_minutes: int = _WINDOW_MINUTES,
) -> list[Event]:
    """Return GCal events ending in [now - window_minutes, now] not yet summarized.

    Returns an empty list when GCal is not connected or no events match.
    """
    gcal = await _build_gcal_client(session)
    if gcal is None:
        return []

    now = datetime.now(UTC)
    since = now - timedelta(minutes=window_minutes)

    # Fetch already-summarized event IDs so we skip them without hitting Granola.
    existing_result = await session.execute(
        select(MeetingSummary.gcal_event_id).where(MeetingSummary.gcal_event_id.isnot(None))
    )
    summarized_gcal_ids: set[str] = {r for (r,) in existing_result.all()}

    rows = await repo.list_active(session, provider="gcal")
    gcal_integration_id: int | None = rows[0].id if rows else None

    try:
        events = await gcal.list_events(
            calendar_id="primary",
            time_min=since.isoformat(),
            time_max=now.isoformat(),
        )
    except GCalAuthDeadError:
        if gcal_integration_id is not None:
            await _handle_gcal_auth_dead(session, gcal_integration_id)
        return []
    except Exception:
        logger.warning("GCal list_events failed in find_recently_ended_meetings", exc_info=True)
        return []

    ended: list[Event] = []
    for event in events:
        if event.id in summarized_gcal_ids:
            continue
        end_dt = _parse_event_end_dt(event)
        if end_dt is None:
            continue
        # Accept events whose end is in the past (within window).
        if since <= end_dt <= now:
            ended.append(event)

    return ended


# ── Granola title matching ────────────────────────────────────────────────────


def _title_match_score(gcal_title: str, granola_title: str) -> tuple[str, float]:
    """Return (match_kind, score) for a GCal/Granola title pair.

    match_kind:
      "exact"    — identical after strip
      "fuzzy"    — case-insensitive substring (either direction)
      "none"     — no match
    score: 1.0 exact, 0.5 fuzzy, 0.0 none.
    """
    g = gcal_title.strip()
    r = granola_title.strip()
    if g == r:
        return ("exact", 1.0)
    if g.lower() == r.lower():
        return ("exact", 1.0)
    gl, rl = g.lower(), r.lower()
    if gl in rl or rl in gl:
        return ("fuzzy", 0.5)
    return ("none", 0.0)


async def find_granola_match(
    event: Event,
    granola_meetings: list[Meeting],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Find the best Granola meeting for a GCal event.

    Returns (granola_id, match_kind, best_candidate_id, best_candidate_title).
    granola_id is None if no acceptable match was found.

    Match priority:
      1. Exact title (case-insensitive)
      2. Substring match (either direction, case-insensitive)
      3. Date-proximity tiebreak: among scored ties, pick closest in time
    """
    gcal_title = (event.summary or "").strip()
    gcal_end = _parse_event_end_dt(event)

    exact: list[tuple[Meeting, str]] = []
    fuzzy: list[tuple[Meeting, str]] = []
    best_candidate: Meeting | None = None
    best_score: float = -1.0

    for m in granola_meetings:
        kind, score = _title_match_score(gcal_title, m.title)
        if score > best_score:
            best_score = score
            best_candidate = m
        if kind == "exact":
            exact.append((m, kind))
        elif kind == "fuzzy":
            fuzzy.append((m, kind))

    candidates = exact if exact else fuzzy
    if not candidates:
        bc_id = best_candidate.id if best_candidate else None
        bc_title = best_candidate.title if best_candidate else None
        return (None, None, bc_id, bc_title)

    # Tiebreak by proximity: pick candidate closest to gcal_end.
    if gcal_end is not None and len(candidates) > 1:

        def proximity(pair: tuple[Meeting, str]) -> float:
            m = pair[0]
            if not m.date_ms:
                return float("inf")
            m_dt = datetime.fromtimestamp(m.date_ms / 1000, tz=UTC)
            return abs((m_dt - gcal_end).total_seconds())

        candidates.sort(key=proximity)

    winner, match_kind = candidates[0]

    # Reject proximity matches where event is further than the window.
    if gcal_end is not None and winner.date_ms:
        w_dt = datetime.fromtimestamp(winner.date_ms / 1000, tz=UTC)
        if abs((w_dt - gcal_end).total_seconds()) > _PROXIMITY_WINDOW_HOURS * 3600:
            bc_id = best_candidate.id if best_candidate else None
            bc_title = best_candidate.title if best_candidate else None
            return (None, None, bc_id, bc_title)

    return (winner.id, match_kind, winner.id, winner.title)


# ── Log helpers ───────────────────────────────────────────────────────────────


async def _log_match(
    session: AsyncSession,
    *,
    event: Event,
    matched_granola_id: str | None,
    match_kind: str | None,
    best_candidate_id: str | None,
    best_candidate_title: str | None,
    outcome: str,
) -> None:
    now = datetime.now(UTC)
    end_dt = _parse_event_end_dt(event)
    row = MeetingMatchLog(
        gcal_event_id=event.id,
        gcal_title=event.summary or "",
        gcal_end_time=end_dt,
        matched_granola_id=matched_granola_id,
        match_kind=match_kind,
        best_candidate_title=best_candidate_title,
        best_candidate_id=best_candidate_id,
        outcome=outcome,
        logged_at=now,
    )
    session.add(row)
    await session.flush()


# ── LLM summarization ─────────────────────────────────────────────────────────

_SUMMARY_PROMPT = """You are summarizing a meeting transcript for a marketing intelligence system.

Meeting title: {title}

Transcript / notes:
{transcript}

Produce a JSON object with exactly these keys:
- "bullets": a list of 3-5 concise bullet-point strings summarizing the key points
- "action_items": a list of objects, each with:
    - "text": string (what needs to be done)
    - "owner": string or null (person responsible, if mentioned)
    - "due": string or null (due date/timeframe, if mentioned)

Respond with ONLY the JSON object. No markdown fences, no preamble."""


_MAX_VALIDATION_RETRIES = 1


async def _llm_summarize(
    title: str,
    transcript_data: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Call the LLM and return (bullets_text, action_items) bounded by Pydantic.

    H4: Output is validated against `MeetingSummarySchema`. On validation
    failure, retries once with the error appended to the next prompt. After
    the retry, returns an empty summary rather than letting hallucinated
    content propagate into Floating Artemis's system prompt.

    Returns placeholder values if the LLM call fails so the scheduler doesn't
    crash — a failed summary is still logged.
    """
    from artemis.agent.client import AnthropicAdapter, CompletionRequest
    from artemis.agent.types import Message, TextBlock
    from artemis.providers import get_adapter
    from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

    # Build a readable transcript string from the meeting detail dict.
    transcript_text = ""
    if "transcript" in transcript_data:
        transcript_text = str(transcript_data["transcript"])
    elif "notes" in transcript_data:
        transcript_text = str(transcript_data["notes"])
    else:
        # Fall back to full JSON if no obvious transcript key.
        transcript_text = json.dumps(transcript_data, indent=2)[:60000]

    # Resolve best available provider (same chain as floating_artemis).
    adapter = None
    for candidate in ("claude-code", "codex", "lm-studio", "anthropic"):
        try:
            adapter = get_adapter(candidate)
            break
        except (MissingApiKeyError, UnknownProviderError):
            continue
        except Exception:
            continue

    if adapter is None:
        adapter = AnthropicAdapter()

    last_error: str | None = None
    for attempt in range(_MAX_VALIDATION_RETRIES + 1):
        prompt = _SUMMARY_PROMPT.format(title=title, transcript=transcript_text[:60000])
        if last_error is not None:
            prompt += (
                f"\n\nYour previous response failed Pydantic validation: {last_error}\n"
                "Re-emit the JSON with corrected shape. Do not add commentary."
            )
        try:
            request = CompletionRequest(
                messages=[Message(role="user", content=[TextBlock(text=prompt)])],
                max_tokens=1024,
            )
            response = await adapter.complete(request)
            raw_text = "".join(
                block.text for block in response.message.content if isinstance(block, TextBlock)
            )
            summary = MeetingSummarySchema.model_validate_json(raw_text.strip())
            bullets_text = "\n".join(f"- {b}" for b in summary.bullets)
            action_items = [item.model_dump() for item in summary.action_items]
            # Record cost — failure must never propagate.
            try:
                from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

                _provider = "claude-code" if isinstance(adapter, ClaudeCodeAdapter) else "anthropic"
                _path = "cli" if isinstance(adapter, ClaudeCodeAdapter) else "api"
                _model = (
                    getattr(adapter, "_default_model", None)
                    or getattr(adapter, "model", None)
                    or "claude-sonnet-4-6"
                )
                async with _db.SessionLocal() as _cost_session:
                    await record_cost_event(
                        _cost_session,
                        provider=_provider,
                        model=_model,
                        provider_path=_path,
                        feature_tag="meeting_summary",
                        input_tokens=getattr(response.usage, "input_tokens", 0),
                        output_tokens=getattr(response.usage, "output_tokens", 0),
                        cache_creation_input_tokens=getattr(
                            response.usage, "cache_creation_input_tokens", 0
                        ),
                        cache_read_input_tokens=getattr(
                            response.usage, "cache_read_input_tokens", 0
                        ),
                    )
                    await _cost_session.commit()
            except Exception:
                logger.warning("cost_event recording failed in meeting summarizer", exc_info=True)
            return bullets_text, action_items
        except ValidationError as exc:
            last_error = str(exc)
            logger.warning(
                "Meeting summarizer validation failed (attempt %d) for %r: %s",
                attempt + 1,
                title,
                exc,
            )
            if attempt >= _MAX_VALIDATION_RETRIES:
                # Persistent shape failure → empty placeholder (no hallucinated commitments).
                return "- Summary unavailable (validation failed)", []
        except Exception:
            logger.warning("LLM summarization failed for meeting %r", title, exc_info=True)
            return "- Summary unavailable (LLM call failed)", []
    return "- Summary unavailable (validation failed)", []


# ── Main scheduler tick ───────────────────────────────────────────────────────


async def run_summarizer_tick() -> None:
    """Scheduler entry point: scan for recently ended meetings, summarize new ones.

    Called every 2 minutes by APScheduler. Does nothing if no recently-ended
    meetings exist (cheap idle). Idempotent: UNIQUE(granola_id) prevents doubles.
    """
    async with _db.SessionLocal() as session:
        try:
            await _run_tick_in_session(session)
            await session.commit()
        except Exception:
            logger.exception("Summarizer tick failed")
            await session.rollback()


async def _run_tick_in_session(session: AsyncSession) -> None:
    await sync_recent_gcal_events_cache(session)
    ended_events = await find_recently_ended_meetings(session)
    if not ended_events:
        logger.debug("Summarizer tick: no recently ended meetings, skipping")
        return

    logger.info("Summarizer tick: %d recently ended meeting(s) to check", len(ended_events))

    granola = await _build_granola_client(session)
    if granola is None:
        logger.info("Summarizer tick: Granola not connected, skipping")
        return

    # Fetch Granola meetings list once; reuse for all events in this tick.
    # NOTE: Granola's "last_7_days" range returns an empty list even when recent
    # meetings exist (observed 2026-06-13: last_7_days=0 while last_30_days=40,
    # incl. 9 meetings inside the past week). Use last_30_days — find_granola_match
    # narrows by exact/fuzzy title + time-proximity to the specific ended event, so
    # the wider candidate pool is safe and just ensures recent meetings are present.
    try:
        granola_meetings = await granola.list_meetings(time_range="last_30_days")
    except Exception:
        logger.warning("Summarizer tick: Granola list_meetings failed", exc_info=True)
        return

    for event in ended_events:
        await _process_event(session, event, granola, granola_meetings)


async def _process_event(
    session: AsyncSession,
    event: Event,
    granola: GranolaClient,
    granola_meetings: list[Meeting],
) -> None:
    gcal_title = event.summary or "(untitled)"

    # Check if already summarized by granola_id (handles case where gcal_event_id
    # lookup missed it, e.g. duplicate event IDs in different calendars).
    granola_id, match_kind, best_candidate_id, best_candidate_title = await find_granola_match(
        event, granola_meetings
    )

    if granola_id is None:
        logger.info(
            "Summarizer: no Granola match for GCal event %r (gcal_id=%s, best_candidate=%r)",
            gcal_title,
            event.id,
            best_candidate_title,
        )
        await _log_match(
            session,
            event=event,
            matched_granola_id=None,
            match_kind=None,
            best_candidate_id=best_candidate_id,
            best_candidate_title=best_candidate_title,
            outcome="no_match",
        )
        return

    # Check idempotency — already have a summary for this granola_id?
    existing = await session.execute(
        select(MeetingSummary).where(MeetingSummary.granola_id == granola_id)
    )
    if existing.scalar_one_or_none() is not None:
        logger.info(
            "Summarizer: skipped %r (granola_id=%s) — already summarized",
            gcal_title,
            granola_id,
        )
        return

    # Fetch transcript.
    try:
        transcript_data = await granola.get_meeting(granola_id)
    except Exception:
        logger.warning(
            "Summarizer: failed to fetch Granola transcript for %s", granola_id, exc_info=True
        )
        await _log_match(
            session,
            event=event,
            matched_granola_id=granola_id,
            match_kind=match_kind,
            best_candidate_id=best_candidate_id,
            best_candidate_title=best_candidate_title,
            outcome="no_transcript",
        )
        return

    if not transcript_data:
        logger.info(
            "Summarizer: Granola transcript not yet available for %s — will retry next tick",
            granola_id,
        )
        await _log_match(
            session,
            event=event,
            matched_granola_id=granola_id,
            match_kind=match_kind,
            best_candidate_id=best_candidate_id,
            best_candidate_title=best_candidate_title,
            outcome="no_transcript",
        )
        return

    # Extract plain-text transcript for storage.
    transcript_text: str | None = None
    if isinstance(transcript_data, dict):
        if "transcript" in transcript_data:
            transcript_text = str(transcript_data["transcript"])
        elif "notes" in transcript_data:
            transcript_text = str(transcript_data["notes"])
    if not transcript_text:
        transcript_text = None

    # Summarize via LLM.
    summary_text, action_items = await _llm_summarize(gcal_title, transcript_data)

    # Write M1 raw_input (hash chain — must be inside same transaction).
    payload: dict[str, Any] = {
        "granola_id": granola_id,
        "gcal_event_id": event.id,
        "title": gcal_title,
        "summary": summary_text,
        "action_items": action_items,
        "transcript_length": len(str(transcript_data)),
    }

    async with session.begin_nested():
        raw = await insert_raw_input(
            session,
            source_kind="meeting_summary",
            source_id=granola_id,
            actor="artemis-scheduler",
            scope_kind="user",
            scope_id="jon",
            payload=payload,
        )

        # Write meeting_summaries row (UNIQUE granola_id prevents doubles).
        stmt = (
            pg_insert(MeetingSummary.__table__)  # type: ignore[arg-type]
            .values(
                granola_id=granola_id,
                gcal_event_id=event.id,
                title=gcal_title,
                summary=summary_text,
                action_items=action_items,
                transcript=transcript_text,
                raw_input_id=raw.id,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["granola_id"])
        )
        await session.execute(stmt)

        await ingest_meeting_commitments(
            session,
            granola_id=granola_id,
            title=gcal_title,
            action_items=action_items,
        )

    await _log_match(
        session,
        event=event,
        matched_granola_id=granola_id,
        match_kind=match_kind,
        best_candidate_id=best_candidate_id,
        best_candidate_title=best_candidate_title,
        outcome="summarized",
    )
    logger.info(
        "Summarizer: wrote summary for %r (granola_id=%s, raw_input_id=%d)",
        gcal_title,
        granola_id,
        raw.id,
    )


# ── Recent summaries for Floating Artemis ────────────────────────────────────


async def get_recent_summaries(
    session: AsyncSession,
    hours: int = 4,
    limit: int = 3,
) -> list[MeetingSummary]:
    """Return meeting summaries created within the last `hours` hours.

    Used by Floating Artemis to inject recent meeting context into the system
    prompt.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    result = await session.execute(
        select(MeetingSummary)
        .where(MeetingSummary.created_at >= cutoff)
        .order_by(MeetingSummary.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
