"""Brief tools for the qualifier brief_composer and content brief_assembler.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.

Tools in this module:
  signal_briefs.write              — brief_composer's Gate-1-readable signal brief
                                      (brief_composer only).
  signal_briefs.get_approval_history — prior approval decisions for similar briefs.
  campaign_brief.read              — read the most recent immutable campaign brief
                                      for a candidate (companion to the existing
                                      campaign_brief.write tool).

Brief-table reconciliation (the one real CC4 unknown — decided by reading source,
not guessing):

  * Gate 1 is a ``human_gate`` node (approval_kind="signal_brief"). Its approval-card
    content is built by ``_build_pipe4_context()`` in
    ``artemis/pipelines/node_executors/human_gate_executor.py`` — which reads from the
    pipeline run's ``node_states`` (the agent's returned output), NOT from any DB
    table. There is NO ``signal_briefs`` table in the schema.
  * ``campaign_briefs`` (the only brief table) has a NOT-NULL ``candidate_id`` FK to
    ``campaign_candidates``. At Gate-1 time the signal has not yet been promoted to a
    candidate (promotion happens after Gate-1 approval), so ``campaign_briefs`` cannot
    hold the brief_composer's pre-Gate-1 brief.
  * Therefore ``signal_briefs.write`` persists the brief onto the SIGNAL it summarizes,
    in ``signal_queue.qualification_json["brief"]`` (existing JSONB column, signal-keyed,
    FK-free — no CC3-class FK-target registration risk). It populates exactly the
    approval-card preview fields the gate reads: ``preview``, ``reason_codes``,
    ``evidence_quote``, ``districts``, plus a free-form ``body``. The DB write is the
    lossless/auditable record; the gate card itself renders from node_states.
  * ``campaign_brief.write`` (existing P3 tool) and ``campaign_brief.read`` (here) operate
    on the DOWNSTREAM immutable ``campaign_briefs`` table, keyed by candidate — a
    different table, post-Gate-1. They do not overlap with signal_briefs.*.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import Approval, CampaignBrief, SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_BRIEF_COMPOSER_AGENT = "marketing.qualifier.brief_composer"
_MARKETING_PREFIX = "marketing."


# ── signal_briefs.write ─────────────────────────────────────────────────────────

_WRITE_DEF = Tool(
    name="signal_briefs.write",
    description=(
        "Write the Gate-1 signal brief for a qualified signal. The brief is persisted "
        "onto the signal (signal_queue.qualification_json.brief) and supplies the fields "
        "the Gate 1 approval card renders: a short preview, the evidence quote, reason "
        "codes, and the brief body. Only the brief_composer agent may call this. Returns "
        "the signal ID on success, or an error string."
    ),
    input_schema={
        "type": "object",
        "required": ["signalId", "preview", "body"],
        "properties": {
            "signalId": {
                "type": "integer",
                "description": "The signal_queue.id this brief summarizes.",
            },
            "preview": {
                "type": "string",
                "description": "One-line preview shown on the Gate 1 approval card.",
            },
            "body": {
                "type": "string",
                "description": "The full brief body (markdown ok) for the decision view.",
            },
            "evidenceQuote": {
                "type": "string",
                "description": "Optional verbatim evidence snippet for the card.",
            },
            "recommendedFamilies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional campaign families this signal is routed to.",
            },
        },
    },
)


def _write_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if ctx.agent_id != _BRIEF_COMPOSER_AGENT:
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot call signal_briefs.write"
        signal_id = arguments.get("signalId")
        preview = arguments.get("preview")
        body = arguments.get("body")
        if not isinstance(signal_id, int):
            return "VALIDATION_ERROR: 'signalId' is required and must be an integer"
        if not isinstance(preview, str) or not preview:
            return "VALIDATION_ERROR: 'preview' is required and must be a non-empty string"
        if not isinstance(body, str) or not body:
            return "VALIDATION_ERROR: 'body' is required and must be a non-empty string"

        row = await ctx.session.get(SignalQueue, signal_id)
        if row is None:
            return f"NOT_FOUND: no signal with id={signal_id}"

        recommended = arguments.get("recommendedFamilies")
        recommended_families: list[str] = []
        if isinstance(recommended, list):
            recommended_families = [str(f) for f in recommended if isinstance(f, str)]

        brief_payload: dict[str, Any] = {
            "preview": preview,
            "body": body,
            "evidence_quote": (
                arguments.get("evidenceQuote")
                if isinstance(arguments.get("evidenceQuote"), str)
                else None
            ),
            "reason_codes": row.reason_codes or [],
            "districts": [row.district_id] if row.district_id else [],
            "recommended_families": recommended_families,
            "composed_by": ctx.agent_id,
            "agent_run_id": ctx.agent_run_id,
            "composed_at": datetime.now().astimezone().isoformat(),
        }

        # Merge into qualification_json without clobbering qualifier scoring data.
        existing = row.qualification_json
        merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        merged["brief"] = brief_payload
        row.qualification_json = merged

        await ctx.session.flush()
        logger.info(
            "signal_briefs.write: agent=%s signal_id=%s (brief persisted to qualification_json)",
            ctx.agent_id,
            signal_id,
        )
        return json.dumps({"signal_id": signal_id, "status": "written"})

    return (_WRITE_DEF, _impl)


register_tool("signal_briefs.write", _write_factory)


# ── signal_briefs.get_approval_history ──────────────────────────────────────────

_HISTORY_DEF = Tool(
    name="signal_briefs.get_approval_history",
    description=(
        "Return prior approval decisions, optionally filtered by approval kind "
        "(default 'signal_brief'). Helps the brief composer/ruleset manager judge how "
        "similar briefs were decided. Returns a JSON list of approval rows (newest "
        "first). Any marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "description": "Approval kind to filter by (default 'signal_brief').",
            },
            "status": {
                "type": "string",
                "description": "Optional status filter (pending | approved | rejected).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 50, max 200).",
            },
        },
    },
)


def _history_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read approval history"
        kind = arguments.get("kind")
        kind = kind if isinstance(kind, str) and kind else "signal_brief"
        raw_limit = arguments.get("limit", 50)
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 200 else 50
        status = arguments.get("status")

        stmt = select(Approval).where(Approval.kind == kind)
        if isinstance(status, str) and status:
            stmt = stmt.where(Approval.status == status)
        stmt = stmt.order_by(Approval.created_at.desc()).limit(limit)
        rows = (await ctx.session.execute(stmt)).scalars().all()

        return json.dumps(
            {
                "kind": kind,
                "total": len(rows),
                "approvals": [
                    {
                        "id": a.id,
                        "subject_id": a.subject_id,
                        "status": a.status,
                        "decided_by": a.decided_by,
                        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
                        "decision_payload": a.decision_payload,
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                    }
                    for a in rows
                ],
            }
        )

    return (_HISTORY_DEF, _impl)


register_tool("signal_briefs.get_approval_history", _history_factory)


# ── campaign_brief.read ─────────────────────────────────────────────────────────

_READ_DEF = Tool(
    name="campaign_brief.read",
    description=(
        "Read the most recent immutable campaign brief for a candidate from "
        "campaign_briefs. Companion to campaign_brief.write. Returns the brief row as a "
        "JSON object, or NOT_FOUND if the candidate has no brief yet. Any marketing "
        "agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["candidateId"],
        "properties": {
            "candidateId": {
                "type": "integer",
                "description": "The campaign_candidates.id whose latest brief to read.",
            },
        },
    },
)


def _read_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read campaign briefs"
        candidate_id = arguments.get("candidateId")
        if not isinstance(candidate_id, int):
            return "VALIDATION_ERROR: 'candidateId' is required and must be an integer"
        stmt = (
            select(CampaignBrief)
            .where(CampaignBrief.candidate_id == candidate_id)
            .order_by(CampaignBrief.generated_at.desc())
            .limit(1)
        )
        row = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return f"NOT_FOUND: no campaign brief for candidate id={candidate_id}"
        return json.dumps(
            {
                "id": row.id,
                "candidate_id": row.candidate_id,
                "content": row.content,
                "generated_by": row.generated_by,
                "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            }
        )

    return (_READ_DEF, _impl)


register_tool("campaign_brief.read", _read_factory)
