"""Screen-Time & AI-policy tools for Callie -- gated to agent_id='callie' only.

ON DEMAND, not auto-push
========================
These tools exist so Callie can answer when a teammate ASKS in Slack ("what's
the latest on screen-time / AI policy?"). There is no scheduler here and this
file never posts to Slack on its own -- Callie composes her own reply from the
tool's return value, in the SAME turn, exactly like her other read tools
(``list_signals``, ``get_signal``). Compare with
``artemis/screentime/reporting.py`` (Brief 2), which is a SEPARATE, unrelated
auto-digest that posts to #policy-watch on a cron / big-move trigger -- that
module is untouched by this one and stays dormant unless
``screentime_report_channel`` is explicitly configured.

Callie only
===========
Registered only when ``agent_id == "callie"`` in
``artemis/floating_artemis/tool_registry.py`` -- no other agent sees these
tools. Reachable by ANY Slack user who can already talk to Callie (owner or
not -- e.g. Amy the COO): there is no owner-gate on marketing-os tools on the
Slack inbound path, and these tools carry no owner check of their own.

Read-only + reaction learning
==============================
``get_screentime_report`` is pure read (layer 1, auto-invoke, no
confirmation). ``record_screentime_feedback`` is the one write (layer 2 --
an idempotent, low-risk side effect, same layer as ``qualify_signal``): it
teaches the reaction-learning loop via
``artemis.screentime.callie_report.record_feedback``, which itself reuses
``callie_push.record_signal_engagement`` -- no parallel learning system.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_logger = logging.getLogger(__name__)

_SURFACE = "[surface:marketing-os]"
_AGENT_GATE = "[agent:callie]"


# ── Tool definitions ──────────────────────────────────────────────────────────

GET_SCREENTIME_REPORT = Tool(
    name="get_screentime_report",
    description=(
        "Compose an ON-DEMAND overview of the Screen-Time Watch / AI-in-schools "
        "policy signals: a national summary (real-move counts by stance and "
        "status), notable state/district moves with sources, and the Amira "
        "carve-out angle where one exists. READ-ONLY -- never sends or posts "
        "anything; use this whenever a teammate (owner or not) asks for the "
        "screen-time / AI-policy update, in Slack or otherwise. Each notable "
        "move includes its signal id -- use that id if the teammate later "
        "reacts to a specific item (see record_screentime_feedback). "
        f"{_SURFACE} {_AGENT_GATE} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "notable_limit": {
                "type": "integer",
                "description": "Max notable moves to include (default 10).",
            },
        },
    },
)

RECORD_SCREENTIME_FEEDBACK = Tool(
    name="record_screentime_feedback",
    description=(
        "Record an EXPLICIT teammate reaction to one screen-time/AI-policy "
        "signal from a get_screentime_report answer, so future reports learn "
        "what this audience does and doesn't care about. "
        "Call this ONLY when the teammate explicitly says the item is not "
        "relevant AND gives a reason (e.g. 'that CA one isn't relevant, we "
        "already track that bill elsewhere') -- pass not_relevant=true and "
        "the reason. You may also call it when a teammate explicitly confirms "
        "an item IS useful/relevant -- pass not_relevant=false. "
        "Do NOT call this for silence, a topic change, or a vague reaction -- "
        "a silent ignore must never be recorded (mirrors Callie's marketing "
        "signal engagement rule exactly). A 'not relevant' with no reason is "
        "refused and nothing is recorded. "
        f"{_SURFACE} {_AGENT_GATE} [layer:2]"
    ),
    input_schema={
        "type": "object",
        "required": ["signal_id", "not_relevant"],
        "properties": {
            "signal_id": {
                "type": "integer",
                "description": "The screentime signal id from a prior get_screentime_report answer.",
            },
            "not_relevant": {
                "type": "boolean",
                "description": "true = teammate said this item is not relevant; false = teammate confirmed it IS relevant/useful.",
            },
            "reason": {
                "type": "string",
                "description": "Required when not_relevant=true -- the teammate's stated reason. Omit/blank is refused (nothing recorded).",
            },
        },
    },
)


# ── Tool implementations ─────────────────────────────────────────────────────


async def _get_screentime_report(inp: dict[str, Any]) -> str:
    try:
        limit_raw = inp.get("notable_limit")
        limit = int(limit_raw) if limit_raw is not None else 10
        limit = max(1, min(limit, 50))
    except (TypeError, ValueError):
        limit = 10

    try:
        import artemis.db as _db
        from artemis.screentime.callie_report import build_report, format_report_text

        async with _db.SessionLocal() as session:
            data = await build_report(session, notable_limit=limit)
        return format_report_text(data)
    except Exception as exc:
        _logger.warning("get_screentime_report failed", exc_info=True)
        return f"get_screentime_report failed: {exc}"


async def _record_screentime_feedback(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    if signal_id is None:
        return "Error: signal_id is required"
    not_relevant = bool(inp.get("not_relevant"))
    reason = inp.get("reason")

    try:
        import artemis.db as _db
        from artemis.screentime.callie_report import record_feedback

        async with _db.SessionLocal() as session:
            return await record_feedback(
                session,
                signal_id=int(signal_id),
                not_relevant=not_relevant,
                reason=str(reason) if reason is not None else None,
            )
    except Exception as exc:
        _logger.warning("record_screentime_feedback failed", exc_info=True)
        return f"record_screentime_feedback failed: {exc}"


# ── Registry helper ────────────────────────────────────────────────────────────


def register_screentime_report_tools(registry: AuthorizedToolRegistry) -> None:
    """Register the on-demand screen-time report tools into *registry*.

    Called only when agent_id == 'callie' (enforced in tool_registry.py).
    """
    registry.register(GET_SCREENTIME_REPORT, _get_screentime_report, layer=1)
    registry.register(RECORD_SCREENTIME_FEEDBACK, _record_screentime_feedback, layer=2)
