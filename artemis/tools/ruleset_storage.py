"""Ruleset storage tools for the qualifier ruleset_manager.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.

Tools:
  ruleset_storage.get_active        — active ruleset for a family
  ruleset_storage.get_version       — a specific (family, version_tag) ruleset
  ruleset_storage.write_new_version — append a new draft version (lossless; never mutates
                                      an existing published version)
  ruleset_storage.activate          — activate a version: archive the prior active row,
                                      flip the target to active (lossless — archived rows
                                      are kept, not deleted)
  ruleset_storage.get_hit_rate      — qualified / total ratio over recent signals for a
                                      family (documented zero/empty result when no data)

There is no ``campaign_ruleset_versions`` table — versioning lives entirely within the
``rulesets`` table (one row per (family, version_tag), UNIQUE-constrained). All write
operations are append-only / state-flip: a published version's logic columns are never
rewritten in place.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import Ruleset, SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_RULESET_MANAGER_AGENT = "marketing.qualifier.ruleset_manager"
_MARKETING_PREFIX = "marketing."


def _serialize_ruleset(row: Ruleset) -> dict[str, Any]:
    return {
        "id": row.id,
        "family": row.family,
        "version_tag": row.version_tag,
        "hard_filters": row.hard_filters,
        "weighted_signals": row.weighted_signals,
        "qualitative_rubrics": row.qualitative_rubrics,
        "state": row.state,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── ruleset_storage.get_active ──────────────────────────────────────────────────

_GET_ACTIVE_DEF = Tool(
    name="ruleset_storage.get_active",
    description=(
        "Return the active ruleset for a campaign family, or NOT_FOUND if no active "
        "ruleset exists. Any marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["family"],
        "properties": {
            "family": {"type": "string", "description": "Campaign family, e.g. 'obc'."},
        },
    },
)


def _get_active_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read rulesets"
        family = arguments.get("family")
        if not isinstance(family, str) or not family:
            return "VALIDATION_ERROR: 'family' is required and must be a non-empty string"
        stmt = (
            select(Ruleset)
            .where(Ruleset.family == family, Ruleset.state == "active")
            .order_by(Ruleset.created_at.desc())
            .limit(1)
        )
        row = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return f"NOT_FOUND: no active ruleset for family={family!r}"
        return json.dumps(_serialize_ruleset(row))

    return (_GET_ACTIVE_DEF, _impl)


register_tool("ruleset_storage.get_active", _get_active_factory)


# ── ruleset_storage.get_version ─────────────────────────────────────────────────

_GET_VERSION_DEF = Tool(
    name="ruleset_storage.get_version",
    description=(
        "Return a specific ruleset version by (family, versionTag), or NOT_FOUND. Any "
        "marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["family", "versionTag"],
        "properties": {
            "family": {"type": "string"},
            "versionTag": {"type": "string"},
        },
    },
)


def _get_version_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read rulesets"
        family = arguments.get("family")
        version_tag = arguments.get("versionTag")
        if not isinstance(family, str) or not family:
            return "VALIDATION_ERROR: 'family' is required and must be a non-empty string"
        if not isinstance(version_tag, str) or not version_tag:
            return "VALIDATION_ERROR: 'versionTag' is required and must be a non-empty string"
        stmt = select(Ruleset).where(Ruleset.family == family, Ruleset.version_tag == version_tag)
        row = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return f"NOT_FOUND: no ruleset family={family!r} version={version_tag!r}"
        return json.dumps(_serialize_ruleset(row))

    return (_GET_VERSION_DEF, _impl)


register_tool("ruleset_storage.get_version", _get_version_factory)


# ── ruleset_storage.write_new_version ───────────────────────────────────────────

_WRITE_NEW_DEF = Tool(
    name="ruleset_storage.write_new_version",
    description=(
        "Append a NEW draft ruleset version for a family. Lossless: this never mutates "
        "an existing version — it inserts a new (family, versionTag) row in 'draft' "
        "state. Use ruleset_storage.activate to publish it. Only the ruleset_manager "
        "agent may call this. Returns the new ruleset ID, or an error string."
    ),
    input_schema={
        "type": "object",
        "required": ["family", "versionTag"],
        "properties": {
            "family": {"type": "string"},
            "versionTag": {"type": "string", "description": "Unique tag within the family."},
            "hardFilters": {"type": "array", "items": {"type": "object"}},
            "weightedSignals": {"type": "array", "items": {"type": "object"}},
            "qualitativeRubrics": {"type": "array", "items": {"type": "object"}},
        },
    },
)


def _write_new_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if ctx.agent_id != _RULESET_MANAGER_AGENT:
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot write ruleset versions"
        family = arguments.get("family")
        version_tag = arguments.get("versionTag")
        if not isinstance(family, str) or not family:
            return "VALIDATION_ERROR: 'family' is required and must be a non-empty string"
        if not isinstance(version_tag, str) or not version_tag:
            return "VALIDATION_ERROR: 'versionTag' is required and must be a non-empty string"

        # Lossless guard: refuse to overwrite an existing version.
        existing = (
            await ctx.session.execute(
                select(Ruleset).where(Ruleset.family == family, Ruleset.version_tag == version_tag)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return (
                f"VALIDATION_ERROR: ruleset family={family!r} version={version_tag!r} "
                "already exists; pick a new versionTag (versions are append-only)"
            )

        def _list_arg(key: str) -> list[Any]:
            val = arguments.get(key)
            return val if isinstance(val, list) else []

        row = Ruleset(
            family=family,
            version_tag=version_tag,
            hard_filters=_list_arg("hardFilters"),
            weighted_signals=_list_arg("weightedSignals"),
            qualitative_rubrics=_list_arg("qualitativeRubrics"),
            state="draft",
        )
        ctx.session.add(row)
        await ctx.session.flush()
        logger.info(
            "ruleset_storage.write_new_version: agent=%s family=%s version=%s id=%s",
            ctx.agent_id,
            family,
            version_tag,
            row.id,
        )
        return json.dumps(
            {"ruleset_id": row.id, "family": family, "version_tag": version_tag, "state": "draft"}
        )

    return (_WRITE_NEW_DEF, _impl)


register_tool("ruleset_storage.write_new_version", _write_new_factory)


# ── ruleset_storage.activate ────────────────────────────────────────────────────

_ACTIVATE_DEF = Tool(
    name="ruleset_storage.activate",
    description=(
        "Activate a ruleset version for a family. The currently-active version (if any) "
        "is archived (state='archived', kept — never deleted) and the target version is "
        "set active. Only the ruleset_manager agent may call this. Returns the activated "
        "ruleset ID, or an error string."
    ),
    input_schema={
        "type": "object",
        "required": ["family", "versionTag"],
        "properties": {
            "family": {"type": "string"},
            "versionTag": {"type": "string"},
        },
    },
)


def _activate_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if ctx.agent_id != _RULESET_MANAGER_AGENT:
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot activate rulesets"
        family = arguments.get("family")
        version_tag = arguments.get("versionTag")
        if not isinstance(family, str) or not family:
            return "VALIDATION_ERROR: 'family' is required and must be a non-empty string"
        if not isinstance(version_tag, str) or not version_tag:
            return "VALIDATION_ERROR: 'versionTag' is required and must be a non-empty string"

        target = (
            await ctx.session.execute(
                select(Ruleset).where(Ruleset.family == family, Ruleset.version_tag == version_tag)
            )
        ).scalar_one_or_none()
        if target is None:
            return f"NOT_FOUND: no ruleset family={family!r} version={version_tag!r}"

        # Archive currently-active versions for this family (lossless state flip).
        current_active = (
            (
                await ctx.session.execute(
                    select(Ruleset).where(
                        Ruleset.family == family,
                        Ruleset.state == "active",
                        Ruleset.id != target.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in current_active:
            row.state = "archived"

        target.state = "active"
        await ctx.session.flush()
        logger.info(
            "ruleset_storage.activate: agent=%s family=%s version=%s archived=%d",
            ctx.agent_id,
            family,
            version_tag,
            len(current_active),
        )
        return json.dumps(
            {
                "ruleset_id": target.id,
                "family": family,
                "version_tag": version_tag,
                "state": "active",
                "archived_prior": len(current_active),
            }
        )

    return (_ACTIVATE_DEF, _impl)


register_tool("ruleset_storage.activate", _activate_factory)


# ── ruleset_storage.get_hit_rate ────────────────────────────────────────────────

_HIT_RATE_DEF = Tool(
    name="ruleset_storage.get_hit_rate",
    description=(
        "Compute the qualification hit-rate (qualified / total) over recent signals for "
        "a campaign family. Returns a JSON object with counts and the rate. If there is "
        "no signal data, returns a documented zero result (total=0, hit_rate=0.0). Any "
        "marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["family"],
        "properties": {
            "family": {"type": "string"},
            "limit": {
                "type": "integer",
                "description": "Max recent signals to consider (default 200, max 1000).",
            },
        },
    },
)


def _hit_rate_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot read hit rate"
        family = arguments.get("family")
        if not isinstance(family, str) or not family:
            return "VALIDATION_ERROR: 'family' is required and must be a non-empty string"
        raw_limit = arguments.get("limit", 200)
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 1000 else 200

        stmt = (
            select(SignalQueue.signal_status)
            .where(SignalQueue.campaign_family == family)
            .order_by(SignalQueue.created_at.desc())
            .limit(limit)
        )
        statuses = list((await ctx.session.execute(stmt)).scalars().all())
        total = len(statuses)
        # "qualified" and any post-qualification state count as a qualifier hit.
        hit_states = {"qualified", "approved", "rejected_at_gate_1", "snoozed", "archived"}
        qualified = sum(1 for s in statuses if s in hit_states)
        hit_rate = round(qualified / total, 4) if total else 0.0
        return json.dumps(
            {
                "family": family,
                "total": total,
                "qualified": qualified,
                "hit_rate": hit_rate,
                "note": "no signal data for this family" if total == 0 else None,
            }
        )

    return (_HIT_RATE_DEF, _impl)


register_tool("ruleset_storage.get_hit_rate", _hit_rate_factory)
