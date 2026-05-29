"""Qualifier-facing read + transition tools over ``signal_queue``.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.

Tools in this module (all dotted names the qualifier agents declare):
  signal_queue.get                         — read one signal as JSON (any marketing agent)
  signal_queue.update_status               — transition a signal via the M3 state
                                             machine (qualifier agents only). THE tool
                                             that unblocks pending_qualification → qualified.
  signal_queue.find_by_district_and_code   — filter signals by district + reason code
  signal_queue.find_recent_qualification_results
                                           — recent signals + qualification status (hit-rate input)

DB writes use ``ctx.session`` + ``await ctx.session.flush()``; the MCP server owns the
commit boundary, so we never commit here. Permission / validation failures RETURN a
string, never raise.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import SignalQueue
from artemis.marketing.state_machine import IllegalTransition, transition
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_MARKETING_PREFIX = "marketing."
_QUALIFIER_PREFIX = "marketing.qualifier."


def _serialize_signal(row: SignalQueue) -> dict[str, Any]:
    """Stable JSON-able view of a signal row."""

    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_url": row.source_url,
        "source_id": row.source_id,
        "pipeline_run_id": row.pipeline_run_id,
        "headline": row.headline,
        "summary": row.summary,
        "campaign_family": row.campaign_family,
        "urgency_tier": row.urgency_tier,
        "discovered_by": row.discovered_by,
        "district_id": row.district_id,
        "state": row.state,
        "reason_codes": row.reason_codes,
        "provenance": row.provenance,
        "qualification_json": row.qualification_json,
        "signal_status": row.signal_status,
        "rejected_reason": row.rejected_reason,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# ── signal_queue.get ──────────────────────────────────────────────────────────

_GET_DEF = Tool(
    name="signal_queue.get",
    description=(
        "Read a single signal from the qualification queue by ID. Returns the full "
        "signal row as a JSON object, or NOT_FOUND if no such signal exists. Any "
        "marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["signalId"],
        "properties": {
            "signalId": {
                "type": "integer",
                "description": "The signal_queue.id to read.",
            },
        },
    },
)


def _get_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read signals"
        signal_id = arguments.get("signalId")
        if not isinstance(signal_id, int):
            return "VALIDATION_ERROR: 'signalId' is required and must be an integer"
        row = await ctx.session.get(SignalQueue, signal_id)
        if row is None:
            return f"NOT_FOUND: no signal with id={signal_id}"
        return json.dumps(_serialize_signal(row))

    return (_GET_DEF, _impl)


register_tool("signal_queue.get", _get_factory)


# ── signal_queue.update_status ──────────────────────────────────────────────────

_UPDATE_STATUS_DEF = Tool(
    name="signal_queue.update_status",
    description=(
        "Transition a signal to a new lifecycle status (e.g. qualified, "
        "rejected_hard_filter, suppressed_stale). The transition is validated against "
        "the M3 state machine and an audit row is written atomically. Only qualifier "
        "agents may call this. Returns the new status on success, or an error string "
        "(ILLEGAL_TRANSITION / NOT_FOUND / PERMISSION_DENIED / VALIDATION_ERROR)."
    ),
    input_schema={
        "type": "object",
        "required": ["signalId", "newStatus"],
        "properties": {
            "signalId": {
                "type": "integer",
                "description": "The signal_queue.id to transition.",
            },
            "newStatus": {
                "type": "string",
                # H1: enum declared so validation errors enumerate valid alternatives.
                # Canonical source of truth: SignalState in artemis/marketing/state_machine.py.
                "enum": [
                    "pending_qualification",
                    "qualified",
                    "rejected_hard_filter",
                    "suppressed_stale",
                    "approved",
                    "rejected_at_gate_1",
                    "snoozed",
                    "archived",
                ],
                "description": (
                    "Target signal status. Legal transitions from pending_qualification: "
                    "qualified, rejected_hard_filter, suppressed_stale. "
                    "From qualified: approved, rejected_at_gate_1, snoozed, archived."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Optional human-readable reason recorded on the audit row.",
            },
        },
    },
)


def _update_status_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_QUALIFIER_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot transition signal status"
        signal_id = arguments.get("signalId")
        new_status = arguments.get("newStatus")
        if not isinstance(signal_id, int):
            return "VALIDATION_ERROR: 'signalId' is required and must be an integer"
        if not isinstance(new_status, str) or not new_status:
            return "VALIDATION_ERROR: 'newStatus' is required and must be a non-empty string"
        reason = arguments.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)

        try:
            entity = await transition(
                ctx.session,
                "signal",
                signal_id,
                new_status,
                reason=reason,
                actor=ctx.agent_id,
            )
        except ValueError as exc:
            # entity_id not found
            return f"NOT_FOUND: {exc}"
        except IllegalTransition as exc:
            return f"ILLEGAL_TRANSITION: {exc}"

        logger.info(
            "signal_queue.update_status: agent=%s signal_id=%s -> %s",
            ctx.agent_id,
            signal_id,
            new_status,
        )
        return json.dumps({"signal_id": signal_id, "signal_status": entity.signal_status})

    return (_UPDATE_STATUS_DEF, _impl)


register_tool("signal_queue.update_status", _update_status_factory)


# ── signal_queue.find_by_district_and_code ──────────────────────────────────────

_FIND_BY_DC_DEF = Tool(
    name="signal_queue.find_by_district_and_code",
    description=(
        "Find signals matching a district and a reason code. Returns a JSON list of "
        "matching signal rows (newest first). Used by the brief composer to cross-link "
        "related signals for the same district. Any marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["districtId", "reasonCode"],
        "properties": {
            "districtId": {
                "type": "string",
                "description": "The district_id to match.",
            },
            "reasonCode": {
                "type": "string",
                "description": "A reason code that must be present in the signal's reason_codes.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 50, max 200).",
            },
        },
    },
)


def _matches_reason_code(reason_codes: Any, code: str) -> bool:
    """Reason codes are stored as a JSONB list of {code: ...} dicts or bare strings."""
    if not isinstance(reason_codes, list):
        return False
    for item in reason_codes:
        if isinstance(item, str) and item == code:
            return True
        if isinstance(item, dict) and str(item.get("code", "")) == code:
            return True
    return False


def _find_by_dc_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read signals"
        district_id = arguments.get("districtId")
        reason_code = arguments.get("reasonCode")
        if not isinstance(district_id, str) or not district_id:
            return "VALIDATION_ERROR: 'districtId' is required and must be a non-empty string"
        if not isinstance(reason_code, str) or not reason_code:
            return "VALIDATION_ERROR: 'reasonCode' is required and must be a non-empty string"
        raw_limit = arguments.get("limit", 50)
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 200 else 50

        # Filter by district in SQL; reason-code membership is checked in Python because
        # the JSONB shape (list of dicts or strings) is awkward to query portably.
        stmt = (
            select(SignalQueue)
            .where(SignalQueue.district_id == district_id)
            .order_by(SignalQueue.created_at.desc())
            .limit(limit * 4)
        )
        rows = (await ctx.session.execute(stmt)).scalars().all()
        matched = [
            _serialize_signal(row)
            for row in rows
            if _matches_reason_code(row.reason_codes, reason_code)
        ][:limit]
        return json.dumps(
            {"district_id": district_id, "reason_code": reason_code, "signals": matched}
        )

    return (_FIND_BY_DC_DEF, _impl)


register_tool("signal_queue.find_by_district_and_code", _find_by_dc_factory)


# ── signal_queue.find_recent_qualification_results ──────────────────────────────

_FIND_RECENT_DEF = Tool(
    name="signal_queue.find_recent_qualification_results",
    description=(
        "Return recent signals together with their qualification status, for ruleset "
        "hit-rate analysis. Optionally filter by campaign family. Returns a JSON object "
        "with a summary count by status and the matching signal rows. Any marketing "
        "agent may call this."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "campaignFamily": {
                "type": "string",
                "description": "Optional campaign family to filter by.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 100, max 500).",
            },
        },
    },
)


def _find_recent_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read signals"
        raw_limit = arguments.get("limit", 100)
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 500 else 100
        family = arguments.get("campaignFamily")

        stmt = select(SignalQueue).order_by(SignalQueue.created_at.desc()).limit(limit)
        if isinstance(family, str) and family:
            stmt = (
                select(SignalQueue)
                .where(SignalQueue.campaign_family == family)
                .order_by(SignalQueue.created_at.desc())
                .limit(limit)
            )
        rows = (await ctx.session.execute(stmt)).scalars().all()

        status_counts: dict[str, int] = {}
        for row in rows:
            status_counts[row.signal_status] = status_counts.get(row.signal_status, 0) + 1

        return json.dumps(
            {
                "total": len(rows),
                "status_counts": status_counts,
                "signals": [
                    {
                        "id": row.id,
                        "campaign_family": row.campaign_family,
                        "signal_status": row.signal_status,
                        "urgency_tier": row.urgency_tier,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ],
            }
        )

    return (_FIND_RECENT_DEF, _impl)


register_tool("signal_queue.find_recent_qualification_results", _find_recent_factory)
