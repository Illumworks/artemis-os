"""Argus dispatch tool for Callie -- gated to agent_id='callie' only.

Callie owns this tool. No other agent sees it.

v1 (sync, retired): research_district ran in-turn; dossier returned directly.

v2 (async, current): dispatch_research fires a background task immediately and
returns a short ``{"status":"dispatched","district":<name>}`` payload so Callie
acknowledges in the same turn. The background task runs research_district with its
own DB session, produces a Callie-voiced summary, and posts it to the originating
channel via SlackClient.

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
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_logger = logging.getLogger(__name__)

_SURFACE = "[surface:marketing-os]"
_AGENT_GATE = "[agent:callie]"

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
    """Fire-and-acknowledge: schedule background research, return immediately.

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

    # Fire the background task (GC-guarded)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — shouldn't happen in production; fall back to sync error
        return "Error: no running event loop (dispatch_research requires async context)"

    task = loop.create_task(
        _safe_research_and_post(
            session_id=session_id,
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


# ── Background task ────────────────────────────────────────────────────────────


async def _safe_research_and_post(
    *,
    session_id: str | None,
    district_key: str,
    triggering_signal_id: str | None,
    signal: dict[str, Any] | None,
) -> None:
    """Run research + Callie-voiced summary + Slack post. Swallows all exceptions."""
    try:
        await _research_and_post(
            session_id=session_id,
            district_key=district_key,
            triggering_signal_id=triggering_signal_id,
            signal=signal,
        )
    except Exception:
        _logger.warning(
            "dispatch_research: background task failed for district_key=%r session_id=%r "
            "(non-fatal, no Slack post will be sent)",
            district_key,
            session_id,
            exc_info=True,
        )


async def _research_and_post(
    *,
    session_id: str | None,
    district_key: str,
    triggering_signal_id: str | None,
    signal: dict[str, Any] | None,
) -> None:
    """Core background logic: research → LLM summary → Slack post."""
    import artemis.db as _db

    # ── 1. Resolve channel_id and team_id from session context ────────────────
    # The session_id for Slack turns is ``slack-callie-{team_id}-{channel_id}-{bucket}``.
    # _session_channel_id reads metadata["channel_id"] first, then parses the id.
    # team_id comes from session metadata["team_id"].
    channel_id: str | None = None
    team_id: str = ""

    if session_id:
        try:
            import artemis.db as _db
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
        except Exception:
            _logger.warning(
                "dispatch_research: could not resolve channel/team from session_id=%r",
                session_id,
                exc_info=True,
            )

    if not channel_id:
        _logger.warning(
            "dispatch_research: no channel_id resolved for session_id=%r — "
            "cannot post Argus findings back to Slack",
            session_id,
        )
        return

    # ── 2. Optionally fetch signal row if only signal_id provided ─────────────
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

    # ── 3. Run research_district (background DB session) ──────────────────────
    _logger.info(
        "dispatch_research (bg): starting research for district_key=%r channel_id=%r",
        district_key,
        channel_id,
    )

    import artemis.db as _db
    from artemis.argus.flow import research_district

    async with _db.SessionLocal() as session:
        summary = await research_district(
            session,
            district_key=district_key,
            signal=signal,
            triggering_signal_id=triggering_signal_id,
        )
        await session.commit()

    # ── 4. Produce a Callie-voiced summary via LLM ────────────────────────────
    callie_post = await _callie_summarize(district_key=district_key, summary=summary)

    # Apply Slack formatting + linting
    from artemis.writing_rules import lint_agent_text, md_to_mrkdwn

    formatted_text = md_to_mrkdwn(lint_agent_text(callie_post))
    if not formatted_text.strip():
        _logger.warning(
            "dispatch_research: formatted Callie post is empty for district_key=%r; skipping Slack post",
            district_key,
        )
        return

    # ── 5. Post back to channel as Callie ─────────────────────────────────────
    await _post_as_callie(
        channel_id=channel_id,
        team_id=team_id,
        text=formatted_text,
    )

    _logger.info(
        "dispatch_research (bg): posted Argus findings to channel_id=%r for district_key=%r",
        channel_id,
        district_key,
    )


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
        resp = await complete_with_fallback(
            req,
            primary="codex",
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
    import artemis.db as _db
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


# ── Registry helper ────────────────────────────────────────────────────────────


def register_argus_tools(registry: AuthorizedToolRegistry) -> None:
    """Register Argus tools into the provided registry.

    Called only when agent_id == 'callie' (enforced in tool_registry.py).
    Layer 1: Callie calls this without confirmation -- the tool fires a background
    task and returns immediately. All writes stay within the workspace:marketing
    scope she already has full access to.
    """
    registry.register(DISPATCH_RESEARCH, _dispatch_research, layer=1)
