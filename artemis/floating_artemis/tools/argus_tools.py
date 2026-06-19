"""Argus dispatch tool for Callie -- gated to agent_id='callie' only.

Callie owns this tool. No other agent sees it.

v1 (sync, retired): research_district ran in-turn; dossier returned directly.

v2 (async, current): dispatch_research fires a background task immediately and
returns a short ``{"status":"dispatched","district":<name>}`` payload so Callie
acknowledges in the same turn. The background task runs research_district with its
own DB session, produces a Callie-voiced summary, and posts it to the originating
channel via SlackClient.

v3 (resilient, current): dispatch_research PERSISTS a ``pending`` row in
``argus_research_requests`` BEFORE firing the background task. The row captures
channel_id, team_id, district_key, and signal so a process restart can recover
and re-fire the task. On completion the row is marked ``done``; on repeated
failure the row is marked ``failed`` and a fallback Slack post is sent.

Channel ID resolution
---------------------
The tool reads the ``floating_session_id_var`` context variable (set by the turn
engine in chat.py) to get the current session_id. That session_id for Slack turns
has the form ``slack-callie-{team_id}-{channel_id}-{bucket}``, and is also stored
in the session row's metadata with an explicit ``channel_id`` key. The background
task uses ``_session_channel_id`` from session_scope to extract the channel_id, and
reads ``team_id`` from the session metadata.

Callie's token resolution
-------------------------
The background task calls ``_resolve_agent_slack_config(session, agent_id="callie",
team_id=team_id)`` -- the same resolver used by ``_post_slack_message`` in
``integrations_slack_events.py`` -- to get the access_token from the Callie
Integration row in the DB.

Background task safety
----------------------
Wrapped in a try/except so any failure is logged at WARNING level and never
propagates. Strong-referenced in ``_BACKGROUND_TASKS`` (same GC-guard pattern as
``artemis/trace/capture.py``) so asyncio.create_task() result isn't GC'd.

Retry cap
---------
attempts >= 3 → mark failed + post fallback. Startup recovery only re-fires rows
with attempts < 3, preventing infinite restart loops.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import artemis.db as _db
from artemis.agent.types import Tool
from artemis.argus.models import ArgusResearchRequest
from artemis.floating_artemis.authority import AuthorizedToolRegistry
from sqlalchemy import select

_logger = logging.getLogger(__name__)

_SURFACE = "[surface:marketing-os]"
_AGENT_GATE = "[agent:callie]"

# Retry cap: after this many attempts, mark failed and post a fallback.
_MAX_ATTEMPTS = 3

# ── GC-retention guard (mirrors artemis/trace/capture.py pattern) ─────────────
# asyncio.create_task() returns a weakly-referenced Task; holding a strong ref
# here prevents GC before execution.  The done-callback drops the ref.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

# ── Tool definition ────────────────────────────────────────────────────────────

DISPATCH_RESEARCH = Tool(
    name="dispatch_research",
    description=(
        "Ask Argus (Callie's dedicated research agent) to research a district in depth. "
        "ASYNC: returns immediately with an acknowledgement payload; Argus posts findings "
        "back to this channel when research completes. "
        "Each finding carries source='Argus' so attribution is grounded. "
        "Use when Jon asks Callie to dig into a district or a qualified signal. "
        f"{_SURFACE} {_AGENT_GATE} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "required": ["district_key"],
        "properties": {
            "district_key": {
                "type": "string",
                "description": (
                    "Stable district identifier -- district_id from a signal "
                    "(e.g. 'TX-001') or a normalised district name slug. "
                    "Used as the drawer key; must match the signal's district_id "
                    "if one exists so findings accumulate correctly."
                ),
            },
            "signal_id": {
                "type": "integer",
                "description": (
                    "Optional signal ID that triggered this research. "
                    "When provided, every finding is linked back to the signal "
                    "as evidence so the provenance chain is preserved."
                ),
            },
            "signal": {
                "type": "object",
                "description": (
                    "Optional triggering signal dict (from get_signal). "
                    "Provides state, headline, and provenance context to focus "
                    "Argus's research. Pass the full get_signal output."
                ),
            },
        },
    },
)

# ── Tool implementation ────────────────────────────────────────────────────────


async def _dispatch_research(inp: dict[str, Any]) -> str:
    """Fire-and-acknowledge: persist a pending row, schedule background research, return immediately.

    Returns a JSON payload like ``{"status":"dispatched","district":"TX-001"}``
    so Callie naturally produces the acknowledgement in her own turn without
    waiting for research to finish.
    """
    import json

    district_key = str(inp.get("district_key") or "").strip()
    if not district_key:
        return "Error: district_key is required"

    signal_id_raw = inp.get("signal_id")
    triggering_signal_id: str | None = (
        str(int(signal_id_raw)) if signal_id_raw is not None else None
    )
    signal: dict[str, Any] | None = inp.get("signal") or None

    # Capture the current session_id from the turn context.
    # floating_session_id_var is set by handle_turn in chat.py BEFORE the tool
    # is invoked, so it is always available here for Slack-originated turns.
    from artemis.floating_artemis.context import floating_session_id_var

    session_id: str | None = floating_session_id_var.get()

    _logger.info(
        "dispatch_research: dispatching async background task for district_key=%r "
        "signal_id=%r session_id=%r",
        district_key,
        triggering_signal_id,
        session_id,
    )

    # ── Resolve channel_id + team_id now (in-turn, before going async) ────────
    channel_id, team_id = await _resolve_channel_and_team(session_id)

    if not channel_id:
        _logger.warning(
            "dispatch_research: no channel_id resolved for session_id=%r — "
            "cannot post Argus findings back to Slack",
            session_id,
        )
        return json.dumps({"status": "dispatched", "district": district_key, "warning": "no_channel_resolved"})

    # ── Persist a pending row BEFORE firing the task ───────────────────────────
    request_id = await _insert_pending_request(
        district_key=district_key,
        channel_id=channel_id,
        team_id=team_id,
        signal=signal,
        triggering_signal_id=triggering_signal_id,
    )

    # Fire the background task (GC-guarded)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return "Error: no running event loop (dispatch_research requires async context)"

    task = loop.create_task(
        _safe_research_and_post(
            request_id=request_id,
            channel_id=channel_id,
            team_id=team_id,
            district_key=district_key,
            triggering_signal_id=triggering_signal_id,
            signal=signal,
        ),
        name=f"argus_bg_{district_key}",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    # Return immediately so Callie acknowledges in her own voice
    return json.dumps({"status": "dispatched", "district": district_key})


# ── Persistence helpers ────────────────────────────────────────────────────────


async def _resolve_channel_and_team(session_id: str | None) -> tuple[str | None, str]:
    """Resolve channel_id and team_id from a Slack session_id.

    Returns (channel_id, team_id). channel_id may be None if resolution fails.
    This runs in-turn (before the background task) so the resolved values are
    captured in the persistent row and survive across process restarts.
    """
    if not session_id:
        return None, ""

    try:
        from artemis.floating_artemis import repository as fa_repo
        from artemis.floating_artemis.session_scope import _session_channel_id

        async with _db.SessionLocal() as _sess:
            try:
                row = await fa_repo.get_session_by_id(_sess, session_id)
                metadata: dict[str, Any] = (
                    row.metadata_ if isinstance(row.metadata_, dict) else {}
                )
            except Exception:
                metadata = {}

        channel_id = _session_channel_id(session_id, metadata)
        team_id = str(metadata.get("team_id") or "")
        return channel_id, team_id
    except Exception:
        _logger.warning(
            "dispatch_research: could not resolve channel/team from session_id=%r",
            session_id,
            exc_info=True,
        )
        return None, ""


async def _insert_pending_request(
    *,
    district_key: str,
    channel_id: str,
    team_id: str,
    signal: dict[str, Any] | None,
    triggering_signal_id: str | None,
) -> int | None:
    """Insert a pending row into argus_research_requests; return the row id."""
    try:
        async with _db.SessionLocal() as session:
            row = ArgusResearchRequest(
                district_key=district_key,
                channel_id=channel_id,
                team_id=team_id,
                signal=signal,
                triggering_signal_id=triggering_signal_id,
                status="pending",
                attempts=0,
            )
            session.add(row)
            await session.flush()
            row_id: int = row.id
            await session.commit()
        _logger.info(
            "dispatch_research: persisted pending request id=%s for district_key=%r",
            row_id,
            district_key,
        )
        return row_id
    except Exception:
        _logger.warning(
            "dispatch_research: failed to persist pending request for district_key=%r — "
            "proceeding without persistence (restart recovery will not apply)",
            district_key,
            exc_info=True,
        )
        return None


async def _mark_request_done(request_id: int | None) -> None:
    """Mark a request row as done with completed_at=now."""
    if request_id is None:
        return
    try:
        from datetime import UTC, datetime

        async with _db.SessionLocal() as session:
            row = await session.get(ArgusResearchRequest, request_id)
            if row is not None:
                row.status = "done"
                row.completed_at = datetime.now(UTC)
                await session.commit()
    except Exception:
        _logger.warning(
            "dispatch_research: failed to mark request id=%s done",
            request_id,
            exc_info=True,
        )


async def _mark_request_failed(
    request_id: int | None,
    *,
    error: str,
    channel_id: str,
    team_id: str,
    district_key: str,
) -> bool:
    """Increment attempts; if >= _MAX_ATTEMPTS mark failed and return True (should post fallback)."""
    if request_id is None:
        return False
    try:
        async with _db.SessionLocal() as session:
            row = await session.get(ArgusResearchRequest, request_id)
            if row is None:
                return False
            row.attempts = (row.attempts or 0) + 1
            row.error = error[:2000] if error else None
            if row.attempts >= _MAX_ATTEMPTS:
                row.status = "failed"
                await session.commit()
                return True  # caller should post fallback
            await session.commit()
            return False
    except Exception:
        _logger.warning(
            "dispatch_research: failed to update request id=%s on error",
            request_id,
            exc_info=True,
        )
        return False


# ── Background task ────────────────────────────────────────────────────────────


async def _safe_research_and_post(
    *,
    request_id: int | None,
    channel_id: str,
    team_id: str,
    district_key: str,
    triggering_signal_id: str | None,
    signal: dict[str, Any] | None,
) -> None:
    """Run research + Callie-voiced summary + Slack post. Swallows all exceptions."""
    try:
        await _research_and_post(
            request_id=request_id,
            channel_id=channel_id,
            team_id=team_id,
            district_key=district_key,
            triggering_signal_id=triggering_signal_id,
            signal=signal,
        )
    except Exception as exc:
        _logger.warning(
            "dispatch_research: background task failed for district_key=%r "
            "request_id=%s channel_id=%r (non-fatal)",
            district_key,
            request_id,
            channel_id,
            exc_info=True,
        )
        should_post_fallback = await _mark_request_failed(
            request_id,
            error=str(exc),
            channel_id=channel_id,
            team_id=team_id,
            district_key=district_key,
        )
        if should_post_fallback:
            _logger.warning(
                "dispatch_research: request_id=%s reached max attempts (%d) for district_key=%r — "
                "posting fallback to channel_id=%r",
                request_id,
                _MAX_ATTEMPTS,
                district_key,
                channel_id,
            )
            await _post_fallback(
                channel_id=channel_id,
                team_id=team_id,
                district_key=district_key,
            )


async def _research_and_post(
    *,
    request_id: int | None,
    channel_id: str,
    team_id: str,
    district_key: str,
    triggering_signal_id: str | None,
    signal: dict[str, Any] | None,
) -> None:
    """Core background logic: research → LLM summary → Slack post → mark done."""
    # ── 1. Optionally fetch signal row if only signal_id provided ─────────────
    if signal is None and triggering_signal_id is not None:
        try:
            from artemis.marketing import repository as _repo

            async with _db.SessionLocal() as _sess:
                sig_row = await _repo.get_signal(_sess, int(triggering_signal_id))
            signal = {
                "headline": sig_row.headline or "",
                "state": sig_row.state or "",
                "district_id": sig_row.district_id or "",
                "source_url": sig_row.source_url or "",
            }
        except Exception as exc:
            _logger.warning(
                "dispatch_research: could not fetch signal_id=%s -- %s (continuing without signal context)",
                triggering_signal_id,
                exc,
            )

    # ── 2. Run research_district (background DB session) ──────────────────────
    _logger.info(
        "dispatch_research (bg): starting research for district_key=%r channel_id=%r request_id=%s",
        district_key,
        channel_id,
        request_id,
    )

    from artemis.argus.flow import research_district

    async with _db.SessionLocal() as session:
        summary = await research_district(
            session,
            district_key=district_key,
            signal=signal,
            triggering_signal_id=triggering_signal_id,
        )
        await session.commit()

    # ── 3. Produce a Callie-voiced summary via LLM ────────────────────────────
    callie_post = await _callie_summarize(district_key=district_key, summary=summary)

    # Apply Slack formatting + linting
    from artemis.writing_rules import lint_agent_text, md_to_mrkdwn

    formatted_text = md_to_mrkdwn(lint_agent_text(callie_post))
    if not formatted_text.strip():
        _logger.warning(
            "dispatch_research: formatted Callie post is empty for district_key=%r; skipping Slack post",
            district_key,
        )
        await _mark_request_done(request_id)
        return

    # ── 4. Post back to channel as Callie ─────────────────────────────────────
    await _post_as_callie(
        channel_id=channel_id,
        team_id=team_id,
        text=formatted_text,
    )

    _logger.info(
        "dispatch_research (bg): posted Argus findings to channel_id=%r for district_key=%r request_id=%s",
        channel_id,
        district_key,
        request_id,
    )

    # ── 5. Mark request done ──────────────────────────────────────────────────
    await _mark_request_done(request_id)


# ── Startup recovery ───────────────────────────────────────────────────────────


async def recover_pending_requests() -> None:
    """Re-fire any pending Argus research requests orphaned by a previous process restart.

    Called once at app startup (from the FastAPI lifespan hook in main.py).
    Queries ``status='pending' AND attempts < _MAX_ATTEMPTS`` and schedules a
    background task for each row. Non-blocking — does not delay startup.

    These rows are DEFINITIONALLY orphaned: any task that was running died with the
    previous process, so they will never complete on their own. We re-fire them
    exactly as if dispatch_research had just been called.
    """
    try:
        async with _db.SessionLocal() as session:
            result = await session.execute(
                select(ArgusResearchRequest).where(
                    ArgusResearchRequest.status == "pending",
                    ArgusResearchRequest.attempts < _MAX_ATTEMPTS,
                )
            )
            rows = result.scalars().all()

        if not rows:
            _logger.info("dispatch_research startup_recovery: no orphaned pending requests")
            return

        _logger.info(
            "dispatch_research startup_recovery: found %d orphaned pending request(s) — re-firing",
            len(rows),
        )

        loop = asyncio.get_running_loop()
        for row in rows:
            task = loop.create_task(
                _safe_research_and_post(
                    request_id=row.id,
                    channel_id=row.channel_id,
                    team_id=row.team_id or "",
                    district_key=row.district_key,
                    triggering_signal_id=row.triggering_signal_id,
                    signal=row.signal,
                ),
                name=f"argus_recover_{row.district_key}_{row.id}",
            )
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_TASKS.discard)
            _logger.info(
                "dispatch_research startup_recovery: re-fired request_id=%s district_key=%r",
                row.id,
                row.district_key,
            )
    except Exception:
        _logger.warning(
            "dispatch_research startup_recovery: failed to query/re-fire pending requests — "
            "startup continues normally",
            exc_info=True,
        )


# ── Callie summarize + post helpers ───────────────────────────────────────────


async def _callie_summarize(
    *,
    district_key: str,
    summary: dict[str, Any],
) -> str:
    """Produce a Callie-voiced summary of the Argus dossier via an LLM pass.

    Uses complete_with_fallback (codex primary → claude-code fallback) so it
    works on the Claude Code subscription surface. Falls back to a structured
    plain-text summary on any LLM error.
    """
    from artemis.floating_artemis.personality import CALLIE_PERSONA_CORE

    new_findings: int = summary.get("new_findings", 0)
    gap_dims: list[str] = summary.get("gap_dimensions", [])
    existing_dims: list[str] = summary.get("existing_dimensions", [])
    angle: str | None = summary.get("recommended_angle")

    is_thin = new_findings == 0 and not angle

    # Build a dossier brief for the LLM
    dossier_lines: list[str] = [
        f"District: {district_key}",
        f"New findings from Argus: {new_findings}",
    ]
    if gap_dims:
        dossier_lines.append(f"Dimensions researched: {', '.join(gap_dims)}")
    if existing_dims:
        dossier_lines.append(f"Previously known dimensions: {', '.join(existing_dims)}")
    if angle:
        dossier_lines.append(f"Recommended outreach angle: {angle}")
    if is_thin:
        dossier_lines.append("(Argus found no new material on this district.)")

    dossier_text = "\n".join(dossier_lines)

    if is_thin:
        summary_instruction = (
            "Argus came back light on this district. Write a brief, honest note to Jon "
            "in Callie's voice: acknowledge what Argus looked for, note the gap, and "
            "suggest a next step (e.g. 'we can revisit when a signal surfaces'). "
            "Keep it to 2-3 sentences. Credit Argus by name."
        )
    else:
        summary_instruction = (
            "Write a Slack message in Callie's voice summarising Argus's findings for this district. "
            "Lead with the single most critical data point. Then: what we know, the recommended "
            "angle, and any open flags. Credit Argus by name. Be concrete and direct. "
            "No bullet soup. Use bold labels sparingly. No em or en dashes. No emojis. "
            "2-4 short paragraphs max."
        )

    prompt = (
        f"Argus has finished researching district {district_key!r}. "
        f"Here is the research dossier summary:\n\n{dossier_text}\n\n"
        f"{summary_instruction}"
    )

    try:
        from artemis.agent.client import CompletionRequest
        from artemis.agent.types import Message, TextBlock
        from artemis.providers.fallback import complete_with_fallback

        req = CompletionRequest(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=CALLIE_PERSONA_CORE,
            model=None,  # let each adapter use its default
            max_tokens=600,
            cache_system=False,
            cache_tools=False,
        )
        # Voice-critical: this is Callie relaying Argus's findings to Jon, so it
        # runs on claude-code (her voice) — NOT codex. claude-code is free
        # (subscription), so there's no cost reason to use codex here.
        resp = await complete_with_fallback(
            req,
            primary="claude-code",
            fallback="claude-code",
            feature_tag="argus_callie_summary",
        )
        text = ""
        for block in resp.message.content:
            if hasattr(block, "text"):
                text += block.text
        text = text.strip()
        if text:
            return text
    except Exception:
        _logger.warning(
            "dispatch_research: Callie LLM summary failed for district_key=%r — using plain fallback",
            district_key,
            exc_info=True,
        )

    # Plain fallback (no LLM)
    if is_thin:
        return (
            f"Argus came back light on {district_key} — no new material surfaced this pass. "
            "We can revisit when a stronger signal comes through."
        )

    lines = [f"Argus is back with findings on {district_key}."]
    if angle:
        lines.append(f"*Recommended angle:* {angle}")
    if gap_dims:
        lines.append(f"*Dimensions covered:* {', '.join(gap_dims)}")
    lines.append(
        "Findings are in the district drawer (workspace:marketing scope). Source: Argus."
    )
    return "\n\n".join(lines)


async def _post_as_callie(
    *,
    channel_id: str,
    team_id: str,
    text: str,
) -> None:
    """Post a message to Slack as Callie.

    Reuses ``_resolve_agent_slack_config`` from integrations_slack_events — the
    same resolver that handles all Callie inbound events — to fetch Callie's
    access_token from the Integration row in the DB. Then uses SlackClient to
    post.
    """
    from artemis.integrations.slack.client import SlackClient
    from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

    async with _db.SessionLocal() as session:
        agent_cfg = await _resolve_agent_slack_config(
            session,
            agent_id="callie",
            team_id=team_id or None,
        )

    if not agent_cfg.access_token:
        _logger.warning(
            "dispatch_research: no Slack access token configured for agent=callie; "
            "cannot post Argus findings to channel_id=%r",
            channel_id,
        )
        return

    client = SlackClient(token=agent_cfg.access_token)
    await client.post_message(channel=channel_id, text=text)


async def _post_fallback(
    *,
    channel_id: str,
    team_id: str,
    district_key: str,
) -> None:
    """Post a fallback message when a request has exhausted all retry attempts."""
    try:
        await _post_as_callie(
            channel_id=channel_id,
            team_id=team_id,
            text=(
                f"Argus couldn't complete the dig on {district_key} after {_MAX_ATTEMPTS} attempts "
                "— flagging it. I'll retry when you ask again."
            ),
        )
    except Exception:
        _logger.warning(
            "dispatch_research: _post_fallback itself failed for district_key=%r channel_id=%r",
            district_key,
            channel_id,
            exc_info=True,
        )


# ── Registry helper ────────────────────────────────────────────────────────────


def register_argus_tools(registry: AuthorizedToolRegistry) -> None:
    """Register Argus tools into the provided registry.

    Called only when agent_id == 'callie' (enforced in tool_registry.py).
    Layer 1: Callie calls this without confirmation -- the tool fires a background
    task and returns immediately. All writes stay within the workspace:marketing
    scope she already has full access to.
    """
    registry.register(DISPATCH_RESEARCH, _dispatch_research, layer=1)
